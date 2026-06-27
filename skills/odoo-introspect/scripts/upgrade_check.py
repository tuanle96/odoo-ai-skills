"""
Upgrade harness — detect upgrade-unsafe field changes and scaffold pre-migrate.py.

fields input: {name: {"type":str,"required":bool,"has_default":bool,"store":bool}}

Shell: MODEL=sale.order AGAINST=old_brief.json [MODULE=m] [VERSION=v]
         odoo-bin shell -d <DB> --no-http < upgrade_check.py
Local: python3 upgrade_check.py diff old.json new.json [--module M] [--version V]

Shell output: JSON between ===ODOO_UPG_START=== / ===ODOO_UPG_END===.
Local output: plain json.dumps to stdout.
"""
import os
import sys
import json
import difflib
import argparse
from pathlib import Path

WARNINGS = []


# --- Pure helpers (no Odoo — unit-testable) ----------------------------------

def _name_sim(a, b):
    """Similarity: max of difflib ratio and (shared prefix+suffix) / max-len."""
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    maxlen = max(len(a), len(b)) or 1
    prefix = 0
    for x, y in zip(a, b):
        if x == y:
            prefix += 1
        else:
            break
    suffix = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x == y:
            suffix += 1
        else:
            break
    return max(ratio, (prefix + suffix) / maxlen)


def _reduce_fields(fields_dict):
    """Normalise a Layer-A brief's fields section to the four keys we need."""
    result = {}
    for fname, meta in (fields_dict or {}).items():
        result[fname] = {
            "type": meta.get("type", ""),
            "required": bool(meta.get("required", False)),
            "has_default": bool(meta.get("has_default", False)),
            "store": bool(meta.get("store", True)),
        }
    return result


# Minimum name similarity for a HIGH-confidence rename. Below this, a sole
# same-type candidate is only a LOW (possible) rename and the old field is still
# treated as removed — otherwise a real DROP (legacy_code -> customer_note) would
# masquerade as a rename and suppress the data-loss warning.
_HIGH_SIM = 0.5


def detect_renames(old_fields, new_fields):
    """Heuristic rename: a disappeared + an appeared field of the SAME type.

    Returns [{"old","new","confidence":"high"|"low","similarity","reason"}].
    HIGH requires BOTH a sole same-type candidate AND name similarity >=
    `_HIGH_SIM`; everything else is LOW (a *possible* rename — the caller still
    treats the old field as removed). Only STORED fields are considered: a
    non-stored computed field has no column to rename.
    """
    disappeared = {f: m for f, m in old_fields.items()
                   if f not in new_fields and m.get("store", True)}
    appeared = {f: m for f, m in new_fields.items()
                if f not in old_fields and m.get("store", True)}
    used = set()
    results = []

    for old_f in sorted(disappeared):
        old_type = disappeared[old_f].get("type", "")
        candidates = [f for f, m in appeared.items()
                      if m.get("type", "") == old_type and f not in used]
        if not candidates:
            continue
        best = max(candidates, key=lambda f: _name_sim(old_f, f))
        sim = round(_name_sim(old_f, best), 3)
        if len(candidates) == 1 and sim >= _HIGH_SIM:
            confidence = "high"
            reason = f"sole {old_type!r}-type candidate, name similarity {sim} ≥ {_HIGH_SIM}"
        else:
            confidence = "low"
            reason = (f"sole {old_type!r}-type candidate but low name similarity {sim} < {_HIGH_SIM}"
                      if len(candidates) == 1
                      else f"{len(candidates)} {old_type!r}-type candidates (ambiguous); best similarity {sim}")
        results.append({"old": old_f, "new": best, "confidence": confidence,
                        "similarity": sim, "reason": reason})
        used.add(best)

    return results


def classify_upgrade_risks(old_fields, new_fields, noupdate_xmlids=None):
    """Classify upgrade risks. Returns [{"kind","field"|"xmlid","severity","detail","mitigation"}].

    Kinds: field_removed/field_renamed/new_required_no_default (blocking),
    type_changed/noupdate_protected (warning).
    """
    renames = detect_renames(old_fields, new_fields)
    high_old = {r["old"] for r in renames if r["confidence"] == "high"}
    high_new = {r["new"] for r in renames if r["confidence"] == "high"}
    low_old = {r["old"]: r for r in renames if r["confidence"] == "low"}
    risks = []

    # field_removed: in old, not in new, not a HIGH-confidence rename. A stored
    # field is a real column drop (blocking); a non-stored field has no column
    # (warning, API/view compatibility only). A LOW-confidence same-type match is
    # surfaced as an UNCONFIRMED possible-rename note, but the field is still
    # treated as removed (never silently suppressed).
    for f in sorted(old_fields):
        if f in new_fields or f in high_old:
            continue
        stored = old_fields[f].get("store", True)
        hint = ""
        if f in low_old:
            r = low_old[f]
            hint = (f" — possible (UNCONFIRMED) rename to {r['new']!r} "
                    f"(similarity {r['similarity']}); confirm before treating as a rename")
        risks.append({
            "kind": "field_removed",
            "field": f,
            "severity": "blocking" if stored else "warning",
            "detail": (
                f"Field {f!r} exists in old version but not in new"
                + ("; ORM will drop the column" if stored
                   else "; non-stored field — no column, API/view compatibility only")
                + hint
            ),
            "mitigation": ("pre-migrate: preserve/move data before the column drop"
                           if stored else "update references in views/domains/code"),
        })

    # field_renamed: high-confidence renames only (stored, gated in detect_renames)
    for r in renames:
        if r["confidence"] == "high":
            risks.append({
                "kind": "field_renamed",
                "field": r["old"],
                "severity": "blocking",
                "detail": (
                    f"Field {r['old']!r} appears renamed to {r['new']!r} "
                    f"({r['reason']}); ORM drops old column + creates empty new one"
                ),
                "mitigation": (
                    "pre-migrate: ALTER TABLE ... RENAME COLUMN "
                    "(don't let ORM drop+recreate)"
                ),
            })

    # new_required_no_default: a new STORED required field with no default (a
    # non-stored field has no NOT NULL column to violate). Skip rename targets.
    for f in sorted(new_fields):
        meta = new_fields[f]
        if (f not in old_fields and f not in high_new and meta.get("store", True)
                and meta.get("required") and not meta.get("has_default")):
            risks.append({
                "kind": "new_required_no_default",
                "field": f,
                "severity": "blocking",
                "detail": (
                    f"New field {f!r} is required with no default; "
                    "existing rows will fail the NOT NULL constraint on upgrade"
                ),
                "mitigation": "backfill in pre-migrate before NOT NULL",
            })

    # type_changed: same field name, different type — only meaningful for a real
    # column (both sides stored).
    for f in sorted(old_fields):
        if (f in new_fields and old_fields[f].get("type") != new_fields[f].get("type")
                and old_fields[f].get("store", True) and new_fields[f].get("store", True)):
            risks.append({
                "kind": "type_changed",
                "field": f,
                "severity": "warning",
                "detail": (
                    f"Field {f!r} type changed "
                    f"from {old_fields[f].get('type')!r} "
                    f"to {new_fields[f].get('type')!r}"
                ),
                "mitigation": "pre-migrate: convert existing values before column is retyped",
            })

    # noupdate_protected: xmlids that won't refresh on -u
    for xmlid in (noupdate_xmlids or []):
        risks.append({
            "kind": "noupdate_protected",
            "xmlid": xmlid,
            "severity": "warning",
            "detail": f"noupdate record {xmlid!r} won't update on -u; needs a migration",
            "mitigation": "Write a migration to update this record directly via cr.execute / env",
        })

    return risks


def render_migration_script(module, version, renames, removals):
    """Return a valid Python pre-migrate.py skeleton (ast.parse-clean).

    renames: [{"old","new",...}] (high-confidence only).  removals: [field_name].
    """
    lines = [
        "# Auto-scaffolded pre-migration script",
        f"# Module: {module or 'UNKNOWN'}  Version: {version or 'UNKNOWN'}",
        "# Generated by odoo-ai upgrade-check — review before committing",
        "",
        "",
        "def migrate(cr, version):",
        '    """Pre-migrate: runs BEFORE this module\'s schema is updated."""',
        "    if not version:",
        "        return  # fresh install — migrations do not run on -i",
        "",
    ]
    if renames:
        lines.append("    # --- Field renames: run BEFORE ORM drops old + creates new ---")
        for r in renames:
            old_col, new_col = r["old"], r["new"]
            lines.append(
                f"    # ALTER TABLE <table_name> RENAME COLUMN {old_col} TO {new_col};"
            )
            lines.append(
                f'    # cr.execute("ALTER TABLE <table_name>'
                f' RENAME COLUMN {old_col} TO {new_col}")'
            )
        lines.append("")
    if removals:
        lines.append("    # --- Field removals: preserve data before the column is dropped ---")
        for field in removals:
            lines.append(f"    # TODO: handle removal of field '{field}'")
            lines.append(
                "    #   e.g. copy to another field/model, or archive before ORM drops it"
            )
        lines.append("")
    if not renames and not removals:
        lines.append("    pass  # TODO: add migration steps")
    return "\n".join(lines)


def build_report(old_fields, new_fields, module=None, version=None, noupdate_xmlids=None):
    """Build upgrade-risk report: {"renames","risks","migration_script","summary","_warnings","_caveat"}."""
    renames = detect_renames(old_fields, new_fields)
    risks = classify_upgrade_risks(old_fields, new_fields, noupdate_xmlids)
    high_renames = [r for r in renames if r["confidence"] == "high"]
    removals = [r["field"] for r in risks if r["kind"] == "field_removed"]
    migration_script = render_migration_script(module, version, high_renames, removals)
    blocking = sum(1 for r in risks if r["severity"] == "blocking")
    warning = sum(1 for r in risks if r["severity"] == "warning")
    return {
        "renames": renames,
        "risks": risks,
        "migration_script": migration_script,
        "summary": {"blocking": blocking, "warning": warning},
        "_warnings": [],
        "_caveat": (
            "Rename detection is heuristic (type + name similarity): verify each rename pair. "
            "High-confidence renames are excluded from field_removed and new_required_no_default. "
            "Shell mode builds the new-field snapshot from the live registry; "
            "local diff reads both snapshots from JSON. "
            "noupdate_protected warnings need Layer C (metadata.py) to enumerate xmlids."
        ),
    }


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------

def run():
    MODEL = os.environ.get("MODEL")
    if not MODEL:
        raise SystemExit("Set MODEL, e.g. MODEL=sale.order")
    AGAINST = os.environ.get("AGAINST")
    if not AGAINST:
        raise SystemExit("Set AGAINST to the path of an old model brief JSON")
    MODULE = os.environ.get("MODULE")
    VERSION = os.environ.get("VERSION")

    try:
        old_brief = json.loads(Path(AGAINST).read_text())
    except Exception as exc:
        raise SystemExit(f"Cannot load AGAINST={AGAINST!r}: {exc}") from exc
    old_fields = _reduce_fields(old_brief.get("fields", {}))

    try:
        model_obj = env[MODEL]  # noqa: F821
    except KeyError:
        raise SystemExit(f"Model {MODEL!r} not found in registry")

    new_fields = {}
    for fname, field in model_obj._fields.items():
        new_fields[fname] = {
            "type": field.type,
            "required": bool(getattr(field, "required", False)),
            "has_default": field.default is not None,
            "store": bool(getattr(field, "store", True)),
        }

    noupdate_xmlids = []
    try:
        imd = env["ir.model.data"].search(  # noqa: F821
            [("model", "=", MODEL), ("noupdate", "=", True)]
        )
        noupdate_xmlids = [f"{r.module}.{r.name}" for r in imd]
    except Exception as exc:
        WARNINGS.append(f"noupdate lookup failed ({type(exc).__name__}: {exc})")

    report = build_report(old_fields, new_fields, module=MODULE, version=VERSION,
                          noupdate_xmlids=noupdate_xmlids)
    report["_warnings"] = WARNINGS

    payload = json.dumps(report, indent=2, default=str)
    print("===ODOO_UPG_START===")
    print(payload)
    print("===ODOO_UPG_END===")


# --- Local mode (direct Python invocation) -----------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="upgrade_check.py",
        description="Upgrade risk diff between two model field snapshots",
    )
    sub = parser.add_subparsers(dest="cmd")
    diff_p = sub.add_parser("diff", help="Compare old and new brief JSON files")
    diff_p.add_argument("old_brief", metavar="old_brief.json", help="Old model brief JSON")
    diff_p.add_argument("new_brief", metavar="new_brief.json", help="New model brief JSON")
    diff_p.add_argument("--module", default=None, help="Module name for migration header")
    diff_p.add_argument("--version", default=None, help="Version for the migration folder name")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.cmd != "diff":
        parser.print_help()
        sys.exit(1)

    old_data = json.loads(Path(args.old_brief).read_text())
    new_data = json.loads(Path(args.new_brief).read_text())
    old_fields = _reduce_fields(old_data.get("fields", {}))
    new_fields = _reduce_fields(new_data.get("fields", {}))
    report = build_report(old_fields, new_fields, module=args.module, version=args.version)
    print(json.dumps(report, indent=2, default=str))


# --- Entry-point guards (mutually exclusive: shell → run(), CLI → main()) -----
# `elif` so the shell never also runs main() when stdin is exec'd as __main__.
if "env" in globals():
    run()
elif __name__ == "__main__":
    main()
