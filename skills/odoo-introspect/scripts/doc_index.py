"""
doc_index.py — Layer J local Odoo developer-doc index (offline TF-IDF).

Build a queryable index from a local checkout of odoo/documentation (CC-BY-SA-4.0)
and query it at zero network cost.  The built index lives OUTSIDE this repo at
~/.odoo-ai/docs-index/<version>/index.json — it is a CC-BY-SA-4.0 derived artifact
and must NEVER be committed to git.  Every result links to the canonical
https://www.odoo.com/documentation/ URL for required attribution.

Usage
-----
    # Build (one-time — sparse-clones odoo/documentation if --src is omitted):
    python3 doc_index.py build --version 18 [--src /path/to/odoo/documentation]

    # Query (fully offline after build):
    python3 doc_index.py query "ORM create method" --version 18

    # Via the odoo-ai CLI:
    odoo-ai docs-build --version 18 [--src <dir>]
    odoo-ai docs "<query>" --version 18 [--top N]

Result caveat: docs describe the API as DESIGNED; always existence-gate via
odoo-ai native-check / introspect to confirm what THIS instance actually has.
"""
import os
import re
import sys
import json
import argparse
import subprocess
import tempfile
from pathlib import Path

# Reuse TF-IDF + tokenize helpers from native_check (safe: run() is env-gated).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_check  # noqa: E402

_MIN_CHUNK_CHARS = 80  # discard sections shorter than this (prose threshold)


# ---------------------------------------------------------------------------
# Pure helpers — no Odoo, no network, fully unit-testable
# ---------------------------------------------------------------------------

def strip_images(text):
    """Drop RST image/figure directives and image-substitution definitions.

    Removes ``.. image::``, ``.. figure::``, and ``.. |name| image::`` blocks,
    including all option lines and figure captions, so the index stores prose only.
    Standalone text is left untouched.
    """
    _img = re.compile(r'[ \t]*\.\.\s+(?:image|figure|\|[^|]+\|\s+image)::')
    lines = text.split('\n')
    out, i = [], 0
    while i < len(lines):
        if _img.match(lines[i]):
            i += 1
            # skip all continuation lines: blank or indented
            while i < len(lines) and (
                lines[i].strip() == '' or lines[i][:1] in (' ', '\t')
            ):
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return '\n'.join(out)


def _slugify(s):
    """Lowercase + collapse non-alphanumeric runs to hyphens."""
    return re.sub(r'[^a-z0-9]+', '-', s.strip().lower()).strip('-')


def _norm_ver(version):
    """Normalize a bare integer version to X.0 (18 → 18.0; 18.0 stays 18.0)."""
    return f"{version}.0" if re.match(r'^\d+$', str(version)) else str(version)


def chunk_rst(text, rel_path):
    """Split a .rst file into section chunks (underline-style headings only).

    Underline heading: a non-blank, non-indented title line immediately followed
    by a line of one repeated punctuation character (``= - ~ ^ "`` etc.) at least
    as long as the title.  Images are stripped first.  Chunks shorter than
    ``_MIN_CHUNK_CHARS`` characters are skipped.

    Returns ``[{"rel_path", "heading", "text", "anchor"}, ...]``.
    """
    text = strip_images(text)
    lines = text.split('\n')
    n = len(lines)

    # Detect (title_line_index, title_text) for every underline-style heading
    headings = []
    i = 1
    while i < n:
        title = lines[i - 1].rstrip()
        uline = lines[i].rstrip()
        if (title and uline
                and not title[:1].isspace()
                and len(uline) >= len(title)
                and uline == uline[0] * len(uline)
                and not uline[0].isalnum()):
            headings.append((i - 1, title))
            i += 2  # skip title + underline
        else:
            i += 1

    # Build chunks: prose between consecutive headings
    chunks = []
    current = Path(rel_path).stem.replace('_', ' ')
    prev = 0
    for hpos, htitle in headings:
        body = '\n'.join(lines[prev:hpos]).strip()
        if len(body) >= _MIN_CHUNK_CHARS:
            chunks.append({
                "rel_path": rel_path,
                "heading":  current,
                "text":     body,
                "anchor":   _slugify(current),
            })
        current, prev = htitle, hpos + 2  # advance past title + underline

    body = '\n'.join(lines[prev:]).strip()
    if len(body) >= _MIN_CHUNK_CHARS:
        chunks.append({
            "rel_path": rel_path,
            "heading":  current,
            "text":     body,
            "anchor":   _slugify(current),
        })
    return chunks


def canonical_url(rel_path, version, anchor=None):
    """Map a docs rel_path to its canonical odoo.com/documentation URL.

    Drops the ``content/`` prefix and ``.rst`` suffix; appends ``.html``.

    >>> canonical_url("content/developer/reference/backend/orm.rst", "18")
    'https://www.odoo.com/documentation/18.0/developer/reference/backend/orm.html'
    >>> canonical_url("content/developer/reference/backend/orm.rst", "18", "create")
    'https://www.odoo.com/documentation/18.0/developer/reference/backend/orm.html#create'
    """
    path = rel_path.replace('\\', '/')
    if path.startswith('content/'):
        path = path[len('content/'):]
    if path.endswith('.rst'):
        path = path[:-4] + '.html'
    url = f"https://www.odoo.com/documentation/{_norm_ver(version)}/{path}"
    return f"{url}#{anchor}" if anchor else url


def build_index(chunks):
    """Build a TF-IDF index from doc chunks using native_check's IDF helpers.

    Each chunk is adapted to a card-shaped dict so ``corpus_idf`` can tokenise
    heading + body text.  Vectors are stored sparse (only non-zero weights).

    Returns::

        {
          "idf":   {token: weight, ...},
          "docs":  [{"rel_path","heading","anchor","vec","preview"}, ...],
          "_meta": {"doc_count": N, "vocab_size": M},
        }
    """
    # Adapt chunks → card-like dicts: title = heading, intents = [body text]
    # corpus_idf reads card.get("title") + card.get("intents", []) via card_tokens
    fake_cards = [
        {"title": c["heading"], "intents": [c["text"]], "domain": "", "primitive": ""}
        for c in chunks
    ]
    idf = native_check.corpus_idf(fake_cards)

    docs = []
    for c in chunks:
        toks = native_check.tokenize(c["heading"] + " " + c["text"])
        docs.append({
            "rel_path": c["rel_path"],
            "heading":  c["heading"],
            "anchor":   c["anchor"],
            "vec":      native_check.tfidf_vector(toks, idf),
            "preview":  c["text"][:240].strip(),
        })
    return {
        "idf":   idf,
        "docs":  docs,
        "_meta": {"doc_count": len(docs), "vocab_size": len(idf)},
    }


def query_index(index, q, top=5):
    """Rank docs by cosine(query_vec, doc_vec); return top ``top`` results.

    Returns ``[{"heading","rel_path","anchor","score","preview"}, ...]``,
    sorted descending by score, zero-score entries excluded.
    """
    idf  = index["idf"]
    qvec = native_check.tfidf_vector(native_check.tokenize(q), idf)
    results = [
        {
            "heading":  d["heading"],
            "rel_path": d["rel_path"],
            "anchor":   d["anchor"],
            "score":    round(native_check.cosine(qvec, d["vec"]), 4),
            "preview":  d["preview"],
        }
        for d in index["docs"]
    ]
    results = [r for r in results if r["score"] > 0]
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top]


# ---------------------------------------------------------------------------
# CLI subcommands (no Odoo; subprocess git for --src-less build)
# ---------------------------------------------------------------------------

def _git_checkout(version, repo_url):
    """Best-effort sparse-checkout of content/developer from odoo/documentation."""
    branch  = _norm_ver(version)
    tmpdir  = tempfile.mkdtemp(prefix="odoo-docs-")
    try:
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--sparse", "--depth=1",
             "--branch", branch, repo_url, tmpdir],
            check=True, capture_output=True, timeout=180,
        )
        subprocess.run(
            ["git", "-C", tmpdir, "sparse-checkout", "set", "content/developer"],
            check=True, capture_output=True,
        )
        return Path(tmpdir)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError) as exc:
        print(
            f"ERROR: could not clone {repo_url} (branch {branch}):\n  {exc}\n"
            "  Tip: pass --src pointing at a local checkout to skip git.",
            file=sys.stderr,
        )
        return None


def _cmd_build(args):
    version = args.version
    out_dir = Path(args.out or os.path.expanduser(f"~/.odoo-ai/docs-index/{version}"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.src:
        root = Path(args.src)
        if (root / "content" / "developer").is_dir():
            # src is the docs repo root
            dev_dir = root / "content" / "developer"
            def _rel(p): return str(p.relative_to(root)).replace('\\', '/')
        else:
            # src is the content/developer/ dir itself
            dev_dir = root
            def _rel(p): return "content/developer/" + str(p.relative_to(root)).replace('\\', '/')
    else:
        root = _git_checkout(version, args.repo_url)
        if root is None:
            return
        dev_dir = root / "content" / "developer"
        def _rel(p): return str(p.relative_to(root)).replace('\\', '/')

    chunks, file_count = [], 0
    for rst in sorted(dev_dir.rglob("*.rst")):
        try:
            rel = _rel(rst)
        except ValueError:
            rel = str(rst)
        chunks.extend(chunk_rst(rst.read_text(encoding="utf-8", errors="ignore"), rel))
        file_count += 1

    idx      = build_index(chunks)
    out_file = out_dir / "index.json"
    out_file.write_text(json.dumps(idx, ensure_ascii=False, separators=(',', ':')))
    print(f"Built: {file_count} files · {len(chunks)} chunks · {len(idx['idf'])} vocab")
    print(f"Index: {out_file}")


def _cmd_query(args):
    version  = args.version
    idx_dir  = Path(args.index_dir or os.path.expanduser(f"~/.odoo-ai/docs-index/{version}"))
    idx_file = idx_dir / "index.json"
    if not idx_file.exists():
        print(
            f"ERROR: index not found at {idx_file}\n"
            f"  Run: odoo-ai docs-build --version {version}",
            file=sys.stderr,
        )
        sys.exit(1)
    idx     = json.loads(idx_file.read_text())
    results = query_index(idx, args.q, top=args.top)
    for r in results:
        r["url"] = canonical_url(r["rel_path"], version, r.get("anchor"))
    print(json.dumps({
        "query":   args.q,
        "version": version,
        "results": results,
        "_caveat": (
            "Docs say how the API SHOULD work; introspect the live instance for what "
            "THIS instance has. Existence-gate any model/field/method against the "
            "instance before relying on it."
        ),
    }, indent=2, ensure_ascii=False))


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="doc_index.py",
        description="Build/query a local TF-IDF index of Odoo developer docs (CC-BY-SA-4.0).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("build", help="Build the index from a local .rst tree")
    bp.add_argument("--version",  required=True, help="Odoo version (e.g. 18)")
    bp.add_argument("--src",      help="Docs root containing content/developer/ — skips git clone")
    bp.add_argument("--out",      help="Output directory (default ~/.odoo-ai/docs-index/<V>)")
    bp.add_argument("--repo-url", dest="repo_url",
                    default="https://github.com/odoo/documentation.git",
                    help="Git repo URL (used only when --src is omitted)")

    qp = sub.add_parser("query", help="Query the built index")
    qp.add_argument("q",           help="Natural-language query string")
    qp.add_argument("--version",   required=True, help="Odoo version (e.g. 18)")
    qp.add_argument("--index-dir", dest="index_dir",
                    help="Directory with index.json (default ~/.odoo-ai/docs-index/<V>)")
    qp.add_argument("--top", type=int, default=5, help="Max results (default 5)")

    args = p.parse_args(argv)
    (_cmd_build if args.cmd == "build" else _cmd_query)(args)


if __name__ == "__main__":
    main()
