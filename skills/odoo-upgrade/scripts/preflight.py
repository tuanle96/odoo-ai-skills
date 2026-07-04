#!/usr/bin/env python3
"""preflight.py — Apply the DETERMINISTIC field-notes-18-19.md transforms in one
pass, BEFORE any runtime install. Every transform here is a mechanical rewrite
that does not need the target source to verify — the point is to get them out of
the runtime discovery loop (each one otherwise costs a full container install to
find). Source-verified transforms (res.groups→privilege, SVL, mobile→phone,
_name_search...) are NOT here — those go to agents/humans reading
`references/field-notes-18-19.md`, and view anchors go to `anchor_check.py`.

Runs on a COPY of the addons tree (never the originals). Idempotent-ish: safe to
re-run. Reports what it changed; writes the external-python-deps list for the
verify container to `<out>/pydeps.txt`.

Covers field notes: #1 (deps scan), #2 (description xml-decl), #3 (version bump),
#4 (_sql_constraints→Constraint incl. dead comment blocks that crash the
rewriter), #10 (module categories), #11 (search-view group string/expand),
#12 (target=inline), #13 (tree leftovers), #16 (odoo.fields stdlib import).

Usage:
  python3 preflight.py --addons-dir /path/to/COPY [--out /tmp/odoo-ai/upgrade/_fleet]

Stdlib only. License: MIT (part of the odoo-upgrade skill).
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# field note #10 — module category consolidation (verify survivors in your
# target's base/data/ir_module_category_data.xml before trusting these)
CATEGORY_MAP = {
    "base.module_category_inventory_inventory": "base.module_category_supply_chain",
    "base.module_category_inventory": "base.module_category_supply_chain",
    "base.module_category_manufacturing": "base.module_category_supply_chain",
    "base.module_category_sales_sales": "base.module_category_sales",
    "base.module_category_human_resources_fleet": "base.module_category_human_resources",
}
TARGET_SERIES = "19.0"
OLD_SERIES = ("18.0", "17.0", "16.0")


def _py_files(ws: Path):
    for p in sorted(ws.rglob("*.py")):
        if "__pycache__" not in p.parts:
            yield p


def _xml_files(ws: Path):
    for p in sorted(ws.rglob("*.xml")):
        if "static" not in p.parts and "repomix" not in p.name:
            yield p


def convert_sql_constraints(ws: Path) -> str:
    """#4 — _sql_constraints -> models.Constraint; drop dead commented blocks
    (both crash `odoo-bin upgrade_code`)."""
    assign_re = re.compile(r"^([ \t]+)_sql_constraints\s*=\s*\[", re.M)
    dead_re = re.compile(r"^[ \t]*#[ \t]*_sql_constraints[ \t]*=[ \t]*\[\n(?:[ \t]*#.*\n)*", re.M)
    n = 0
    for p in _py_files(ws):
        t = p.read_text(encoding="utf-8", errors="replace")
        t2 = dead_re.sub("", t)
        m = assign_re.search(t2)
        if not m:
            if t2 != t:
                p.write_text(t2)
            continue
        start = t2.index("[", m.start())
        depth, i = 0, start
        while i < len(t2):
            if t2[i] == "[":
                depth += 1
            elif t2[i] == "]":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block, indent = t2[start:i + 1], m.group(1)
        try:
            triples = ast.literal_eval(re.sub(r"\b_\(", "(", block))
        except Exception:
            if t2 != t:
                p.write_text(t2)
            continue
        attrs = []
        for name, definition, *msg in triples:
            message = msg[0] if msg else ""
            attrs.append(f"{indent}_{name} = models.Constraint(\n"
                         f"{indent}    {definition!r},\n{indent}    {message!r},\n{indent})")
        p.write_text(t2[:m.start()] + "\n".join(attrs) + t2[i + 1:])
        n += 1
    return f"#4 _sql_constraints converted: {n} files"


def bump_versions(ws: Path) -> str:
    """#3 — bump old-series versions to the target series. Series-less versions
    (`"1.0"`) are NOT auto-bumped (could be intentional) but FLAGGED: they skip
    their own migration scripts on -u unless bumped to 19.0.x (field-notes #37)."""
    n = 0
    seriesless = []
    pat = re.compile(r"([\"']version[\"']\s*:\s*[\"'])(?:%s)\." % "|".join(
        re.escape(s.split(".")[0] + "." + s.split(".")[1]) for s in OLD_SERIES))
    ver_re = re.compile(r"[\"']version[\"']\s*:\s*[\"']([^\"']+)[\"']")
    for mf in ws.glob("*/__manifest__.py"):
        t = mf.read_text(encoding="utf-8", errors="replace")
        t2 = pat.sub(r"\g<1>%s." % TARGET_SERIES, t)
        if t2 != t:
            mf.write_text(t2)
            n += 1
        else:
            m = ver_re.search(t)
            if m and not re.match(r"(?:19|18|17|16)\.0\.", m.group(1)):
                seriesless.append(f"{mf.parent.name} (={m.group(1)})")
    out = f"#3 manifest version -> {TARGET_SERIES}: {n}"
    if seriesless:
        out += ("\n     #37 series-less versions — bump to 19.0.x by hand or "
                "their migrations won't run: " + ", ".join(seriesless))
    return out


def strip_description(ws: Path) -> str:
    n = 0
    for p in ws.glob("*/static/description/index.html"):
        t = p.read_text(encoding="utf-8", errors="replace")
        t2 = re.sub(r"^\s*<\?xml[^>]*\?>\s*\n", "", t)
        if t2 != t:
            p.write_text(t2)
            n += 1
    return f"#2 description xml-decl stripped: {n}"


def xml_sweeps(ws: Path) -> str:
    search_block = re.compile(r"<search\b.*?</search>", re.S)
    n_inline = n_tree = n_grp = n_cat = 0
    for p in _xml_files(ws):
        t = p.read_text(encoding="utf-8", errors="replace")
        orig = t
        t2 = re.sub(r'[ \t]*<field name="target">inline</field>\n', "", t)   # #12
        if t2 != t:
            n_inline += 1
            t = t2
        if 'mode="tree"' in t:
            t = t.replace('mode="tree"', 'mode="list"')                       # #13
            n_tree += 1
        if "tree_view_ref" in t:
            t = t.replace("'tree_view_ref'", "'list_view_ref'").replace(
                '"tree_view_ref"', '"list_view_ref"')
        for old, new in CATEGORY_MAP.items():                                 # #10
            if old in t:
                t = re.sub(re.escape(old) + r"\b", new, t)
                n_cat += 1

        def clean(m):                                                        # #11
            s = m.group(0)
            s = re.sub(r'(<group\b[^>]*?)\s+string="[^"]*"', r"\1", s)
            s = re.sub(r"(<group\b[^>]*?)\s+expand=([\"'])[01]\2", r"\1", s)
            return s
        t3 = search_block.sub(clean, t)
        if t3 != t:
            n_grp += 1
            t = t3
        if t != orig:
            p.write_text(t)
    return (f"#12 target=inline:{n_inline} #13 tree->list:{n_tree} "
            f"#11 search-group:{n_grp} #10 categories:{n_cat}")


def fix_fields_import(ws: Path) -> str:
    """#16 — `from odoo.fields import datetime/date` (19 made odoo.fields a
    package) → stdlib; and lowercase `fields.date` / `fields.datetime` helpers
    (gone in 19) → the `fields.Date` / `fields.Datetime` classes."""
    n = 0
    for p in _py_files(ws):
        t = p.read_text(encoding="utf-8", errors="replace")
        t2 = re.sub(r"^from odoo\.fields import (datetime|date)\b",
                    lambda m: f"from datetime import {m.group(1)}", t, flags=re.M)
        t2 = re.sub(r"\bfields\.date\b", "fields.Date", t2)
        t2 = re.sub(r"\bfields\.datetime\b", "fields.Datetime", t2)
        if t2 != t:
            p.write_text(t2)
            n += 1
    return f"#16 odoo.fields import/helper: {n}"


def flag_init_hooks(ws: Path) -> str:
    """#28 — init hooks changed signature (cr, registry) -> (env) in 19. The
    body port isn't purely mechanical (cr->env.cr), so this only FLAGS them —
    it does not rewrite. Report so they don't get discovered by a crash."""
    hook_re = re.compile(r"^def (post_init_hook|pre_init_hook|uninstall_hook)\(cr,\s*registry\)", re.M)
    hits = []
    for p in _py_files(ws):
        if hook_re.search(p.read_text(encoding="utf-8", errors="replace")):
            hits.append(str(p.relative_to(ws)))
    if not hits:
        return "#28 init hooks with old (cr, registry) signature: none"
    return ("#28 PORT THESE init hooks to (env) — env.cr / env.registry "
            "(field-notes #28):\n     " + "\n     ".join(hits))


def scan_python_deps(ws: Path, out: Path) -> str:
    pkgs = set()
    for mf in ws.glob("*/__manifest__.py"):
        try:
            d = ast.literal_eval(mf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        pkgs.update(d.get("external_dependencies", {}).get("python", []))
    out.mkdir(parents=True, exist_ok=True)
    (out / "pydeps.txt").write_text(" ".join(sorted(pkgs)))
    return f"#1 external python deps -> {out/'pydeps.txt'}: {sorted(pkgs)}"


def compile_broken(ws: Path) -> set:
    """Set of files that fail to compile/parse RIGHT NOW."""
    import py_compile
    broken = set()
    for p in _py_files(ws):
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError:
            broken.add(p)
    for p in _xml_files(ws):
        try:
            ET.parse(p)
        except ET.ParseError:
            broken.add(p)
    return broken


def validate(ws: Path, baseline: set) -> int:
    """Report ONLY files that transforms newly broke — not source that was
    already broken before pre-flight (a real Vantis run had 18 test files
    committed with syntax errors; blaming pre-flight for those cries wolf)."""
    now = compile_broken(ws)
    introduced = now - baseline
    for p in sorted(introduced):
        print(f"  NEWLY-BROKEN {p.relative_to(ws)}", file=sys.stderr)
    if baseline:
        print(f"  ({len(baseline)} file(s) were already broken in the source "
              f"before pre-flight — not counted)")
    return len(introduced)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--addons-dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("/tmp/odoo-ai/upgrade/_fleet"))
    args = ap.parse_args()
    if not args.addons_dir.is_dir():
        print(f"error: not a directory: {args.addons_dir}", file=sys.stderr)
        return 2

    ws = args.addons_dir
    baseline = compile_broken(ws)   # snapshot source before touching anything
    for step in (convert_sql_constraints, bump_versions, strip_description,
                 xml_sweeps, fix_fields_import):
        print(step(ws))
    print(flag_init_hooks(ws))
    print(scan_python_deps(ws, args.out))
    bad = validate(ws, baseline)
    print(f"errors INTRODUCED by pre-flight: {bad}")
    print("\nNEXT: read brief WARNINGs as a checklist, run upgrade_code + "
          "anchor_check.py, then the source-verified transforms "
          "(references/field-notes-18-19.md).")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
