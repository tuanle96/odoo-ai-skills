"""
Odoo module preflight — run INSIDE `odoo-bin shell`.

Answers "is the thing I edited actually the thing running?" — the loop that
wastes hours when an agent edits code that the instance never loads:

  - effective addons_path (and DUPLICATE/shadowing paths — the auto-injected
    ~/.local/share/Odoo/addons/<series> trap)
  - is the module installed / which state (and its installed version)
  - where on disk the running module is loaded FROM (so you can confirm it's the
    folder you're editing, not a stale copy under a different addons path)
  - did its registry models actually build
  - whether the DB has demo data (changes what tests/records exist)

When "my change didn't apply": the cause is usually not the code — it's -u not
run, the file not imported in __init__, a shadow copy on another addons path, or
the wrong DB. This surfaces all of those at once.

Pure helpers (shadow_paths, parse_addons_path) are module-level and
unit-testable; run() executes only when `env` is present.

Usage
-----
    MODULE=my_module odoo-bin shell -d <DB> --no-http < preflight.py
    # no MODULE → reports environment only (addons_path, demo, db)

Output: pure JSON wrapped in ===ODOO_PREFLIGHT_START=== / ===ODOO_PREFLIGHT_END===.
"""
import os
import json

WARNINGS = []


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def parse_addons_path(raw):
    """odoo.conf addons_path is a comma list; normalize to a clean list."""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def shadow_paths(paths):
    """Flag addons-path entries that commonly shadow an edited module:
    duplicates, and the auto-injected user data-dir addons path. Returns a list
    of {path, reason}. A module present on two paths loads from whichever wins
    by order — edits to the other copy do nothing, silently."""
    flags = []
    seen = {}
    for p in paths:
        norm = p.rstrip("/")
        if norm in seen:
            flags.append({"path": p, "reason": "duplicate addons_path entry"})
        seen[norm] = True
        # the data-dir addons path Odoo injects automatically
        if "/.local/share/Odoo/addons/" in norm or norm.endswith("/Odoo/addons"):
            flags.append({"path": p, "reason": "auto-injected user data-dir addons path "
                          "(can shadow your edited module)"})
    return flags


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODULE = os.environ.get("MODULE")

    def safe(label, fn, default=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"{label} failed ({type(e).__name__}: {e})")
            return default

    cfg = safe("config read", lambda: __import__("odoo").tools.config, {})
    raw_addons = ""
    try:
        raw_addons = cfg.get("addons_path", "") if hasattr(cfg, "get") else ""
    except Exception:
        pass
    paths = parse_addons_path(raw_addons)

    out = {
        "database": safe("db name", lambda: env.cr.dbname),               # noqa: F821
        "has_demo_data": safe("demo check", lambda: bool(                  # noqa: F821
            env["ir.module.module"].search_count(                          # noqa: F821
                [("demo", "=", True), ("state", "=", "installed")]))),
        "addons_path": paths,
        "shadow_warnings": shadow_paths(paths),
        "_warnings": WARNINGS,
    }

    if MODULE:
        def module_info():
            m = env["ir.module.module"].search([("name", "=", MODULE)], limit=1)  # noqa: F821
            if not m:
                return {"found": False,
                        "hint": "Not in ir.module.module — not on any addons_path, "
                                "or the app list wasn't updated. Check addons_path above "
                                "and run -u base / Update Apps List."}
            return {"found": True, "name": m.name, "state": m.state,
                    "installed_version": m.installed_version,
                    "latest_version": m.latest_version,
                    "auto_install": m.auto_install,
                    "application": m.application}
        info = safe("module lookup", module_info, {"found": None})
        out["module"] = info

        # Where is the module actually loaded from on disk?
        def loaded_from():
            import importlib.util
            spec = importlib.util.find_spec(f"odoo.addons.{MODULE}")
            if spec and spec.submodule_search_locations:
                return list(spec.submodule_search_locations)
            return None
        out["module_loaded_from"] = safe("module path", loaded_from)

        # Did its models build in the registry?
        def module_models():
            recs = env["ir.model"].search([])  # noqa: F821
            names = [r.model for r in recs
                     if MODULE in (r.modules or "").split(", ")]
            return sorted(names)
        out["module_models_in_registry"] = safe("module models", module_models)

        # Actionable verdict.
        verdict = []
        if info.get("found") is False:
            verdict.append("MODULE NOT KNOWN to this DB — fix addons_path / update app list "
                           "before editing code.")
        elif info.get("state") not in (None, "installed"):
            verdict.append(f"state={info.get('state')} — install it (-i) or it won't run.")
        if info.get("found") and info.get("latest_version") and \
                info.get("installed_version") and \
                info["installed_version"] != info["latest_version"]:
            verdict.append("installed_version != latest_version — run -u "
                           f"{MODULE} to apply your code/data changes.")
        if out.get("shadow_warnings"):
            verdict.append("shadowing addons_path entries present — confirm the module "
                           "loads from the folder you are editing (see module_loaded_from).")
        out["verdict"] = verdict or ["module installed and up to date in this DB."]

    payload = json.dumps(out, indent=2, default=str)
    print("===ODOO_PREFLIGHT_START===")
    print(payload)
    print("===ODOO_PREFLIGHT_END===")


if "env" in globals():
    run()
