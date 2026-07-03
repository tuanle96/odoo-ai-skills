"""
Instance Dossier v0 — run INSIDE `odoo-bin shell`.

The read-only, one-command takeover / pre-sales due-diligence artifact: "what
the hell is actually in this Odoo instance?" Walks the live registry and inventories
every dimension a consultant checks before quoting a migration or takeover —
installed modules (standard / OCA / custom), Studio footprint, manual fields,
server actions, automations, crons, security groups + record rules, custom view
overrides, data volumes, integration config surface (KEYS ONLY), multi-company
shape — then derives upgrade-risk flags. It NEVER exports field VALUES or user
data; only schema/config metadata and counts leave the box (redaction applied).

Every section is guarded: a failing section appends to _warnings and the dossier
is emitted partial rather than crashing.

Usage
-----
    # whole-instance dossier to stdout (piped to odoo-bin shell):
    cat dossier.py | odoo-bin shell -d mydb --no-http
    # write JSON to a file and keep source locals (trusted dev box):
    OUT=/tmp/dossier.json DOSSIER_REDACT=local  cat dossier.py | odoo-bin shell -d mydb

Config (env):
    OUT             (opt)  write JSON to this path instead of stdout sentinels
    DOSSIER_REDACT  (opt)  external (default, safe to share) | local (dev box)
    SCRIPTS_DIR     (set by the CLI) absolute scripts dir so `redaction` imports

Output: pure JSON wrapped in ===ODOO_DOSSIER_START=== / ===ODOO_DOSSIER_END===.
"""
import os
import re
import sys
import json
from datetime import datetime

# --------------------------------------------------------------------------- #
# Caps + scope constants
# --------------------------------------------------------------------------- #
MODULES_CAP = 400
CUSTOM_FIELDS_CAP = 200
SERVER_ACTIONS_CAP = 100
CRONS_CAP = 100
RULES_CAP = 200
VIEW_OVERRIDES_CAP = 50
CONFIG_KEYS_CAP = 50
DOMAIN_CAP = 200          # chars of a raw record-rule domain kept
MANUAL_FIELD_TOPN = 10

DATA_VOLUME_MODELS = [
    "res.partner", "res.users", "sale.order", "purchase.order", "account.move",
    "stock.move", "stock.picking", "product.template", "mrp.production",
    "crm.lead", "project.task",
]

# ir.config_parameter KEYS worth surfacing (existence only — never the value).
INTEGRATION_KEY_RE = re.compile(
    r"(?i)(mail|smtp|webhook|api|oauth|token|secret|endpoint|url|integration"
    r"|sync|external|remote|client_id|callback|connector|gateway|payment)")

WARNINGS = []


# --------------------------------------------------------------------------- #
# Pure helpers (no Odoo — unit-testable)
# --------------------------------------------------------------------------- #
def classify_module_author(author):
    """Classify a module by its manifest ``author`` string.

    ``standard`` – authored by Odoo S.A. (community core + enterprise),
    ``oca``      – Odoo Community Association,
    ``custom``   – anything else (client / partner / unknown).
    """
    a = (author or "").lower()
    if "odoo s.a." in a or "odoo sa" in a:
        return "standard"
    if "odoo community association" in a:
        return "oca"
    return "custom"


def is_integration_config_key(key):
    """True when a config-parameter KEY looks integration/secret-ish.

    Used to surface which external hooks exist — never the value behind them.
    """
    return bool(INTEGRATION_KEY_RE.search(key or ""))


def domain_references_company(domain):
    """True when a record-rule domain string references ``company_id``
    (a strong signal the rule enforces multi-company isolation)."""
    return "company_id" in (domain or "")


def risk_flags(dossier):
    """Derive upgrade-risk flags from an assembled dossier dict — PURE.

    Reads every input defensively (partial dossiers are fine), so it is fully
    unit-testable. Each flag: ``{flag, severity, detail}`` with severity in
    ``info`` | ``warn`` | ``high``.
    """
    flags = []

    def add(cond, flag, severity, detail):
        if cond:
            flags.append({"flag": flag, "severity": severity, "detail": detail})

    studio = dossier.get("studio_footprint") or {}
    manual = studio.get("manual_field_count") or 0
    x_studio = studio.get("x_studio_field_count") or 0
    add(bool(studio.get("web_studio_installed")) or x_studio > 0,
        "studio_present", "warn",
        f"Odoo Studio customizations detected ({x_studio} x_studio fields) — "
        "Studio changes are DB-stored and easy to lose track of on upgrade.")
    add(manual > 20, "manual_fields_high", "warn",
        f"{manual} manual (non-code) fields — schema drift not tracked in git.")

    custom = dossier.get("custom_summary") or {}
    ccount = custom.get("custom") or 0
    add(ccount > 10, "many_custom_modules", "high",
        f"{ccount} custom modules — each needs a manual upgrade + review pass.")

    vo = dossier.get("view_overrides") or {}
    vocount = vo.get("count") or 0
    add(vocount > 30, "many_view_overrides", "high",
        f"{vocount} custom view overrides — high template-conflict risk on upgrade.")

    autos = dossier.get("automations") or []
    active_autos = sum(1 for a in autos if a and a.get("active"))
    add(active_autos > 10, "many_active_automations", "warn",
        f"{active_autos} active automated actions — side effects fire during migration.")

    sactions = dossier.get("server_actions") or []
    custom_sa = sum(1 for a in sactions if a and a.get("custom"))
    add(custom_sa > 10, "many_custom_server_actions", "warn",
        f"{custom_sa} custom server actions — bespoke logic outside code review.")

    mc = dossier.get("multi_company") or {}
    sec = dossier.get("security") or {}
    company_count = mc.get("company_count") or 0
    custom_groups = sec.get("custom_groups") or 0
    add(company_count > 1 and custom_groups > 0,
        "multi_company_with_custom_rules", "high",
        f"{company_count} companies with {custom_groups} custom security groups — "
        "verify record-rule isolation before touching anything.")
    return flags


def _rg_count(group):
    """Extract the record count from a read_group result row, version-robust."""
    if "__count" in group:
        return group["__count"]
    for k, v in group.items():
        if k.endswith("_count") and isinstance(v, int):
            return v
    return 0


# --------------------------------------------------------------------------- #
# Redaction import (trusted-SCRIPTS_DIR pattern — same as esg_sample.py)
# --------------------------------------------------------------------------- #
_SD = os.environ.get("SCRIPTS_DIR")
if _SD and os.path.isabs(_SD) and os.path.isfile(os.path.join(_SD, "redaction.py")):
    if _SD not in sys.path:
        sys.path.insert(0, _SD)
elif _SD:
    sys.stderr.write(f"dossier: ignoring untrusted SCRIPTS_DIR={_SD!r} "
                     "(must be an absolute path with a sibling redaction.py)\n")
try:
    from redaction import redact_payload
except Exception:  # noqa: BLE001 — keep import-safe for unit tests
    redact_payload = None


_CAVEAT = (
    "Read-only inventory. No field VALUES were exported — only schema/config "
    "metadata, names and counts. Record-rule domains are capped and PII-masked; "
    "config-parameter values are NEVER read (keys only). Sampled counts reflect the "
    "shell user's visibility. Verify before acting on any figure."
)


# --------------------------------------------------------------------------- #
# Env-dependent work (runs only inside odoo-bin shell)
# --------------------------------------------------------------------------- #
def run():
    OUT = os.environ.get("OUT")
    mode = os.environ.get("DOSSIER_REDACT", "external")
    if mode not in ("external", "local"):
        mode = "external"

    # Author-class map built once, reused by several sections.
    module_class = _module_class_map()
    custom_modules = {n for n, k in module_class.items() if k == "custom"}
    xmlid_module = _xmlid_module_map()  # {(model, res_id): module}

    result = {}
    result["meta"] = _section("meta", _collect_meta)
    result["installed_modules"] = _section(
        "installed_modules", lambda: _collect_modules(module_class))
    result["custom_summary"] = _section(
        "custom_summary", lambda: _collect_custom_summary(module_class))
    result["studio_footprint"] = _section("studio_footprint", _collect_studio)
    result["custom_fields"] = _section("custom_fields", _collect_custom_fields)
    result["server_actions"] = _section(
        "server_actions", lambda: _collect_server_actions(xmlid_module, custom_modules))
    result["automations"] = _section("automations", _collect_automations)
    result["crons"] = _section("crons", _collect_crons)
    result["security"] = _section(
        "security", lambda: _collect_security(xmlid_module, custom_modules))
    result["view_overrides"] = _section(
        "view_overrides", lambda: _collect_view_overrides(custom_modules))
    result["data_volumes"] = _section("data_volumes", _collect_data_volumes)
    result["config_surface"] = _section("config_surface", _collect_config_surface)
    result["multi_company"] = _section("multi_company", _collect_multi_company)

    result["upgrade_risk_flags"] = risk_flags(result)

    if redact_payload is None:
        WARNINGS.append("redaction module not importable — emitting UNREDACTED; "
                        "set SCRIPTS_DIR to the scripts dir before sharing output.")
    result["_caveat"] = _CAVEAT
    result["_warnings"] = WARNINGS

    if redact_payload is not None:
        result = redact_payload(result, mode=mode)
    _emit(result, OUT)


def _section(name, fn):
    """Run a collector, trapping any failure into _warnings (partial dossier)."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        WARNINGS.append(f"{name}: {type(e).__name__}: {e}")
        return None


# --- registry-wide helper maps ----------------------------------------------
def _module_class_map():
    mods = env["ir.module.module"].sudo().search_read(  # noqa: F821
        [("state", "=", "installed")], ["name", "author"])
    return {m["name"]: classify_module_author(m.get("author")) for m in mods}


def _xmlid_module_map():
    """(model, res_id) -> module, for the models we flag customness on."""
    out = {}
    rows = env["ir.model.data"].sudo().search_read(  # noqa: F821
        [("model", "in", ["ir.ui.view", "res.groups", "ir.actions.server"])],
        ["model", "res_id", "module"])
    for r in rows:
        out[(r["model"], r["res_id"])] = r["module"]
    return out


# --- per-section collectors -------------------------------------------------
def _collect_meta():
    version = None
    try:
        import odoo
        version = odoo.release.version
    except Exception:  # noqa: BLE001
        pass
    companies = env["res.company"].sudo().search([])  # noqa: F821
    users = env["res.users"].sudo().search_count(  # noqa: F821
        [("active", "=", True), ("share", "=", False)])
    return {
        "db_name": env.cr.dbname,  # noqa: F821
        "odoo_version": version,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_count": len(companies),
        "internal_user_count": users,
        "db_uuid": env["ir.config_parameter"].sudo().get_param("database.uuid"),  # noqa: F821
    }


def _collect_modules(module_class):
    mods = env["ir.module.module"].sudo().search_read(  # noqa: F821
        [("state", "=", "installed")],
        ["name", "shortdesc", "installed_version", "author", "category_id"],
        limit=MODULES_CAP)
    out = []
    for m in mods:
        cat = m.get("category_id")
        out.append({
            "name": m.get("name"),
            "shortdesc": m.get("shortdesc"),
            "installed_version": m.get("installed_version"),
            "author": m.get("author"),
            "category": cat[1] if isinstance(cat, (list, tuple)) and len(cat) > 1 else None,
            "klass": module_class.get(m.get("name"), "custom"),
        })
    total = env["ir.module.module"].sudo().search_count([("state", "=", "installed")])  # noqa: F821
    return {"total": total, "shown": len(out), "modules": out}


def _collect_custom_summary(module_class):
    counts = {"standard": 0, "oca": 0, "custom": 0}
    custom_names = []
    for name, klass in module_class.items():
        counts[klass] = counts.get(klass, 0) + 1
        if klass == "custom":
            custom_names.append(name)
    return {**counts, "custom_modules": sorted(custom_names)}


def _collect_studio():
    Field = env["ir.model.fields"].sudo()  # noqa: F821
    web_studio = env["ir.module.module"].sudo().search_count(  # noqa: F821
        [("name", "=", "web_studio"), ("state", "=", "installed")]) > 0
    studio_views = env["ir.ui.view"].sudo().search_count(  # noqa: F821
        ["|", ("name", "ilike", "studio"), ("key", "ilike", "studio")])
    manual = Field.search_count([("state", "=", "manual")])
    x_studio = Field.search_count([("name", "=like", "x_studio_%")])
    per_model = []
    try:
        groups = Field.read_group([("state", "=", "manual")], ["model"], ["model"])
        rows = sorted(((g.get("model"), _rg_count(g)) for g in groups),
                      key=lambda kv: -(kv[1] or 0))[:MANUAL_FIELD_TOPN]
        per_model = [{"model": m, "count": c} for m, c in rows if m]
    except Exception as e:  # noqa: BLE001
        WARNINGS.append(f"studio_footprint.per_model: {type(e).__name__}: {e}")
    return {
        "web_studio_installed": web_studio,
        "studio_view_count": studio_views,
        "manual_field_count": manual,
        "x_studio_field_count": x_studio,
        "per_model_manual": per_model,
    }


def _collect_custom_fields():
    rows = env["ir.model.fields"].sudo().search_read(  # noqa: F821
        [("state", "=", "manual")], ["model", "name", "ttype", "relation"],
        limit=CUSTOM_FIELDS_CAP)
    return [{"model": r.get("model"), "name": r.get("name"),
             "ttype": r.get("ttype"), "relation": r.get("relation") or None}
            for r in rows]


def _collect_server_actions(xmlid_module, custom_modules):
    acts = env["ir.actions.server"].sudo().search([], limit=SERVER_ACTIONS_CAP)  # noqa: F821
    out = []
    for a in acts:
        module = xmlid_module.get(("ir.actions.server", a.id))
        out.append({
            "id": a.id,
            "name": a.name,
            "model": a.model_id.model if a.model_id else None,
            "state": a.state,
            "usage": getattr(a, "usage", None),
            "custom": (module is None) or (module in custom_modules),
        })
    return out


def _collect_automations():
    if "base.automation" not in env:  # noqa: F821
        return None
    recs = env["base.automation"].sudo().search([], limit=SERVER_ACTIONS_CAP)  # noqa: F821
    out = []
    for r in recs:
        out.append({
            "name": r.name,
            "model": r.model_id.model if r.model_id else None,
            "trigger": getattr(r, "trigger", None),
            "active": bool(r.active),
        })
    return out


def _collect_crons():
    recs = env["ir.cron"].sudo().search([], limit=CRONS_CAP)  # noqa: F821
    out = []
    for c in recs:
        out.append({
            "name": c.name,
            "model": c.model_id.model if c.model_id else None,
            "interval_number": c.interval_number,
            "interval_type": c.interval_type,
            "active": bool(c.active),
            "user": c.user_id.login if c.user_id else None,
        })
    return out


def _collect_security(xmlid_module, custom_modules):
    Groups = env["res.groups"].sudo()  # noqa: F821
    groups_total = Groups.search_count([])
    custom_groups = 0
    for g in Groups.search([]):
        module = xmlid_module.get(("res.groups", g.id))
        if module is None or module in custom_modules:
            custom_groups += 1

    Rule = env["ir.rule"].sudo()  # noqa: F821
    rules_total = Rule.search_count([])
    rule_rows = []
    mc_rules = 0
    for r in Rule.search([], limit=RULES_CAP):
        dom = r.domain_force or ""
        is_mc = domain_references_company(dom)
        if is_mc:
            mc_rules += 1
        rule_rows.append({
            "model": r.model_id.model if r.model_id else None,
            "name": r.name,
            "is_global": bool(r["global"]),
            "groups": [g.full_name or g.name for g in r.groups][:10],
            "domain": dom[:DOMAIN_CAP],
            "multi_company": is_mc,
        })
    try:
        models_with_rules = len(Rule.read_group([], ["model_id"], ["model_id"]))
    except Exception:  # noqa: BLE001
        models_with_rules = len({row["model"] for row in rule_rows if row["model"]})
    return {
        "groups_total": groups_total,
        "custom_groups": custom_groups,
        "rules_total": rules_total,
        "models_with_rules": models_with_rules,
        "multi_company_rules": mc_rules,
        "record_rules": rule_rows,
    }


def _collect_view_overrides(custom_modules):
    if not custom_modules:
        return {"count": 0, "views": []}
    imd = env["ir.model.data"].sudo().search_read(  # noqa: F821
        [("model", "=", "ir.ui.view"), ("module", "in", list(custom_modules))],
        ["res_id"])
    ids = [d["res_id"] for d in imd]
    views = env["ir.ui.view"].sudo().browse(ids).exists()  # noqa: F821
    overrides = [v for v in views if v.inherit_id]
    top = [{"model": v.model, "name": v.name,
            "inherit_of": v.inherit_id.name if v.inherit_id else None}
           for v in overrides[:VIEW_OVERRIDES_CAP]]
    return {"count": len(overrides), "views": top}


def _collect_data_volumes():
    out = {}
    for model in DATA_VOLUME_MODELS:
        if model in env:  # noqa: F821
            try:
                out[model] = env[model].sudo().search_count([])  # noqa: F821
            except Exception as e:  # noqa: BLE001
                WARNINGS.append(f"data_volumes.{model}: {type(e).__name__}: {e}")
    return out


def _collect_config_surface():
    params = env["ir.config_parameter"].sudo().search_read([], ["key"])  # noqa: F821
    keys = sorted(p["key"] for p in params if is_integration_config_key(p.get("key")))
    mail_servers = env["ir.mail_server"].sudo().search_count([])  # noqa: F821
    return {
        "integration_keys": keys[:CONFIG_KEYS_CAP],
        "integration_keys_total": len(keys),
        "outgoing_mail_servers": mail_servers,
    }


def _collect_multi_company():
    companies = env["res.company"].sudo().search([])  # noqa: F821
    with_company_field = 0
    for model in DATA_VOLUME_MODELS:
        if model in env and "company_id" in env[model]._fields:  # noqa: F821
            with_company_field += 1
    return {
        "company_count": len(companies),
        "company_names": companies.mapped("name"),
        "models_with_company_field": with_company_field,
    }


def _emit(result, OUT):
    payload = json.dumps(result, indent=2, default=str)
    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}")
    else:
        print("===ODOO_DOSSIER_START===")
        print(payload)
        print("===ODOO_DOSSIER_END===")


if "env" in globals():  # `env` injected by odoo-bin shell
    run()  # noqa: F821
