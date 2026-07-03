"""
Compact instance facts for agent context — run INSIDE `odoo-bin shell`.

Small, targeted facts about ONE model (a few KB, not an exhaustive dump) so an
agent can be primed cheaply. Pick a slice with FACT_KIND:
    model    — fields (compacted), inherit chain, method names, module origins
    security — ACL rows (crwu) + record rules (domain, multi-company)
    views    — the model's views (+ effective primary-form arch when ARCH=1)
    flows    — form buttons, window/server actions, automations, crons

Every payload carries an `instance_fingerprint` (db uuid + a hash of installed
modules) so a cached fact can be matched to the instance it came from. These are
CONTEXT facts, not merge-approval evidence — see `snapshot_cache.py`.

The env-dependent work lives in run(); the pure helpers (compact_field,
module_hash, parse_buttons, …) are module-level so they import and unit-test
without Odoo. run() executes only when `env` is present (inside `odoo-bin shell`).

Usage
-----
    FACT_KIND=model    MODEL=sale.order odoo-bin shell -d <DB> --no-http < facts.py
    FACT_KIND=security MODEL=sale.order odoo-bin shell -d <DB> --no-http < facts.py
    FACT_KIND=views ARCH=1 MODEL=sale.order odoo-bin shell -d <DB> --no-http < facts.py
    FACT_KIND=flows    MODEL=sale.order odoo-bin shell -d <DB> --no-http < facts.py
    # write to file instead of stdout:
    FACT_KIND=model MODEL=sale.order OUT=/tmp/facts.json odoo-bin shell -d <DB> < facts.py

Output: pure JSON wrapped in ===ODOO_FACTS_START=== / ===ODOO_FACTS_END===.
"""
import os
import json
import hashlib
import xml.etree.ElementTree as ET

WARNINGS = []

# Auto/technical fields present on every model — noise for an agent, so skipped.
TECHNICAL_FIELDS = {"id", "create_uid", "create_date", "write_uid",
                    "write_date", "display_name", "__last_update"}

_CAVEAT = ("Compact context facts (read-only, non-exhaustive). Not merge-approval "
           "evidence — use a full cold introspection run for that. Selection lists "
           "are keys only; arch/domain strings are length-capped.")


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def module_hash(pairs):
    """Hash installed modules for an instance fingerprint. `pairs` is an iterable
    of (name, latest_version); returns (hexdigest, count). Order-independent."""
    items = sorted(f"{name}:{ver}" for name, ver in pairs)
    digest = hashlib.sha256("\n".join(items).encode("utf-8")).hexdigest()
    return digest, len(items)


def selection_keys(sel):
    """Selection value → list of KEYS only (labels dropped to keep output small).
    A list/tuple of pairs → its keys; a method-name string / callable / None →
    None (can't resolve statically)."""
    if sel is None or isinstance(sel, str) or callable(sel):
        return None
    try:
        keys = [p[0] for p in sel if isinstance(p, (list, tuple)) and p]
    except TypeError:
        return None
    return keys or None


def compact_field(fdata):
    """Compact one field's plain-dict description to the essentials an agent
    needs: type, required, store, compute/related as bools, relation, selection
    keys. `fdata` keys: type, required, store, compute, related, relation,
    selection."""
    out = {
        "type": fdata.get("type"),
        "required": bool(fdata.get("required")),
        "store": bool(fdata.get("store", True)),
        "compute": bool(fdata.get("compute")),
        "related": bool(fdata.get("related")),
    }
    relation = fdata.get("relation")
    if relation:
        out["relation"] = relation
    keys = selection_keys(fdata.get("selection"))
    if keys is not None:
        out["selection"] = keys
    return out


def compact_fields(fields):
    """Map {name: plain-field-dict} to {name: compact}, skipping technical fields."""
    return {name: compact_field(fdata)
            for name, fdata in fields.items() if name not in TECHNICAL_FIELDS}


def acl_perms(row):
    """4-char crwu mask for an ir.model.access row: a letter if the perm is
    granted, '-' otherwise (order: create, read, write, unlink)."""
    order = (("perm_create", "c"), ("perm_read", "r"),
             ("perm_write", "w"), ("perm_unlink", "u"))
    return "".join(letter if row.get(key) else "-" for key, letter in order)


def mentions_company(domain):
    """Does a record-rule domain reference company_id (i.e. is it multi-company)?"""
    return "company_id" in (domain or "")


def cap_str(s, n):
    """Length-cap a string, appending an ellipsis when truncated."""
    s = s or ""
    return s if len(s) <= n else s[:n] + "…"


def module_of_xmlid(xmlid):
    """The declaring module of an external id: the prefix before the first dot."""
    if not xmlid or "." not in xmlid:
        return None
    return xmlid.split(".", 1)[0]


def as_list(value):
    """Normalize _inherit (str | list | None) to a list."""
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def parse_buttons(arch):
    """Extract actionable buttons from a form-view arch string: each named button
    as {name, type, string} (type is 'object' or 'action'). Malformed XML → []."""
    try:
        root = ET.fromstring(arch)
    except ET.ParseError:
        return []
    out = []
    for btn in root.iter("button"):
        name = btn.get("name")
        if not name:
            continue
        out.append({
            "name": name,
            "type": btn.get("type"),
            "string": btn.get("string") or (btn.text or "").strip() or None,
        })
    return out


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    FACT_KIND = os.environ.get("FACT_KIND")
    MODEL = os.environ.get("MODEL")
    ARCH = os.environ.get("ARCH") in ("1", "true", "yes")
    OUT = os.environ.get("OUT")
    KINDS = ("model", "security", "views", "flows")
    if FACT_KIND not in KINDS:
        raise SystemExit(f"Set FACT_KIND to one of {KINDS}, e.g. FACT_KIND=model")
    if not MODEL:
        raise SystemExit("Set MODEL, e.g. MODEL=sale.order")

    def _instance_fingerprint():
        fp = {"db_uuid": None, "module_hash": None, "installed_count": 0}
        try:
            fp["db_uuid"] = env["ir.config_parameter"].sudo().get_param("database.uuid")  # noqa: F821
        except Exception as e:
            WARNINGS.append(f"db_uuid unavailable ({type(e).__name__}: {e})")
        try:
            mods = env["ir.module.module"].sudo().search_read(  # noqa: F821
                [("state", "=", "installed")], ["name", "latest_version"])
            digest, count = module_hash((m["name"], m.get("latest_version") or "")
                                        for m in mods)
            fp["module_hash"], fp["installed_count"] = digest, count
        except Exception as e:
            WARNINGS.append(f"module_hash unavailable ({type(e).__name__}: {e})")
        return fp

    out = {"fact_kind": FACT_KIND, "model": MODEL,
           "instance_fingerprint": _instance_fingerprint()}

    if MODEL not in env:  # noqa: F821
        WARNINGS.append(f"model {MODEL} not in the registry")
        out["_warnings"] = WARNINGS
        out["_caveat"] = _CAVEAT
        return _emit(out, OUT)
    model = env[MODEL]  # noqa: F821

    if FACT_KIND == "model":
        _facts_model(out, model)
    elif FACT_KIND == "security":
        _facts_security(out, MODEL)
    elif FACT_KIND == "views":
        _facts_views(out, model, MODEL, ARCH)
    elif FACT_KIND == "flows":
        _facts_flows(out, model, MODEL)

    out["_warnings"] = WARNINGS
    out["_caveat"] = _CAVEAT
    return _emit(out, OUT)


def _facts_model(out, model):
    def _field_plain(f):
        sel = None
        if f.type == "selection":
            try:
                sel = f._description_selection(env)  # noqa: F821  (resolves method-based)
            except Exception:
                sel = getattr(f, "selection", None)
        return {"type": f.type, "required": bool(f.required), "store": bool(f.store),
                "compute": bool(f.compute), "related": bool(getattr(f, "related", None)),
                "relation": getattr(f, "comodel_name", None), "selection": sel}

    try:
        plain = {name: _field_plain(f) for name, f in model._fields.items()}
        out["fields"] = compact_fields(plain)
        out["field_count"] = len(out["fields"])
    except Exception as e:
        WARNINGS.append(f"field inventory failed ({type(e).__name__}: {e})")
    out["inherit_chain"] = {
        "inherit": as_list(getattr(model, "_inherit", None)),
        "inherits": dict(getattr(model, "_inherits", {})),
    }
    # Public methods declared in an addon (not core ORM), names only, capped.
    try:
        names = set()
        fset = model._fields
        for cls in type(model).__mro__:
            if not (getattr(cls, "__module__", "") or "").startswith("odoo.addons"):
                continue
            for attr, val in vars(cls).items():
                if not attr.startswith("_") and attr not in fset and callable(val):
                    names.add(attr)
        out["method_names"] = sorted(names)[:60]
    except Exception as e:
        WARNINGS.append(f"method_names failed ({type(e).__name__}: {e})")
    # Which module contributed each field (from ir.model.fields.modules).
    try:
        origins = {}
        for r in env["ir.model.fields"].sudo().search_read(  # noqa: F821
                [("model", "=", out["model"])], ["name", "modules"]):
            if r.get("modules"):
                origins[r["name"]] = r["modules"]
        out["module_origins"] = origins
    except Exception as e:
        WARNINGS.append(f"module_origins unavailable ({type(e).__name__}: {e})")


def _facts_security(out, model_name):
    try:
        acls = []
        for r in env["ir.model.access"].sudo().search_read(  # noqa: F821
                [("model_id.model", "=", model_name)],
                ["name", "group_id", "perm_read", "perm_write",
                 "perm_create", "perm_unlink"]):
            acls.append({"name": r["name"],
                         "group": r["group_id"][1] if r.get("group_id") else None,
                         "perms": acl_perms(r)})
        out["acls"] = acls
    except Exception as e:
        WARNINGS.append(f"acls unavailable ({type(e).__name__}: {e})")
    try:
        rules = []
        for r in env["ir.rule"].sudo().search_read(  # noqa: F821
                [("model_id.model", "=", model_name)],
                ["name", "global", "groups", "domain_force"]):
            dom = r.get("domain_force") or ""
            grp_ids = r.get("groups") or []
            try:
                groups = env["res.groups"].browse(grp_ids).mapped("full_name")  # noqa: F821
            except Exception:
                groups = grp_ids
            rules.append({"name": r["name"], "global": bool(r.get("global")),
                          "groups": groups, "domain": cap_str(dom, 200),
                          "multi_company": mentions_company(dom)})
        out["record_rules"] = rules
    except Exception as e:
        WARNINGS.append(f"record_rules unavailable ({type(e).__name__}: {e})")


def _facts_views(out, model, model_name, arch):
    try:
        recs = env["ir.ui.view"].sudo().search([("model", "=", model_name)])  # noqa: F821
        ext = recs.get_external_id()
        views = []
        for v in recs:
            xmlid = ext.get(v.id) or None
            views.append({"xml_id": xmlid, "id": v.id, "type": v.type, "name": v.name,
                          "inherit_id": v.inherit_id.name if v.inherit_id else None,
                          "module": module_of_xmlid(xmlid)})
        out["views"] = views
    except Exception as e:
        WARNINGS.append(f"views unavailable ({type(e).__name__}: {e})")
    if arch:
        arch_str = _primary_form_arch(model)
        if arch_str is not None:
            out["primary_form_arch"] = cap_str(arch_str, 8000)


def _facts_flows(out, model, model_name):
    arch_str = _primary_form_arch(model)
    out["buttons"] = parse_buttons(arch_str) if arch_str else []
    for key, model_key, domain in (
        ("window_actions", "ir.actions.act_window", [("res_model", "=", model_name)]),
        ("server_actions", "ir.actions.server", [("model_id.model", "=", model_name)]),
        ("crons", "ir.cron", [("model_id.model", "=", model_name)]),
    ):
        try:
            fields = ["id", "name", "active"] if key == "crons" else ["id", "name"]
            out[key] = env[model_key].sudo().search_read(domain, fields)  # noqa: F821
        except Exception as e:
            WARNINGS.append(f"{key} unavailable ({type(e).__name__}: {e})")
    if "base.automation" in env:  # noqa: F821  (only if base_automation installed)
        try:
            out["automations"] = env["base.automation"].sudo().search_read(  # noqa: F821
                [("model_id.model", "=", model_name)], ["name", "trigger", "active"])
        except Exception as e:
            WARNINGS.append(f"automations unavailable ({type(e).__name__}: {e})")
    else:
        WARNINGS.append("base.automation not installed — automations skipped")


def _primary_form_arch(model):
    """Effective arch of the model's primary form view (get_view on 17+, else
    fields_view_get). Returns the arch string, or None on failure (warned)."""
    try:
        if hasattr(model, "get_view"):
            return model.get_view(view_type="form").get("arch")
        return model.fields_view_get(view_type="form").get("arch")  # <17.0
    except Exception as e:
        WARNINGS.append(f"primary form arch unavailable ({type(e).__name__}: {e})")
        return None


def _emit(out, out_path):
    payload = json.dumps(out, indent=2, default=str)
    if out_path:
        with open(out_path, "w") as fh:
            fh.write(payload)
        print(f"WROTE {out_path}  (fact_kind={out.get('fact_kind')})")
    else:
        print("===ODOO_FACTS_START===")
        print(payload)
        print("===ODOO_FACTS_END===")


# `env` is injected by `odoo-bin shell`; absent (e.g. a unit-test import) → run()
# is skipped and only the pure helpers above are exposed.
if "env" in globals():
    run()  # noqa: F821
