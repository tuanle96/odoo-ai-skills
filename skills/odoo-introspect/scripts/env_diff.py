"""
Environment parity & drift detector — fingerprint a running Odoo instance and
diff two fingerprints (dev vs prod) so the agent never claims production safety
against a divergent environment.

Pure helpers require no Odoo and are unit-testable. run() executes only inside
`odoo-bin shell` (gated on `env` in globals). main() provides a local CLI for
diffing two saved JSON fingerprint files.

Shell mode:  odoo-bin shell -d <DB> --no-http < env_diff.py
             Output: JSON wrapped in ===ODOO_ENVFP_START=== / ===ODOO_ENVFP_END===.

Local mode:  python3 env_diff.py diff <base.json> <target.json>
             Output: {"diff": ..., "summary": ...} as JSON to stdout.
"""
import os
import sys
import json
from pathlib import Path

WARNINGS = []


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------

def fingerprint_shape():
    """Spec/example dict for a fingerprint. Keys: modules ({name: version}),
    edition ("enterprise"|"community"), counts ({model: int}), studio_fields
    (["model.x_studio_*"]), config_params ([key names only — never values])."""
    return {
        "modules": {"sale": "16.0.1.0.0", "purchase": "16.0.1.0.0"},
        "edition": "enterprise",
        "counts": {"ir.model": 512, "ir.rule": 42, "ir.model.access": 180,
                   "ir.cron": 15, "ir.actions.server": 30},
        "studio_fields": ["sale.order.x_studio_custom_field"],
        "config_params": ["web.base.url", "base.lang"],
    }


def diff_fingerprints(base, target):
    """Compare two fingerprint dicts. Returns structured diff with keys:
    modules (only_in_base, only_in_target, version_changed), counts (delta per key),
    studio_fields (only_in_base/target), config_params (only_in_base/target),
    edition (base, target, changed)."""
    base_mods = base.get("modules") or {}
    target_mods = target.get("modules") or {}
    base_names, target_names = set(base_mods), set(target_mods)
    version_changed = [
        {"name": n, "base": base_mods[n], "target": target_mods[n]}
        for n in sorted(base_names & target_names)
        if base_mods[n] != target_mods[n]
    ]

    base_counts = base.get("counts") or {}
    target_counts = target.get("counts") or {}
    counts = {
        k: {"base": base_counts.get(k, 0), "target": target_counts.get(k, 0),
             "delta": target_counts.get(k, 0) - base_counts.get(k, 0)}
        for k in sorted(set(base_counts) | set(target_counts))
    }

    base_studio = set(base.get("studio_fields") or [])
    target_studio = set(target.get("studio_fields") or [])
    base_params = set(base.get("config_params") or [])
    target_params = set(target.get("config_params") or [])
    base_ed, target_ed = base.get("edition", ""), target.get("edition", "")

    return {
        "modules": {
            "only_in_base": sorted(base_names - target_names),
            "only_in_target": sorted(target_names - base_names),
            "version_changed": version_changed,
        },
        "counts": counts,
        "studio_fields": {
            "only_in_base": sorted(base_studio - target_studio),
            "only_in_target": sorted(target_studio - base_studio),
        },
        "config_params": {
            "only_in_base": sorted(base_params - target_params),
            "only_in_target": sorted(target_params - base_params),
        },
        "edition": {"base": base_ed, "target": target_ed, "changed": base_ed != target_ed},
    }


def summarize_drift(diff):
    """Classify drift severity and produce a blunt verdict.

    "high"  — edition changed, OR target has modules/studio fields dev lacks.
    "low"   — minor drift: version changes, base-only modules, count/param diffs.
    "none"  — all captured dimensions match.

    Returns {"verdict": str, "blocking": [str], "severity": "none"|"low"|"high"}.
    """
    edition = diff.get("edition") or {}
    modules = diff.get("modules") or {}
    studio = diff.get("studio_fields") or {}

    edition_changed = edition.get("changed", False)
    n_extra_mods = len(modules.get("only_in_target") or [])
    n_studio_extra = len(studio.get("only_in_target") or [])

    blocking = []
    if edition_changed:
        blocking.append(f"Edition changed: {edition.get('base')} → {edition.get('target')}")
    if n_extra_mods:
        blocking.append(f"{n_extra_mods} module(s) installed in target but not in coding env")
    if n_studio_extra:
        blocking.append(f"{n_studio_extra} studio field(s) in target but not in coding env")

    if blocking:
        parts = []
        if n_extra_mods:
            parts.append(f"{n_extra_mods} extra module(s)")
        if n_studio_extra:
            parts.append(f"{n_studio_extra} studio field(s)")
        if parts:
            verdict = (f"Coding env diverges from target: {', '.join(parts)} — "
                       "do NOT claim production safety.")
        else:
            verdict = (f"Coding env diverges from target: edition "
                       f"{edition.get('base')} → {edition.get('target')} — "
                       "do NOT claim production safety.")
        return {"verdict": verdict, "blocking": blocking, "severity": "high"}

    # Low: any minor drift
    version_changed = modules.get("version_changed") or []
    only_in_base = modules.get("only_in_base") or []
    counts = diff.get("counts") or {}
    counts_differ = any(abs(v.get("delta", 0)) > 0 for v in counts.values())
    config_params = diff.get("config_params") or {}
    params_differ = bool(
        (config_params.get("only_in_base") or []) or (config_params.get("only_in_target") or [])
    )
    studio_base_only = bool(studio.get("only_in_base") or [])

    if version_changed or only_in_base or counts_differ or params_differ or studio_base_only:
        return {"verdict": "Minor drift detected: module versions or counts differ between environments.",
                "blocking": [], "severity": "low"}

    return {"verdict": "Environments match on captured dimensions.", "blocking": [], "severity": "none"}


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------

def run():
    """Build and print a fingerprint of this Odoo instance."""
    # Edition: web_enterprise installed?
    try:
        has_ent = bool(env["ir.module.module"].sudo().search(  # noqa: F821
            [("name", "=", "web_enterprise"), ("state", "=", "installed")], limit=1))
        edition = "enterprise" if has_ent else "community"
    except Exception as e:
        WARNINGS.append(f"edition detect failed ({type(e).__name__}: {e})")
        edition = "unknown"

    # Installed modules: {name: latest_version}
    modules = {}
    try:
        for r in env["ir.module.module"].sudo().search([("state", "=", "installed")]):  # noqa: F821
            modules[r.name] = r.latest_version or ""
    except Exception as e:
        WARNINGS.append(f"modules scan failed ({type(e).__name__}: {e})")

    # Counts per important model
    counts = {}
    for _m in ("ir.model", "ir.rule", "ir.model.access", "ir.cron", "ir.actions.server"):
        try:
            counts[_m] = env[_m].sudo().search_count([])  # noqa: F821
        except Exception as e:
            WARNINGS.append(f"count failed for {_m} ({type(e).__name__}: {e})")
            counts[_m] = None

    # Studio fields: x_studio_* → "model.field" strings
    studio_fields = []
    try:
        for f in env["ir.model.fields"].sudo().search([("name", "=like", "x_studio_%")]):  # noqa: F821
            m = f.model_id.model if f.model_id else "unknown"
            studio_fields.append(f"{m}.{f.name}")
        studio_fields.sort()
    except Exception as e:
        WARNINGS.append(f"studio fields scan failed ({type(e).__name__}: {e})")

    # Config params: KEY names only (values omitted — may contain secrets)
    config_params = []
    try:
        config_params = sorted(p.key for p in env["ir.config_parameter"].sudo().search([]))  # noqa: F821
    except Exception as e:
        WARNINGS.append(f"config params scan failed ({type(e).__name__}: {e})")

    out = {
        "modules": modules,
        "edition": edition,
        "counts": counts,
        "studio_fields": studio_fields,
        "config_params": config_params,
        "_warnings": WARNINGS,
        "_caveat": (
            "Fingerprint reflects this instance at capture time. "
            "counts['ir.model'] = total registered ORM models. "
            "config_params lists KEY names only — values omitted (may contain secrets). "
            "Save this JSON; diff with `odoo-ai env-diff <base.json> <target.json>`."
        ),
    }
    payload = json.dumps(out, indent=2, default=str)
    print("===ODOO_ENVFP_START===")
    print(payload)
    print("===ODOO_ENVFP_END===")


# --- Local CLI (no Odoo — loads + diffs two saved JSON fingerprints) ---------

def main(argv=None):
    """Usage: python3 env_diff.py diff <base.json> <target.json>
    Prints {"diff": ..., "summary": ...} as JSON to stdout."""
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) < 3 or argv[0] != "diff":
        print("Usage: env_diff.py diff <base.json> <target.json>", file=sys.stderr)
        sys.exit(1)
    base_path, target_path = Path(argv[1]), Path(argv[2])
    try:
        base = json.loads(base_path.read_text())
        target = json.loads(target_path.read_text())
    except Exception as e:
        print(f"Error loading fingerprints: {e}", file=sys.stderr)
        sys.exit(1)
    diff = diff_fingerprints(base, target)
    print(json.dumps({"diff": diff, "summary": summarize_drift(diff)}, indent=2))


# In odoo-bin shell: __name__ != "__main__" and env exists → run().
# Locally: python3 env_diff.py ... → main().
if "env" in globals():
    run()

if __name__ == "__main__":
    main()
