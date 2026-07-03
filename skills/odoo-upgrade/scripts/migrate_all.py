#!/usr/bin/env python3
"""migrate_all.py — Fleet-level migration orchestrator for a whole custom-addons tree.

Runs the per-module semantic brief (upgrade_brief.py) over EVERY module found in
the given addons dir(s), topologically sorts them by their `depends` (so you port
foundations before the modules that build on them), and writes one aggregated
`fleet.json` with per-module severity counts and an effort grade:

  S  warnings only — likely mechanical review
  M  1–4 errors — targeted fixes
  L  ≥5 errors or any removed-model reference — rewrite work

HONESTY CONTRACT
----------------
This aggregates static briefs; every per-module caveat of upgrade_brief.py applies
(text-match warnings, manifest coverage). The topo order only considers edges
BETWEEN the scanned custom modules; external depends (base, sale...) are assumed
present on the target. `--verify` proves installability on the target runtime per
module, in order — a fleet is "migrated" only when every module verdict is `ok`
AND its tests pass, never because this script ran.

Usage:
  python3 migrate_all.py --addons-dir ~/custom-addons [--addons-dir more/] \
      --manifest references/manifest_18_19.json \
      [--out /tmp/odoo-ai/upgrade/_fleet] \
      [--verify --docker-compose docker/docker-compose.verify.yml --db verify19]

Stdlib only. License: MIT (part of the odoo-upgrade skill).
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #

def discover_modules(roots: list[Path]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for root in roots:
        for child in sorted(root.iterdir()) if root.is_dir() else []:
            if child.is_dir() and (child / "__manifest__.py").exists():
                out.setdefault(child.name, child)
    return out


def read_depends(module_path: Path) -> list[str]:
    try:
        data = ast.literal_eval(
            (module_path / "__manifest__.py").read_text(encoding="utf-8", errors="replace"))
        return [d for d in data.get("depends", []) if isinstance(d, str)]
    except Exception:
        return []


def topo_order(depends: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Kahn's algorithm over custom-only edges. Returns (order, cyclic_leftover)."""
    names = set(depends)
    indeg = {n: 0 for n in names}
    rev: dict[str, list[str]] = {n: [] for n in names}
    for n, deps in depends.items():
        for d in deps:
            if d in names and d != n:
                indeg[n] += 1
                rev[d].append(n)
    queue = sorted(n for n, k in indeg.items() if k == 0)
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in sorted(rev[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
        queue.sort()
    cyclic = sorted(names - set(order))
    return order + cyclic, cyclic


def portable_set(depends: dict[str, list[str]], installed: set[str] | None,
                 exclude: set[str]) -> tuple[list[str], set[str]]:
    """The modules worth porting: optionally only those installed in the
    production db, minus `exclude` and everything that (transitively) depends
    on an excluded module. Returns (sorted portable names, full excluded set).

    Field lesson: scope by production state FIRST — on a real fleet, 18 of 89
    modules were dead (uninstalled) and one of them was the hardest 'fix'."""
    excl, changed = set(exclude), True
    while changed:
        changed = False
        for name, deps in depends.items():
            if name not in excl and any(d in excl for d in deps):
                excl.add(name)
                changed = True
    names = set(depends) - excl
    if installed is not None:
        names &= installed
    return sorted(names), excl


def effort_grade(brief: dict) -> str:
    s = brief["summary"]
    if s["ERROR"] == 0:
        return "S"
    has_removed_model = any(
        f["kind"] in ("removed_model", "removed_module_dependency")
        for f in brief["findings"] if f["severity"] == "ERROR")
    return "L" if (s["ERROR"] >= 5 or has_removed_model) else "M"


def summarize(name: str, path: Path, brief: dict) -> dict:
    return {
        "path": str(path),
        "errors": brief["summary"]["ERROR"],
        "warnings": brief["summary"]["WARNING"],
        "info": brief["summary"]["INFO"],
        "effort": effort_grade(brief),
        "error_kinds": sorted({f["kind"] for f in brief["findings"]
                               if f["severity"] == "ERROR"}),
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_fleet(roots: list[Path], manifest_path: Path, out_dir: Path,
              verify_args: list[str] | None = None) -> dict:
    upgrade_brief = _load_sibling("upgrade_brief")
    manifest = json.loads(manifest_path.read_text())
    modules = discover_modules(roots)
    depends = {n: read_depends(p) for n, p in modules.items()}
    order, cyclic = topo_order(depends)

    per_module: dict[str, dict] = {}
    for name in order:
        brief = upgrade_brief.build_brief(modules[name], manifest)
        mdir = out_dir / name
        mdir.mkdir(parents=True, exist_ok=True)
        (mdir / "brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False))
        per_module[name] = summarize(name, modules[name], brief)

    if verify_args is not None:
        for name in order:
            cmd = [sys.executable, str(HERE / "upgrade_verify.py"),
                   "--module", name, "--out", str(out_dir / name), *verify_args]
            rc = subprocess.run(cmd).returncode
            vj = out_dir / name / "verify.json"
            per_module[name]["verify"] = (
                json.loads(vj.read_text())["verdict"] if vj.exists() else f"rc={rc}")

    totals = {
        "modules": len(order),
        "errors": sum(m["errors"] for m in per_module.values()),
        "warnings": sum(m["warnings"] for m in per_module.values()),
        "effort": {g: sum(1 for m in per_module.values() if m["effort"] == g)
                   for g in ("S", "M", "L")},
    }
    fleet = {
        "meta": {
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "manifest_label": manifest.get("meta", {}).get("label", "?"),
            "addons_roots": [str(r) for r in roots],
            "scope_note": ("Static aggregation of per-module briefs; port in `order`, "
                           "then prove each module with upgrade_verify.py — see SKILL.md."),
            "cyclic_depends": cyclic,
        },
        "order": order,
        "totals": totals,
        "modules": per_module,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "fleet.json").write_text(json.dumps(fleet, indent=2, ensure_ascii=False))
    return fleet


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--addons-dir", action="append", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("/tmp/odoo-ai/upgrade/_fleet"))
    ap.add_argument("--installed-file", type=Path,
                    help="only count modules listed here as portable (comma/newline "
                         "separated) — feed it the installed-module names from the "
                         "PRODUCTION db: SELECT name FROM ir_module_module WHERE state='installed'")
    ap.add_argument("--exclude",
                    help="comma-separated modules to keep OUT of the portable set "
                         "(enterprise-dependent, quarantined-for-redesign); their "
                         "dependents are excluded transitively")
    ap.add_argument("--verify", action="store_true",
                    help="also run upgrade_verify.py per module, in topo order")
    ap.add_argument("--db", default="verify19")
    ap.add_argument("--docker-compose")
    ap.add_argument("--odoo-bin")
    ap.add_argument("--addons-path")
    args = ap.parse_args()

    verify_args = None
    if args.verify:
        verify_args = ["--db", args.db]
        for flag, val in (("--docker-compose", args.docker_compose),
                          ("--odoo-bin", args.odoo_bin),
                          ("--addons-path", args.addons_path)):
            if val:
                verify_args += [flag, val]

    fleet = run_fleet(args.addons_dir, args.manifest, args.out, verify_args)

    if args.installed_file or args.exclude:
        modules = discover_modules(args.addons_dir)
        depends = {n: read_depends(p) for n, p in modules.items()}
        installed = None
        if args.installed_file:
            installed = {m.strip() for m in re.split(r"[,\n]", args.installed_file.read_text())
                         if m.strip()}
        exclude = {m.strip() for m in (args.exclude or "").split(",") if m.strip()}
        portable, excl = portable_set(depends, installed, exclude)
        (args.out / "install_set.txt").write_text(",".join(portable))
        dropped = sorted(excl & set(depends))
        print(f"portable set: {len(portable)}/{len(depends)} -> {args.out}/install_set.txt")
        if dropped:
            print(f"  excluded (incl. transitive dependents): {', '.join(dropped)}")
        dead = sorted(set(depends) - set(portable) - excl) if installed is not None else []
        if dead:
            print(f"  not installed in prod (skip porting): {', '.join(dead)}")

    t = fleet["totals"]
    print(f"fleet: {t['modules']} modules | errors={t['errors']} warnings={t['warnings']} "
          f"| effort S={t['effort']['S']} M={t['effort']['M']} L={t['effort']['L']}")
    if fleet["meta"]["cyclic_depends"]:
        print(f"  ! cyclic depends (appended last): {fleet['meta']['cyclic_depends']}")
    for name in fleet["order"]:
        m = fleet["modules"][name]
        flag = " <-- port first" if m["effort"] == "L" else ""
        v = f" verify={m['verify']}" if "verify" in m else ""
        print(f"  [{m['effort']}] {name}: E={m['errors']} W={m['warnings']}{v}{flag}")
    print(f"wrote {args.out}/fleet.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
