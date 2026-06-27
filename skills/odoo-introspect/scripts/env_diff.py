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

    def _delta(b, t):
        # A failed count is recorded as None by run(); treat it as UNKNOWN, not 0,
        # so the diff never does None - int (TypeError) or reports a false delta.
        if not isinstance(b, int) or not isinstance(t, int):
            return {"base": b, "target": t, "delta": None, "comparable": False}
        return {"base": b, "target": t, "delta": t - b, "comparable": True}

    counts = {
        k: _delta(base_counts.get(k), target_counts.get(k))
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

    "high"  — edition changed, OR a module / Studio field exists on ONE side but
              not the other (EITHER direction). Asymmetry is dangerous both ways:
              code written against a module/field that the deployment target lacks
              fails there just as surely as the reverse.
    "low"   — minor drift: version changes, count/param diffs only.
    "none"  — all captured dimensions match.

    Returns {"verdict": str, "blocking": [str], "severity": "none"|"low"|"high"}.
    """
    edition = diff.get("edition") or {}
    modules = diff.get("modules") or {}
    studio = diff.get("studio_fields") or {}

    edition_changed = edition.get("changed", False)
    mods_target = len(modules.get("only_in_target") or [])   # in target, not coding env
    mods_base = len(modules.get("only_in_base") or [])       # in coding env, not target
    studio_target = len(studio.get("only_in_target") or [])
    studio_base = len(studio.get("only_in_base") or [])

    blocking = []
    if edition_changed:
        blocking.append(f"Edition changed: {edition.get('base')} → {edition.get('target')}")
    if mods_target:
        blocking.append(f"{mods_target} module(s) in target but not coding env")
    if mods_base:
        blocking.append(f"{mods_base} module(s) in coding env but not target "
                        "(code may import/reference what the target lacks)")
    if studio_target:
        blocking.append(f"{studio_target} Studio field(s) in target but not coding env")
    if studio_base:
        blocking.append(f"{studio_base} Studio field(s) in coding env but not target")

    if blocking:
        verdict = ("Coding env diverges from the deployment target ("
                   + "; ".join(blocking) + ") — do NOT claim production safety.")
        return {"verdict": verdict, "blocking": blocking, "severity": "high"}

    # Low: minor drift (versions / counts / config params) OR an UNKNOWN count
    # (a failed collection) — "unknown" must not read as "match" for a gate.
    version_changed = modules.get("version_changed") or []
    counts = diff.get("counts") or {}
    counts_differ = any((v.get("delta") not in (None, 0)) for v in counts.values())
    counts_unknown = any(not v.get("comparable", True) for v in counts.values())
    config_params = diff.get("config_params") or {}
    params_differ = bool(
        (config_params.get("only_in_base") or []) or (config_params.get("only_in_target") or [])
    )

    if version_changed or counts_differ or params_differ or counts_unknown:
        why = ("a count could not be collected (unknown)" if counts_unknown
               else "module versions, counts, or config params differ")
        return {"verdict": f"Minor drift detected: {why}.", "blocking": [], "severity": "low"}

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
# Mutually exclusive: in `odoo-bin shell` (env present) → run(); standalone → main().
# Never both, even if the shell executes stdin with __name__ == "__main__".
if "env" in globals():
    run()
elif __name__ == "__main__":
    main()
