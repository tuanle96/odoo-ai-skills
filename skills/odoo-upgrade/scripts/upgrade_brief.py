#!/usr/bin/env python3
"""upgrade_brief.py — Cross-reference a custom Odoo module against a breaking-change
manifest (produced by gen_manifest.py) and emit a per-module migration brief.

For each custom module it reports, with file:line locations:
  ERROR   depends on a removed module
  ERROR   references a removed model (with rename/merge candidate if the manifest
          has one — candidates are heuristic, see the attached note)
  ERROR   references an XML id that is no longer defined in the target tree
  WARNING textual usage of a field that was removed from a model this module touches
  WARNING textual usage / override of a method that was removed from such a model
  INFO    references a model whose definition changed (review the manifest entry)

HONESTY CONTRACT
----------------
Field/method usage detection is a word-boundary TEXT match scoped to the module's
own files — it can miss dynamic access (getattr, mapped('x.y')) and can false-
positive on identically named symbols of other models. Every finding carries its
`detection` method so the consuming agent knows how much to trust it. A clean
brief does NOT mean the module is migration-ready; it means none of the manifest's
known breakages were textually detected. Runtime verification (upgrade_verify.py)
remains mandatory.

Usage:
  python upgrade_brief.py --module /path/to/custom_module \
      --manifest references/manifest_18_19.json \
      [--out /tmp/odoo-ai/upgrade/<module>/brief.json]

Stdlib only. License: MIT (part of the odoo-upgrade skill).
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", "static/lib"}

RE_ENV_MODEL = re.compile(r'\benv\[\s*[\'"]([\w.]+)[\'"]\s*\]')
RE_ENV_REF = re.compile(r'\.ref\(\s*[\'"]([\w.]+)[\'"]')
RE_XML_REF_ATTR = re.compile(r'\b(?:ref|inherit_id|action|parent|menu)\s*=\s*"([\w.]+)"')
RE_XML_EVAL_REF = re.compile(r'\bref\(\s*[\'"]([\w.]+)[\'"]\s*\)')
RE_XML_GROUPS = re.compile(r'\bgroups\s*=\s*"([^"]+)"')
RE_XML_MODEL_ATTR = re.compile(r'\bmodel\s*=\s*"([\w.]+)"')
RE_XML_MODEL_FIELD = re.compile(
    r'<field\s+name="(?:model|res_model|binding_model|model_id)"\s*(?:ref="([\w.]+)")?\s*>?([\w.]*)'
)


def _iter_files(module: Path, suffixes: tuple[str, ...]):
    for p in sorted(module.rglob("*")):
        if (p.is_file() and p.suffix in suffixes
                and not any(s in p.parts for s in SKIP_DIRS)
                and "static/lib" not in p.as_posix()):  # vendored JS, not module code
            yield p


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# --------------------------------------------------------------------------- #
# Module usage collection
# --------------------------------------------------------------------------- #

def collect_module_usage(module: Path) -> dict:
    """Scan one custom module. Returns models/xmlids referenced (+locations),
    own model definitions, and manifest depends."""
    models: dict[str, list] = defaultdict(list)   # model -> [(file, line)]
    xmlids: dict[str, list] = defaultdict(list)
    own_models: set[str] = set()
    inherited: set[str] = set()
    depends: list[str] = []

    mf = module / "__manifest__.py"
    if mf.exists():
        try:
            data = ast.literal_eval(mf.read_text(encoding="utf-8", errors="replace"))
            depends = list(data.get("depends", []))
        except Exception:
            pass

    for py in _iter_files(module, (".py",)):
        rel = str(py.relative_to(module))
        text = py.read_text(encoding="utf-8", errors="replace")
        for rx, sink in ((RE_ENV_MODEL, models), (RE_ENV_REF, xmlids)):
            for m in rx.finditer(text):
                sink[m.group(1)].append((rel, _line_of(text, m.start())))
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for node in cls.body:
                if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                        and isinstance(node.targets[0], ast.Name)):
                    continue
                t = node.targets[0].id
                if t == "_name" and isinstance(node.value, ast.Constant):
                    own_models.add(node.value.value)
                elif t == "_inherit":
                    vals = [node.value] if isinstance(node.value, ast.Constant) \
                        else getattr(node.value, "elts", [])
                    for v in vals:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            inherited.add(v.value)
                            models[v.value].append((rel, v.lineno))
                elif (isinstance(node.value, ast.Call)
                      and isinstance(node.value.func, ast.Attribute)
                      and getattr(node.value.func.value, "id", "") == "fields"
                      and node.value.args
                      and isinstance(node.value.args[0], ast.Constant)
                      and node.value.func.attr in {"Many2one", "One2many", "Many2many"}):
                    models[node.value.args[0].value].append((rel, node.value.lineno))

    for xf in _iter_files(module, (".xml",)):
        rel = str(xf.relative_to(module))
        text = xf.read_text(encoding="utf-8", errors="replace")
        for m in RE_XML_MODEL_ATTR.finditer(text):
            models[m.group(1)].append((rel, _line_of(text, m.start())))
        for m in RE_XML_MODEL_FIELD.finditer(text):
            val = m.group(2)
            if val and "." in val:
                models[val].append((rel, _line_of(text, m.start())))
        for rx in (RE_XML_REF_ATTR, RE_XML_EVAL_REF):
            for m in rx.finditer(text):
                if "." in m.group(1):
                    xmlids[m.group(1)].append((rel, _line_of(text, m.start())))
        for m in RE_XML_GROUPS.finditer(text):
            for g in m.group(1).replace("!", "").split(","):
                if "." in (g := g.strip()):
                    xmlids[g].append((rel, _line_of(text, m.start())))

    return {"models": dict(models), "xmlids": dict(xmlids),
            "own_models": sorted(own_models), "inherited": sorted(inherited),
            "depends": depends}


# Ubiquitous ORM/dict tokens: a bare text match is pure noise (dict.get,
# records.create...). For these, only count lines that also name the model.
GENERIC_TOKENS = {"create", "write", "unlink", "read", "browse", "search", "copy",
                  "exists", "check", "get", "default_get", "name_get", "name_search"}


def _grep_word(module: Path, word: str, max_hits: int = 20, require: str | None = None):
    rx = re.compile(rf"(?<!\w){re.escape(word)}\b")
    hits = []
    for p in _iter_files(module, (".py", ".xml", ".js", ".csv")):
        rel = str(p.relative_to(module))
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if rx.search(line) and (require is None or require in line):
                hits.append({"file": rel, "line": i})
                if len(hits) >= max_hits:
                    return hits
    return hits


# --------------------------------------------------------------------------- #
# Cross-reference against manifest
# --------------------------------------------------------------------------- #

def build_brief(module: Path, manifest: dict) -> dict:
    usage = collect_module_usage(module)
    mm = manifest["models"]
    findings: list[dict] = []
    rename_by_old = {c["old"]: c for c in mm.get("renamed_candidates", [])}
    merge_by_old = {c["old"]: c for c in mm.get("merged_candidates", [])}
    removed_xmlids = set(manifest.get("xmlids", {}).get("removed", []))

    def add(severity, kind, subject, detail, locations, detection, suggestion=None):
        findings.append({"severity": severity, "kind": kind, "subject": subject,
                         "detail": detail, "locations": locations,
                         "detection": detection,
                         **({"suggestion": suggestion} if suggestion else {})})

    for dep in usage["depends"]:
        if dep in manifest.get("modules", {}).get("removed", []):
            add("ERROR", "removed_module_dependency", dep,
                f"__manifest__.py depends on '{dep}', which no longer exists in the target tree.",
                [{"file": "__manifest__.py", "line": 1}], "manifest depends list",
                "Remove/replace the dependency; check where its models moved "
                "(see models.renamed_candidates / merged_candidates).")

    for model, locs in sorted(usage["models"].items()):
        locations = [{"file": f, "line": l} for f, l in locs[:20]]
        if model in mm.get("removed", {}):
            cand = rename_by_old.get(model) or merge_by_old.get(model)
            hint = (f" Candidate replacement: '{cand['new']}' "
                    f"(similarity {cand['similarity']}, {cand['note']})." if cand else
                    " No replacement candidate found in the scanned trees.")
            add("ERROR", "removed_model", model,
                f"Model '{model}' is not defined in the target tree." + hint,
                locations, "manifest models.removed",
                f"Migrate references to '{cand['new']}'." if cand else None)
        elif model in mm.get("changed", {}):
            ch = mm["changed"][model]
            add("INFO", "changed_model", model,
                "Model definition changed in target: "
                + ", ".join(f"{k}={len(v)}" for k, v in ch.items()),
                locations, "manifest models.changed")
            for fname in ch.get("fields_removed", {}):
                if hits := _grep_word(module, fname):
                    add("WARNING", "removed_field_usage", f"{model}.{fname}",
                        f"Field '{fname}' was removed from '{model}' and this token "
                        f"appears in the module (text match — may be a different symbol).",
                        hits, "word-boundary text match")
            for mname in ch.get("methods_removed", []):
                req = model if mname in GENERIC_TOKENS else None
                if hits := _grep_word(module, mname, require=req):
                    add("WARNING", "removed_method_usage", f"{model}.{mname}",
                        f"Method '{mname}' no longer exists on '{model}' in the target "
                        f"tree and this token appears in the module (text match).",
                        hits, "word-boundary text match")

    for xid, locs in sorted(usage["xmlids"].items()):
        if xid in removed_xmlids:
            add("ERROR", "removed_xmlid", xid,
                f"XML id '{xid}' is no longer defined in the scanned target tree "
                f"(env.ref()/ref= will raise or silently break).",
                [{"file": f, "line": l} for f, l in locs[:20]],
                "manifest xmlids.removed")

    sev_rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    findings.sort(key=lambda f: (sev_rank[f["severity"]], f["kind"], f["subject"]))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in sev_rank}
    return {
        "module": module.name,
        "manifest_label": manifest.get("meta", {}).get("label", "?"),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "scope_note": ("Findings limited to breakages present in the manifest and to "
                       "static/textual detection. Not a completeness guarantee — run "
                       "upgrade_verify.py for runtime confirmation."),
        "summary": counts,
        "own_models": usage["own_models"],
        "inherited_models": usage["inherited"],
        "depends": usage["depends"],
        "findings": findings,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--module", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    if not (args.module / "__manifest__.py").exists():
        print(f"error: {args.module} does not look like an Odoo module", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest.read_text())
    brief = build_brief(args.module, manifest)
    out = args.out or Path(f"/tmp/odoo-ai/upgrade/{args.module.name}/brief.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(brief, indent=2, ensure_ascii=False))
    s = brief["summary"]
    print(f"wrote {out}")
    print(f"  {brief['module']}: ERROR={s['ERROR']} WARNING={s['WARNING']} INFO={s['INFO']}")
    for f in brief["findings"]:
        if f["severity"] == "ERROR":
            print(f"  [ERROR] {f['kind']}: {f['subject']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
