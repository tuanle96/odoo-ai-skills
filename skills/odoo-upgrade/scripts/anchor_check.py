#!/usr/bin/env python3
"""anchor_check.py — Validate every view/template inheritance anchor OFFLINE
against the target Odoo source tree, before any runtime install.

Why: at runtime Odoo stops at the FIRST broken xpath, so a fleet migration
burns one full install per broken view. This tool composes each CORE parent
view through its complete target-version inheritance chain (same locate
semantics as Odoo: xpath with hasclass(), or tag+attrs match; position
inside/after/before/replace/attributes) and reports every anchor that can no
longer be located — all of them, in seconds, without a database.

Born in a real 18→19 migration of a 45-module production fleet: after 16
one-crash-per-run iterations, one offline pass found the 3 remaining broken
anchors at once. See references/field-notes-18-19.md.

HONESTY CONTRACT
----------------
- Static approximation of Odoo's inheritance engine: core specs that miss
  during composition are skipped silently (they may be sibling-dependent),
  and t-if/dynamic QWeb structure is not evaluated. A clean run means "all
  anchors located against the composed target arch", not "views render".
- Anchors into NON-core parents (your own modules, enterprise when you only
  scanned community) are skipped and counted — they are not validated.
- Composition follows the ANCESTOR chain only. An anchor that a SIBLING core
  module injects into the parent (e.g. purchase_stock adds incoterm_id to the
  purchase order form) reports a false ANCHOR-MISS. But "false" is NOT
  automatic: a sibling-injected anchor is only safe if that sibling is in the
  scanned module's OWN `depends` and installed. Triage every ANCHOR-MISS
  against `depends` — an anchor from an UNDECLARED module (that 18 got away
  with because the prod env happened to have it) is a REAL install crash, not
  a false miss. Observed: `incoterm_id` (purchase_stock, in depends → safe)
  vs `requisition_id` (purchase_requisition, not in depends → crashed).
- `move` positions are not simulated.

Usage:
  python3 anchor_check.py --addons-dir /path/to/ported-addons \
      --target-addons ~/src/odoo-19.0/addons [--target-addons ~/src/enterprise-19.0] \
      [--modules m1,m2 | --modules-file install_set.txt] [--json report.json]

Exit code: 0 = no anchor failures, 1 = failures found, 2 = usage/env error.
Requires lxml (the one non-stdlib dependency in this skill): pip install lxml
"""

from __future__ import annotations

import argparse
import copy
import functools
import json
import re
import sys
from pathlib import Path

try:
    from lxml import etree
except ImportError:  # pragma: no cover
    sys.stderr.write("error: anchor_check.py needs lxml (pip install lxml)\n")
    sys.exit(2)

SKIP_DIRS = {"static", "__pycache__", "tests"}

_ns = etree.FunctionNamespace(None)


def _hasclass(context, *cls):
    classes = (context.context_node.get("class") or "").split()
    return all(c in classes for c in cls)


_ns["hasclass"] = _hasclass

PARSER = etree.XMLParser(remove_comments=True)


class TargetTree:
    """Index + composer over the target-version addons tree(s)."""

    def __init__(self, roots: list[Path]):
        self.roots = roots
        self.modules = {}
        for root in roots:
            for child in sorted(root.iterdir()) if root.is_dir() else []:
                if child.is_dir():
                    self.modules.setdefault(child.name, child)

    @functools.lru_cache(maxsize=None)
    def _xml_files(self, mod: str) -> tuple:
        out = []
        base = self.modules.get(mod)
        for p in sorted(base.rglob("*.xml")) if base else []:
            if not any(s in p.parts for s in SKIP_DIRS):
                out.append(p)
        return tuple(out)

    @functools.lru_cache(maxsize=None)
    def find_def(self, mod: str, xid: str):
        """('record'|'template', element, parent_ref|None) for a view/template."""
        pat = re.compile(r'''id=["'](?:%s\.)?%s["']''' % (re.escape(mod), re.escape(xid)))
        for p in self._xml_files(mod):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not pat.search(text):
                continue
            try:
                tree = etree.parse(str(p), PARSER)
            except etree.XMLSyntaxError:
                continue
            for rec in tree.iter("record"):
                if rec.get("id") in (xid, f"{mod}.{xid}"):
                    arch = rec.find("./field[@name='arch']")
                    inh = rec.find("./field[@name='inherit_id']")
                    ref = inh.get("ref") if inh is not None else None
                    if ref and "." not in ref:
                        ref = f"{mod}.{ref}"
                    if arch is not None:
                        return ("record", arch, ref)
            for tpl in tree.iter("template"):
                if tpl.get("id") in (xid, f"{mod}.{xid}"):
                    ref = tpl.get("inherit_id")
                    if ref and "." not in ref:
                        ref = f"{mod}.{ref}"
                    return ("template", tpl, ref)
        return None

    def compose(self, mod: str, xid: str, depth: int = 0, trail: tuple = ()):
        """Deep-copied arch of a core view composed through its ancestor chain."""
        if depth > 8 or (mod, xid) in trail:
            return None
        d = self.find_def(mod, xid)
        if d is None:
            return None
        kind, container, ref = d
        if ref is None:
            if kind == "record":
                kids = [c for c in container if isinstance(c.tag, str)]
                return copy.deepcopy(kids[0]) if kids else None
            return copy.deepcopy(container)
        pm, px = ref.split(".", 1)
        if pm not in self.modules:
            return None
        base = self.compose(pm, px, depth + 1, trail + ((mod, xid),))
        if base is None:
            return None
        for spec in ops_of(container):
            node, _err = locate(base, spec)
            if node is not None:
                apply_op(base, spec, node)
            # missing core specs are sibling-dependent — skip, base stays usable
        return base


# --------------------------------------------------------------------------- #
# Odoo inheritance semantics (pure — unit-tested)
# --------------------------------------------------------------------------- #

def ops_of(container):
    """Direct op children of an arch/template, unwrapping a bare <data>."""
    kids = [c for c in container if isinstance(c.tag, str)]
    if len(kids) == 1 and kids[0].tag == "data" and kids[0].get("position") is None:
        kids = [c for c in kids[0] if isinstance(c.tag, str)]
    return kids


def locate(arch, spec):
    """Find the anchor node for one inherit spec. Returns (node|None, err|None)."""
    if spec.tag == "xpath":
        expr = spec.get("expr", ".")
        try:
            nodes = arch.xpath(expr)
        except Exception as e:
            return None, f"bad-xpath:{e}"
        return (nodes[0], None) if nodes else (None, "no-match")
    attrs = {k: v for k, v in spec.attrib.items() if k not in ("position", "version")}
    for n in arch.iter(spec.tag):
        if all(n.get(k) == v for k, v in attrs.items()):
            return n, None
    return None, "no-match"


def apply_op(arch, spec, node):
    pos = spec.get("position", "inside")
    content = [copy.deepcopy(c) for c in spec if isinstance(c.tag, str)]
    if pos == "attributes":
        for att in spec.iter("attribute"):
            name = att.get("name")
            if att.text is None and not att.get("add") and not att.get("remove"):
                node.attrib.pop(name, None)
            else:
                node.set(name, att.text or "")
    elif pos == "inside":
        for c in content:
            node.append(c)
    elif pos in ("after", "before"):
        parent = node.getparent()
        idx = parent.index(node) + (1 if pos == "after" else 0)
        for i, c in enumerate(content):
            parent.insert(idx + i, c)
    elif pos == "replace":
        parent = node.getparent()
        if parent is None:
            return
        idx = parent.index(node)
        parent.remove(node)
        for i, c in enumerate(content):
            parent.insert(idx + i, c)
    # 'move' is not simulated


# --------------------------------------------------------------------------- #
# Workspace scan
# --------------------------------------------------------------------------- #

def check_fleet(addons_dirs: list[Path], target: TargetTree,
                modules: list[str] | None = None) -> dict:
    failures, skipped_noncore, checked = [], 0, 0
    for root in addons_dirs:
        for mdir in sorted(root.iterdir()) if root.is_dir() else []:
            if not (mdir / "__manifest__.py").exists():
                continue
            if modules and mdir.name not in modules:
                continue
            for p in sorted(mdir.rglob("*.xml")):
                if any(s in p.parts for s in SKIP_DIRS):
                    continue
                try:
                    tree = etree.parse(str(p), PARSER)
                except etree.XMLSyntaxError as e:
                    failures.append({"file": str(p), "record": "-", "parent": "-",
                                     "problem": f"XML-PARSE: {e}"})
                    continue
                work = []
                for rec in tree.iter("record"):
                    if rec.get("model") != "ir.ui.view":
                        continue
                    inh = rec.find("./field[@name='inherit_id']")
                    arch = rec.find("./field[@name='arch']")
                    if inh is None or arch is None:
                        continue
                    ref = inh.get("ref") or ""
                    if "." in ref:
                        work.append((rec.get("id"), ref, arch))
                for tpl in tree.iter("template"):
                    ref = tpl.get("inherit_id") or ""
                    if "." in ref:
                        work.append((tpl.get("id"), ref, tpl))
                for rid, ref, container in work:
                    pm, px = ref.split(".", 1)
                    if pm not in target.modules:
                        skipped_noncore += 1
                        continue
                    checked += 1
                    base = target.compose(pm, px)
                    if base is None:
                        failures.append({"file": str(p), "record": rid, "parent": ref,
                                         "problem": "PARENT-NOT-COMPOSABLE (removed or unscanned?)"})
                        continue
                    arch = copy.deepcopy(base)
                    for spec in ops_of(container):
                        node, err = locate(arch, spec)
                        if node is None:
                            ident = (spec.get("expr") if spec.tag == "xpath"
                                     else f"{spec.tag}[{spec.get('name')}]")
                            failures.append({"file": str(p), "record": rid, "parent": ref,
                                             "problem": f"ANCHOR-MISS: {ident} ({err})"})
                        else:
                            try:
                                apply_op(arch, spec, node)
                            except Exception:
                                pass
    return {"checked_inherits": checked, "skipped_noncore_parents": skipped_noncore,
            "failures": failures,
            "note": ("clean = anchors located in composed target arch (static); "
                     "non-core parents were NOT validated")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--addons-dir", action="append", required=True, type=Path)
    ap.add_argument("--target-addons", action="append", required=True, type=Path,
                    help="target-version addons roots (community; add enterprise too if used)")
    ap.add_argument("--modules", help="comma-separated subset")
    ap.add_argument("--modules-file", type=Path, help="file with comma/newline-separated names")
    ap.add_argument("--json", type=Path, help="also write the full report as JSON")
    args = ap.parse_args()

    mods = None
    if args.modules:
        mods = [m.strip() for m in args.modules.split(",") if m.strip()]
    elif args.modules_file:
        mods = [m.strip() for m in re.split(r"[,\n]", args.modules_file.read_text()) if m.strip()]

    target = TargetTree(args.target_addons)
    if not target.modules:
        print("error: no modules found under --target-addons", file=sys.stderr)
        return 2
    report = check_fleet(args.addons_dir, target, mods)
    for f in report["failures"]:
        print(f"{f['file']} | {f['record']} | {f['parent']} | {f['problem']}")
    print(f"--- checked={report['checked_inherits']} "
          f"skipped_noncore={report['skipped_noncore_parents']} "
          f"failures={len(report['failures'])}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
