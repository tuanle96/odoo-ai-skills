#!/usr/bin/env python3
"""source_sweep.py — non-mutating 18->19 source-verified checklist scanner.

It flags high-signal patterns from references/field-notes-18-19.md that should be
reviewed against target source before the runtime verify loop. It does not rewrite:
most hits need model/context confirmation.

Usage:
  python3 source_sweep.py --addons-dir /path/to/PORTED-copy \
      --out /tmp/odoo-ai/upgrade/_fleet/source_sweep.json

Stdlib only. License: MIT (part of the odoo-upgrade skill).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", "node_modules", "static/lib"}
PY_DEP_ALIASES = {
    "cv2": "opencv-python",
    "jwt": "pyjwt",
    "pil": "pillow",
    "yaml": "pyyaml",
}
IGNORE_IMPORT_ROOTS = {"odoo", "openerp"}
STD_MODULES = set(getattr(sys, "stdlib_module_names", ()))

PATTERNS = [
    ("res_groups_category_xml", "ERROR", (".xml",),
     r'<record[^>]+model="res\.groups"[\s\S]{0,900}?<field\s+name="category_id"',
     "res.groups category_id became privilege_id via res.groups.privilege."),
    ("res_groups_users_xml", "ERROR", (".xml",),
     r'<record[^>]+model="res\.groups"[\s\S]{0,900}?<field\s+name="users"',
     "res.groups users became user_ids."),
    ("groups_id_xml_record_field", "WARNING", (".xml",),
     r'<field\s+name="groups_id"',
     "groups_id in XML records may need group_ids; arch fields need context review."),
    ("groups_id_python", "WARNING", (".py",),
     r"\bgroups_id\b",
     "res.users groups_id reads/writes split into group_ids/all_group_ids; review context."),
    ("name_search_override", "ERROR", (".py",),
     r"def\s+_name_search\s*\(",
     "_name_search override is dead in 19; port to _search_display_name."),
    ("name_get_override", "WARNING", (".py",),
     r"def\s+name_get\s*\(",
     "name_get is no longer framework-called; port display logic."),
    ("request_jsonrequest", "ERROR", (".py",),
     r"\brequest\.jsonrequest\b",
     "request.jsonrequest became request.get_json_data()."),
    ("get_module_resource", "ERROR", (".py",),
     r"\bget_module_resource\b",
     "get_module_resource is gone; use odoo.tools.misc.file_path."),
    ("top_level_registry_import", "ERROR", (".py",),
     r"from\s+odoo\s+import\s+registry\b",
     "top-level odoo.registry import is gone; use odoo.modules.registry.Registry."),
    ("stock_valuation_layer_api", "ERROR", (".py", ".xml", ".csv"),
     r"stock_valuation_layer_ids|stock\.valuation\.layer|_account_entry_move|_generate_valuation_lines_data",
     "stock.valuation.layer and old SVL hooks were removed/reworked."),
    ("procurement_group_removed", "ERROR", (".py", ".xml", ".csv"),
     r"procurement\.group|procurement_group",
     "procurement.group was removed; redesign and data migration may be needed."),
    ("uom_category_tree_rework", "ERROR", (".py", ".xml", ".csv"),
     r"uom\.category|uom_category|category_id[\"'][^\n]{0,80}uom|uom_type|factor_inv",
     "uom.category/uom_type/factor_inv moved to the 19 UoM tree model."),
    ("mobile_phone_consolidation", "WARNING", (".py", ".xml", ".csv"),
     r"\bmobile\b",
     "partner/users/crm mobile references may need phone after 19 consolidation."),
    ("old_init_hook_signature", "ERROR", (".py",),
     r"def\s+(?:post_init_hook|pre_init_hook|uninstall_hook)\(cr,\s*registry\)",
     "init hooks now receive env, not cr, registry."),
    ("lot_single_to_multi", "WARNING", (".py", ".xml"),
     r"\blot_producing_id\b|\bfinished_lot_id\b|\blot_id\b",
     "MRP/quality single-lot fields changed to M2M in 19; review model context."),
    ("reconcile_rule_type", "ERROR", (".xml", ".csv", ".py"),
     r"\brule_type\b|writeoff_button|invoice_matching|writeoff_suggestion",
     "account.reconcile.model rule_type became trigger with mapped values."),
    ("hardcoded_group_db_id", "WARNING", (".py", ".xml", ".csv"),
     r"(?:all_group_ids|groups_id)[^\n]{0,120}\b\d+\b",
     "hardcoded group database IDs break across databases; use XML IDs."),
    ("prod_only_data_delete", "WARNING", (".xml",),
     r"<delete\b",
     "module data deletes may target prod-only XML IDs; move to idempotent migration if needed."),
]


def discover_modules(roots: list[Path]) -> dict[str, Path]:
    out = {}
    for root in roots:
        if root.is_dir() and (root / "__manifest__.py").exists():
            out.setdefault(root.name, root)
            continue
        for child in sorted(root.iterdir()) if root.is_dir() else []:
            if child.is_dir() and (child / "__manifest__.py").exists():
                out.setdefault(child.name, child)
    return out


def iter_files(module: Path, suffixes: tuple[str, ...]):
    for path in sorted(module.rglob("*")):
        if path.is_file() and path.suffix in suffixes and not any(p in SKIP_DIRS for p in path.parts):
            yield path


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def read_manifest(module: Path) -> dict:
    try:
        return ast.literal_eval((module / "__manifest__.py").read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def add_finding(findings: list[dict], module: str, kind: str, severity: str,
                file: str, line: int, detail: str, evidence: str) -> None:
    findings.append({
        "module": module,
        "severity": severity,
        "kind": kind,
        "file": file,
        "line": line,
        "detail": detail,
        "evidence": evidence[:220],
        "detection": "source_sweep regex/import checklist",
    })


def scan_patterns(module_name: str, module: Path) -> list[dict]:
    findings = []
    for kind, severity, suffixes, pattern, detail in PATTERNS:
        rx = re.compile(pattern)
        for path in iter_files(module, suffixes):
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in rx.finditer(text):
                line = line_of(text, match.start())
                evidence = text.splitlines()[line - 1].strip() if text.splitlines() else ""
                add_finding(findings, module_name, kind, severity,
                            str(path.relative_to(module)), line, detail, evidence)
                break
    return findings


def normalize_dep(name: str) -> str:
    return name.lower().replace("_", "-")


def import_roots(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def scan_imports(module_name: str, module: Path, local_modules: set[str]) -> list[dict]:
    data = read_manifest(module)
    declared = {
        normalize_dep(dep)
        for dep in data.get("external_dependencies", {}).get("python", [])
        if isinstance(dep, str)
    }
    findings = []
    for path in iter_files(module, (".py",)):
        for root in sorted(import_roots(path)):
            lower = root.lower()
            dep = normalize_dep(PY_DEP_ALIASES.get(lower, lower))
            if (lower in IGNORE_IMPORT_ROOTS or root in STD_MODULES or root in local_modules
                    or dep in declared or normalize_dep(lower) in declared):
                continue
            add_finding(
                findings, module_name, "undeclared_python_import", "WARNING",
                str(path.relative_to(module)), 1,
                "Python import is not declared in external_dependencies.python; verify before runtime.",
                f"import {root}",
            )
    return findings


def scan(roots: list[Path]) -> dict:
    modules = discover_modules(roots)
    local_names = set(modules)
    findings = []
    for name, path in modules.items():
        findings.extend(scan_patterns(name, path))
        findings.extend(scan_imports(name, path, local_names))
    counts = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return {
        "summary": {"modules": len(modules), "findings": len(findings), **counts},
        "findings": sorted(findings, key=lambda f: (f["module"], f["severity"], f["kind"], f["file"], f["line"])),
        "scope_note": "Checklist only. Confirm each hit against target source/runtime before editing.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--addons-dir", action="append", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("/tmp/odoo-ai/upgrade/_fleet/source_sweep.json"))
    args = ap.parse_args()
    report = scan(args.addons_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"source sweep: {report['summary']['findings']} findings -> {args.out}")
    return 1 if report["summary"].get("ERROR") else 0


if __name__ == "__main__":
    raise SystemExit(main())
