"""
Odoo native-capability scanner (Layer H) — run INSIDE `odoo-bin shell`.

Answers the question that comes BEFORE "where do I extend?": **what does this
instance already ship natively, so I don't reinvent it?** Enumerates the live
capability surface — wizards, window/server/report actions, scheduled actions
(crons), automation rules, sequences, mail templates, feature groups, menus, and
the functional fields / mixins around a model — straight from the running
registry, so an agent reuses native Odoo instead of writing custom code.

This is pure ENUMERATION, not matching/scoring. Existence is ground truth for
THIS instance and version; a capability absent here may still exist in another
edition (Community vs Enterprise) or sit behind a disabled feature-group. The
semantic "which native feature serves this requirement" step is a separate,
later layer — this one just lists what's really there, with xmlids as evidence.

Two modes (set exactly one):
    MODULE=sale   -> everything that module shipped (via the ir.model.data xmlid
                     registry — authoritative "what this addon provides").
    MODEL=sale.order -> the native capability surface AROUND a model (a.k.a.
                     feature-map): mixins, functional fields, actions/reports,
                     the bound Action-menu surface (wizards), crons, automations.

Unlike model_brief, this scanner NEVER reads server-action / cron `code` bodies
— it reports only names, triggers, and targets, so there is nothing to gate.

The env-dependent work lives in run(); the pure helpers are module-level so they
are importable/unit-testable without Odoo. run() executes only when `env` is
present (i.e. inside `odoo-bin shell`).

Usage
-----
    MODULE=sale     odoo-bin shell -d <DB> --no-http < capabilities.py
    MODEL=sale.order odoo-bin shell -d <DB> --no-http < capabilities.py
    MODEL=sale.order OUT=/tmp/cap.json odoo-bin shell -d <DB> < capabilities.py

Output: pure JSON wrapped in ===ODOO_CAP_START=== / ===ODOO_CAP_END===.
"""
import os
import json
from collections import defaultdict

WARNINGS = []

# Fields that are never "functional business fields" worth surfacing in a
# feature-map — ORM plumbing and mixin internals the agent shouldn't re-add.
TECHNICAL_FIELDS = frozenset({
    "id", "display_name", "create_uid", "create_date", "write_uid",
    "write_date", "__last_update",
})
MIXIN_FIELDS = frozenset({
    "access_url", "access_token", "access_warning", "my_activity_date_deadline",
})
MIXIN_FIELD_PREFIXES = ("message_", "activity_", "rating_", "website_message_")


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def is_functional_field(name):
    """True for business/functional fields; False for ORM/mixin plumbing.

    Used by feature-map to surface "what business fields already exist here"
    (so the agent reuses `commitment_date` instead of adding `x_delivery_date`)
    without drowning the list in `message_*` / `activity_*` / audit columns.
    """
    if not name or name in TECHNICAL_FIELDS or name in MIXIN_FIELDS:
        return False
    return not any(name.startswith(p) for p in MIXIN_FIELD_PREFIXES)


def mixin_capabilities(field_names):
    """Detect the native mixins a model carries, from its field names alone.

    Presence of these fields is how a model advertises chatter/activities/portal
    — so the agent reaches for `mail.thread` / `activity_schedule()` instead of a
    custom audit-log or reminder model.
    """
    fset = set(field_names or [])
    return {
        "mail_thread": "message_ids" in fset,   # chatter + field tracking
        "activities": "activity_ids" in fset,   # scheduled activities / reminders
        "portal": "access_url" in fset,         # portal / shareable access
    }


def count_surface(surface):
    """Count list-valued buckets in a capability surface dict (for summaries).

    Ignores the `_truncated` marker rows so a capped bucket still reports its
    real displayed count, and skips non-list metadata keys.
    """
    out = {}
    for key, val in (surface or {}).items():
        if isinstance(val, list):
            out[key] = sum(1 for item in val
                           if not (isinstance(item, dict) and "_truncated" in item))
    return out


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODULE = os.environ.get("MODULE")
    MODEL = os.environ.get("MODEL")
    OUT = os.environ.get("OUT")
    if not MODULE and not MODEL:
        raise SystemExit("Set MODULE=<addon> or MODEL=<model>, e.g. MODULE=sale "
                         "or MODEL=sale.order")

    def rows(recs, spec, cap=200):
        """Build a list of dicts from a recordset via a (key, getter) spec.

        Each getter is a lambda rec->value; per-record failures are collected in
        WARNINGS rather than aborting the whole bucket. Caps long buckets and
        appends a `_truncated` marker so the omission is never silent.
        """
        out = []
        recs = recs or []
        for rec in list(recs)[:cap]:
            row = {}
            for key, getter in spec:
                try:
                    row[key] = getter(rec)
                except Exception as e:  # noqa: BLE001
                    row[key] = None
                    WARNINGS.append(f"{key} read failed ({type(e).__name__}: {e})")
            out.append(row)
        extra = len(recs) - cap
        if extra > 0:
            out.append({"_truncated": f"+{extra} more"})
        return out

    def safe(fn, what):
        """Run a query, returning [] (not a crash) when the model/feature is
        absent — e.g. base.automation when base_automation isn't installed."""
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"{what} unavailable ({type(e).__name__}: {e})")
            return []

    advice = ("Reuse these BEFORE adding fields/models/crons/wizards/reports/"
              "automation. If you must extend, prefer a _prepare_*/_action_*/_get_* "
              "hook on the owning model over overriding create/write/action_*. "
              "See odoo-capabilities/references/native-primitives.md.")
    caveat = ("Existence is ground truth for THIS instance + version. A capability "
              "absent here may exist in another edition (Community vs Enterprise) or "
              "sit behind a disabled feature-group — confirm, don't assume the reverse.")

    if MODULE:
        out = scan_module(MODULE, rows, safe)
    else:
        out = scan_model(MODEL, rows, safe)
    out["_advice"] = advice
    out["_caveat"] = caveat
    out["_warnings"] = WARNINGS

    payload = json.dumps(out, indent=2, default=str)
    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}")
    else:
        print("===ODOO_CAP_START===")
        print(payload)
        print("===ODOO_CAP_END===")


def scan_module(module, rows, safe):
    """Enumerate everything an addon shipped, via the ir.model.data xmlid registry."""
    mod = env["ir.module.module"].sudo().search([("name", "=", module)], limit=1)  # noqa: F821
    if not mod:
        return {"mode": "module", "module": module, "found": False,
                "note": "module not found in ir.module.module (typo? not in addons_path?)"}
    if mod.state != "installed":
        return {"mode": "module", "module": module, "found": True, "state": mod.state,
                "note": "module is not installed — its capability surface is not in this "
                        "registry. Install it (or pick an installed module) to enumerate."}

    imd = safe(lambda: env["ir.model.data"].sudo().search_read(  # noqa: F821
        [("module", "=", module)], ["model", "res_id", "name"]), "ir.model.data")
    res_ids = defaultdict(list)
    xmlid_of = {}
    for r in imd:
        res_ids[r["model"]].append(r["res_id"])
        xmlid_of[(r["model"], r["res_id"])] = f"{module}.{r['name']}"

    def browse(model_name):
        ids = res_ids.get(model_name, [])
        return safe(lambda: env[model_name].browse(ids).exists(), model_name)  # noqa: F821

    def xid(model_name):
        return lambda r: xmlid_of.get((model_name, r.id))

    # ir.model records the module created → split business models vs wizards.
    imodels = browse("ir.model")
    models, wizards = [], []
    for r in imodels or []:
        try:
            entry = {"model": r.model, "name": r.name, "xmlid": xmlid_of.get(("ir.model", r.id))}
            (wizards if getattr(r, "transient", False) else models).append(entry)
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"ir.model row read failed ({type(e).__name__}: {e})")

    out = {
        "mode": "module", "module": module, "found": True, "state": mod.state,
        "models": models,
        "wizards": wizards,
        "window_actions": rows(browse("ir.actions.act_window"), [
            ("name", lambda r: r.name), ("res_model", lambda r: r.res_model),
            ("view_mode", lambda r: r.view_mode), ("xmlid", xid("ir.actions.act_window"))]),
        "server_actions": rows(browse("ir.actions.server"), [
            ("name", lambda r: r.name),
            ("model", lambda r: r.model_id.model if r.model_id else None),
            ("usage", lambda r: getattr(r, "usage", None)),
            ("state", lambda r: r.state), ("xmlid", xid("ir.actions.server"))]),
        "reports": rows(browse("ir.actions.report"), [
            ("name", lambda r: r.name), ("model", lambda r: r.model),
            ("report_name", lambda r: r.report_name),
            ("report_type", lambda r: r.report_type), ("xmlid", xid("ir.actions.report"))]),
        "crons": rows(browse("ir.cron"), [
            ("name", lambda r: r.name),
            ("model", lambda r: r.model_id.model if r.model_id else None),
            ("interval", lambda r: f"{r.interval_number} {r.interval_type}"),
            ("active", lambda r: r.active), ("xmlid", xid("ir.cron"))]),
        "automation_rules": rows(browse("base.automation"), [
            ("name", lambda r: r.name),
            ("model", lambda r: r.model_id.model if r.model_id else None),
            ("trigger", lambda r: getattr(r, "trigger", None)),
            ("active", lambda r: r.active), ("xmlid", xid("base.automation"))]),
        "sequences": rows(browse("ir.sequence"), [
            ("name", lambda r: r.name), ("code", lambda r: r.code),
            ("prefix", lambda r: r.prefix), ("xmlid", xid("ir.sequence"))]),
        "mail_templates": rows(browse("mail.template"), [
            ("name", lambda r: r.name),
            ("model", lambda r: r.model_id.model if r.model_id else None),
            ("xmlid", xid("mail.template"))]),
        "groups": rows(browse("res.groups"), [
            ("name", lambda r: r.full_name if hasattr(r, "full_name") else r.name),
            ("xmlid", xid("res.groups"))]),
        "menus": rows(browse("ir.ui.menu"), [
            ("name", lambda r: r.complete_name if hasattr(r, "complete_name") else r.name),
            ("xmlid", xid("ir.ui.menu"))], cap=300),
    }
    out["_summary"] = count_surface(out)
    return out


def scan_model(model_name, rows, safe):
    """Map the native capability surface AROUND a model (feature-map)."""
    if model_name not in env:  # noqa: F821  (Environment.__contains__ checks the registry)
        return {"mode": "model", "model": model_name, "found": False,
                "note": "model not in registry (typo? module not installed?)"}
    model = env[model_name]  # noqa: F821
    fields_set = model._fields
    functional = [n for n in sorted(fields_set) if is_functional_field(n)]
    FCAP = 150
    func_rows = []
    for name in functional[:FCAP]:
        f = fields_set[name]
        func_rows.append({
            "name": name, "type": f.type, "string": f.string,
            "help": (getattr(f, "help", None) or None),
            "stored": bool(f.store),
            "computed": bool(f.compute),
            "related": ".".join(f.related) if getattr(f, "related", None) else None,
        })
    if len(functional) > FCAP:
        func_rows.append({"_truncated": f"+{len(functional) - FCAP} more"})

    def by_model(actions_model, model_field="model_id.model"):
        return safe(lambda: env[actions_model].search(  # noqa: F821
            [(model_field, "=", model_name)]), actions_model)

    out = {
        "mode": "model", "model": model_name,
        "description": model._description, "transient": model._transient,
        "mixins": mixin_capabilities(fields_set),
        "functional_fields": func_rows,
        "window_actions": rows(safe(lambda: env["ir.actions.act_window"].search(  # noqa: F821
            [("res_model", "=", model_name)]), "ir.actions.act_window"), [
            ("name", lambda r: r.name), ("view_mode", lambda r: r.view_mode)]),
        # The Action-menu surface: contextual actions + wizards bound to this
        # model. This is how native wizards (Register Payment, etc.) attach.
        # NB: materialize each recordset to a list BEFORE concatenating — Odoo
        # forbids `recordset_a + recordset_b` across different models (raises
        # "Mixing apples and oranges", even when empty); rows() iterates a plain
        # list of mixed singleton records fine.
        "bound_actions": rows(
            list(safe(lambda: env["ir.actions.act_window"].search(  # noqa: F821
                [("binding_model_id.model", "=", model_name)]), "bound act_window"))
            + list(safe(lambda: env["ir.actions.server"].search(  # noqa: F821
                [("binding_model_id.model", "=", model_name)]), "bound server actions")), [
            ("name", lambda r: r.name),
            ("opens_model", lambda r: getattr(r, "res_model", None)),
            ("kind", lambda r: r._name)]),
        "reports": rows(by_model("ir.actions.report", "model"), [
            ("name", lambda r: r.name), ("report_type", lambda r: r.report_type)]),
        "server_actions": rows(by_model("ir.actions.server"), [
            ("name", lambda r: r.name), ("usage", lambda r: getattr(r, "usage", None)),
            ("state", lambda r: r.state)]),
        "crons": rows(by_model("ir.cron"), [
            ("name", lambda r: r.name),
            ("interval", lambda r: f"{r.interval_number} {r.interval_type}")]),
        "automation_rules": rows(by_model("base.automation"), [
            ("name", lambda r: r.name), ("trigger", lambda r: getattr(r, "trigger", None)),
            ("active", lambda r: r.active)]),
    }
    out["_summary"] = count_surface(out)
    out["_summary"]["functional_fields"] = len(functional)
    return out


# `env` is injected by `odoo-bin shell`; its presence means we're running for
# real. Absent (e.g. an import in a unit test) → run() is skipped and only the
# pure helpers above are exposed.
if "env" in globals():
    run()
