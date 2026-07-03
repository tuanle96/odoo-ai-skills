#!/usr/bin/env python3
"""gen_manifest.py — Generate a breaking-change manifest between two Odoo source trees.

Diffs two checkouts of odoo/odoo (and optionally enterprise/custom addons) at the
static-analysis level and emits a JSON manifest that AI agents and other scripts
(upgrade_brief.py) consume to detect migration-relevant breakage in custom modules.

What it detects (per addon tree):
  * modules       : addon directories removed / added
  * models        : models removed / added (by ``_name``), with rename CANDIDATES
                    matched heuristically via Jaccard similarity of field-name sets
  * fields        : per surviving model — removed / added / type-changed fields,
                    plus field-rename CANDIDATES (same type + high name similarity)
  * methods       : per surviving model — method names that disappeared
  * xml ids       : ``<record>``/``<template>``/``<menuitem>`` ids removed / added

HONESTY CONTRACT
----------------
This is *static* analysis. It approximates runtime field composition by merging
``_inherit`` extensions found in the scanned tree, but it does NOT execute Odoo,
does NOT resolve the full MRO across unscanned addons, and rename matching is a
HEURISTIC. Anything under a ``*_candidates`` key carries a similarity score and
must be verified (e.g. against git history or a live registry) before being
treated as fact. Keys never overclaim: "removed" means "no longer *defined in the
scanned tree*", which for partial scans may simply mean "moved elsewhere".

Usage:
  python gen_manifest.py --old /path/to/odoo-18.0 --new /path/to/odoo-19.0 \
      --addons-subdirs odoo/addons,addons \
      --out references/manifest_18_19.json [--label 18.0-to-19.0]

Stdlib only. License: MIT (part of the odoo-upgrade skill).
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import difflib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SKIP_DIRS = {"tests", "static", "migrations", "upgrades", "node_modules", ".git", "populate"}
XML_RECORD_TAGS = {"record", "template", "menuitem", "act_window", "report"}
MODEL_MATCH_THRESHOLD = 0.60    # overlap coefficient |A∩B|/min(|A|,|B|) on field-name sets
FIELD_RENAME_THRESHOLD = 0.60   # difflib ratio on field names (same type)
MIN_FIELDS_FOR_MATCH = 4        # don't rename/merge-match near-empty models


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #

def _const_str(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _iter_addon_dirs(root: Path, subdirs: list[str]):
    for sub in subdirs:
        base = root / sub
        if not base.is_dir():
            continue
        for addon in sorted(base.iterdir()):
            # test_* addons are never deployed and pollute rename matching
            # (observed: hr.employee.base -> test_performance.simple.minded)
            if addon.name.startswith("test_"):
                continue
            if addon.is_dir() and (
                (addon / "__manifest__.py").exists() or (addon / "__openerp__.py").exists()
            ):
                yield addon.name, addon


def _walk_files(addon_dir: Path, suffix: str):
    for p in sorted(addon_dir.rglob(f"*{suffix}")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _extract_fields_and_methods(cls: ast.ClassDef):
    """Return (_name, _inherit_list, fields{name:{type,comodel}}, method_names)."""
    name = None
    inherits: list[str] = []
    fields: dict[str, dict] = {}
    methods: list[str] = []
    for node in cls.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            tname = node.targets[0].id
            if tname == "_name":
                name = _const_str(node.value) or name
            elif tname == "_inherit":
                v = node.value
                if (s := _const_str(v)) is not None:
                    inherits.append(s)
                elif isinstance(v, (ast.List, ast.Tuple)):
                    inherits.extend(s for e in v.elts if (s := _const_str(e)))
            elif (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and isinstance(node.value.func.value, ast.Name)
                and node.value.func.value.id == "fields"
            ):
                call = node.value
                info = {"type": call.func.attr}
                comodel = None
                if call.args:
                    comodel = _const_str(call.args[0])
                for kw in call.keywords:
                    if kw.arg == "comodel_name":
                        comodel = _const_str(kw.value) or comodel
                if comodel and info["type"] in {"Many2one", "One2many", "Many2many"}:
                    info["comodel"] = comodel
                fields[tname] = info
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(node.name)
    return name, inherits, fields, methods


def collect_tree(root: Path, subdirs: list[str]) -> dict:
    """Scan one source tree. Returns {modules, models, xmlids, stats}."""
    models: dict[str, dict] = {}
    xmlids: set[str] = set()
    modules: list[str] = []
    stats = {"py_files": 0, "xml_files": 0, "py_parse_errors": 0, "xml_parse_errors": 0}

    def model_entry(mname: str) -> dict:
        return models.setdefault(
            mname, {"module": None, "fields": {}, "methods": set(), "defined": False}
        )

    for addon_name, addon_dir in _iter_addon_dirs(root, subdirs):
        modules.append(addon_name)

        for py in _walk_files(addon_dir, ".py"):
            stats["py_files"] += 1
            try:
                tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                stats["py_parse_errors"] += 1
                continue
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                name, inherits, fields, methods = _extract_fields_and_methods(cls)
                targets = []
                if name:  # defines (or redefines) the model
                    e = model_entry(name)
                    e["defined"] = True
                    e["module"] = e["module"] or addon_name
                    targets.append(e)
                elif len(inherits) == 1:  # pure extension: merge into inherited model
                    targets.append(model_entry(inherits[0]))
                # multi-_inherit without _name (mixins into several models): merge into each
                elif inherits:
                    targets.extend(model_entry(i) for i in inherits)
                for e in targets:
                    e["fields"].update(fields)
                    e["methods"].update(methods)

        for xf in _walk_files(addon_dir, ".xml"):
            stats["xml_files"] += 1
            try:
                xroot = ET.parse(xf).getroot()
            except ET.ParseError:
                stats["xml_parse_errors"] += 1
                continue
            for el in xroot.iter():
                if el.tag in XML_RECORD_TAGS and (rid := el.get("id")):
                    xmlids.add(rid if "." in rid else f"{addon_name}.{rid}")

    return {"modules": sorted(modules), "models": models, "xmlids": xmlids, "stats": stats}


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #

def _overlap(a: set, b: set) -> float:
    """Overlap coefficient: robust when the new model is a superset of the old one
    (e.g. hr.contract(27 fields) absorbed into hr.version(63 fields) -> 0.70)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


_HEURISTIC_NOTE = "HEURISTIC — confirm against git history / upgrade scripts before use"


def _best_matches(removed: dict, pool: dict, evidence: str):
    """Greedy best-match removed→pool models by field-set overlap. Heuristic."""
    pairs = []
    for old_name, old in removed.items():
        of = set(old["fields"])
        if len(of) < MIN_FIELDS_FOR_MATCH:
            continue
        for new_name, new in pool.items():
            nf = set(new["fields"])
            if len(nf) < MIN_FIELDS_FOR_MATCH:
                continue
            # absolute-intersection floor: a 4-field model sharing 3 generic
            # fields scores 0.75 by overlap — that's noise, not a rename
            if len(of & nf) < MIN_FIELDS_FOR_MATCH:
                continue
            score = _overlap(of, nf)
            if score >= MODEL_MATCH_THRESHOLD:
                pairs.append((round(score, 3), old_name, new_name))
    pairs.sort(reverse=True)
    used_old, used_new, out = set(), set(), []
    for score, o, n in pairs:
        if o in used_old or n in used_new:
            continue
        used_old.add(o)
        used_new.add(n)
        out.append({"old": o, "new": n, "similarity": score,
                    "evidence": evidence, "note": _HEURISTIC_NOTE})
    return out


def _match_removed_models(removed: dict, added: dict, surviving_new: dict):
    """Two-phase matching for removed models:
    1. rename candidates : removed → newly-added model (hr.contract → hr.version)
    2. merge candidates  : leftover removed → model that already existed and
                           absorbed the fields (hr.candidate → hr.applicant,
                           hr.expense.sheet → hr.expense)"""
    renames = _best_matches(removed, added, "overlap(field_names) vs ADDED model")
    matched = {r["old"] for r in renames}
    leftover = {k: v for k, v in removed.items() if k not in matched}
    merges = _best_matches(leftover, surviving_new, "overlap(field_names) vs SURVIVING model")
    return renames, merges


def _diff_model(old: dict, new: dict) -> dict | None:
    of, nf = old["fields"], new["fields"]
    removed = {k: of[k]["type"] for k in of.keys() - nf.keys()}
    added = {k: nf[k]["type"] for k in nf.keys() - of.keys()}
    type_changed = {
        k: {"old": of[k]["type"], "new": nf[k]["type"]}
        for k in of.keys() & nf.keys()
        if of[k]["type"] != nf[k]["type"]
    }
    field_renames = []
    for rk, rtype in list(removed.items()):
        best = None
        for ak, atype in added.items():
            if atype != rtype:
                continue
            r = difflib.SequenceMatcher(None, rk, ak).ratio()
            if r >= FIELD_RENAME_THRESHOLD and (best is None or r > best[0]):
                best = (round(r, 3), ak)
        if best:
            field_renames.append(
                {"old": rk, "new": best[1], "type": rtype, "similarity": best[0]}
            )
    methods_removed = sorted(old["methods"] - new["methods"])
    d = {}
    if removed:
        d["fields_removed"] = dict(sorted(removed.items()))
    if added:
        d["fields_added"] = dict(sorted(added.items()))
    if type_changed:
        d["fields_type_changed"] = type_changed
    if field_renames:
        d["field_rename_candidates"] = field_renames
    if methods_removed:
        d["methods_removed"] = methods_removed
    return d or None


def build_manifest(old_root: Path, new_root: Path, subdirs: list[str], label: str) -> dict:
    old = collect_tree(old_root, subdirs)
    new = collect_tree(new_root, subdirs)

    om, nm = old["models"], new["models"]
    removed_models = {k: om[k] for k in om.keys() - nm.keys()}
    added_models = {k: nm[k] for k in nm.keys() - om.keys()}
    surviving_new = {k: nm[k] for k in om.keys() & nm.keys()}
    rename_candidates, merge_candidates = _match_removed_models(
        removed_models, added_models, surviving_new
    )

    changed = {}
    for name in sorted(om.keys() & nm.keys()):
        if d := _diff_model(om[name], nm[name]):
            changed[name] = d

    def _git_ref(p: Path) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(p), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10,
            )
            return r.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    return {
        "meta": {
            "label": label,
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "generator": "gen_manifest.py (odoo-upgrade skill)",
            "method": (
                "static AST + XML scan; _inherit extensions merged within the scanned tree only; "
                "rename matching is HEURISTIC (see *_candidates keys)"
            ),
            "old": {"root": str(old_root), "git_ref": _git_ref(old_root), **old["stats"],
                    "modules_scanned": len(old["modules"])},
            "new": {"root": str(new_root), "git_ref": _git_ref(new_root), **new["stats"],
                    "modules_scanned": len(new["modules"])},
            "coverage_warning": (
                "Findings are relative to the SCANNED subtrees. A 'removed' entry may mean "
                "'moved to an unscanned addon'. For authoritative results scan full "
                "community+enterprise trees for both versions."
            ),
        },
        "modules": {
            "removed": sorted(set(old["modules"]) - set(new["modules"])),
            "added": sorted(set(new["modules"]) - set(old["modules"])),
        },
        "models": {
            "removed": {
                k: {"module": v["module"], "field_count": len(v["fields"])}
                for k, v in sorted(removed_models.items())
            },
            "added": {
                k: {"module": v["module"], "field_count": len(v["fields"])}
                for k, v in sorted(added_models.items())
            },
            "renamed_candidates": rename_candidates,
            "merged_candidates": merge_candidates,
            "changed": changed,
        },
        "xmlids": {
            "removed": sorted(old["xmlids"] - new["xmlids"]),
            "added_count": len(new["xmlids"] - old["xmlids"]),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--old", required=True, type=Path)
    ap.add_argument("--new", required=True, type=Path)
    ap.add_argument("--addons-subdirs", default="odoo/addons,addons",
                    help="comma-separated addon roots relative to each tree "
                         "(use '.' for a bare custom-addons dir)")
    ap.add_argument("--out", type=Path, default=Path("manifest.json"))
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    for p in (args.old, args.new):
        if not p.is_dir():
            print(f"error: not a directory: {p}", file=sys.stderr)
            return 2

    subdirs = [s.strip() for s in args.addons_subdirs.split(",") if s.strip()]
    manifest = build_manifest(args.old, args.new,
                              subdirs, args.label or f"{args.old.name}->{args.new.name}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    m = manifest["models"]
    print(f"wrote {args.out}")
    print(f"  modules: -{len(manifest['modules']['removed'])} +{len(manifest['modules']['added'])}")
    print(f"  models : -{len(m['removed'])} +{len(m['added'])} "
          f"| rename: {len(m['renamed_candidates'])} merge: {len(m['merged_candidates'])} | changed: {len(m['changed'])}")
    print(f"  xmlids removed: {len(manifest['xmlids']['removed'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
