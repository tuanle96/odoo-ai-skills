"""
Odoo metadata / XML-data introspector (Layer C) — run INSIDE `odoo-bin shell`.

Covers what's defined in XML and how the model is wired into the app — the
things grep misses and that cause "I duplicated a seeded record" or "my write
got reverted on -u" bugs:

  1. MENU GRAPH  — which menu paths -> actions reach this model (how a user
     navigates to it), plus actions bound to the model's action menu.
  2. SEEDED DATA — ir.model.data external IDs for this model's records: which
     module owns them, the xmlid, and noupdate (a noupdate=True record is
     loaded once on install, then PROTECTED from `-u` — later XML edits won't
     apply; change it on installed DBs with a migration. noupdate=False/default
     records are re-asserted from XML on every `-u`, so UI edits revert).
  3. QWEB REPORTS — the DEEP report wiring: report actions + their QWeb template
     xmlids + paperformat + the parser model (`_get_report_values`). This is the
     full layer to customize a report. (entrypoints.py also lists reports, but
     only as a QUICK name/type list — come here for the wiring.)

Non-fatal problems are collected in a module-level WARNINGS list and emitted as
"_warnings" rather than swallowed silently.

Usage
-----
    MODEL=sale.order odoo-bin shell -d <DB> --no-http < metadata.py
    MODEL=sale.order DATA_LIMIT=100 OUT=/tmp/meta.json odoo-bin shell -d <DB> < metadata.py

Output: pure JSON wrapped in ===ODOO_META_START=== / ===ODOO_META_END===.
"""
import os
import json

WARNINGS = []

MODEL = os.environ.get("MODEL")
if not MODEL:
    raise SystemExit("Set MODEL, e.g. MODEL=sale.order")

DATA_LIMIT = int(os.environ.get("DATA_LIMIT", "150"))
OUT = os.environ.get("OUT")

model = env[MODEL]            # noqa: F821  (env comes from the odoo shell)


def _read(model_name, domain, cols, **kw):
    try:
        return env[model_name].sudo().search_read(domain, cols, **kw)  # noqa: F821
    except Exception as e:
        WARNINGS.append(f"search_read {model_name} failed ({type(e).__name__}: {e})")
        return [{"_error": str(e)}]


# --- 1. Menu graph -----------------------------------------------------------
def menu_graph():
    actions = env["ir.actions.act_window"].sudo().search([("res_model", "=", MODEL)])  # noqa: F821
    if not actions:
        return {"actions": [], "menus": []}
    refs = [f"ir.actions.act_window,{a.id}" for a in actions]
    menus = env["ir.ui.menu"].sudo().search([("action", "in", refs)])  # noqa: F821
    menu_rows = []
    for m in menus:
        try:
            path = m.complete_name           # "Sales / Orders / Quotations"
        except Exception as e:
            # fall back to walking parent_id
            WARNINGS.append(f"complete_name failed for menu {m.id} "
                            f"({type(e).__name__}); walked parent_id instead")
            parts, cur = [], m
            while cur:
                parts.append(cur.name)
                cur = cur.parent_id
            path = " / ".join(reversed(parts))
        menu_rows.append({"path": path, "action": m.action.name if m.action else None})
    return {
        "actions": [{"id": a.id, "name": a.name, "view_mode": a.view_mode} for a in actions],
        "menus": menu_rows,
    }


# --- 2. Seeded data (external IDs) -------------------------------------------
def seeded_data():
    rows = _read("ir.model.data", [("model", "=", MODEL)],
                 ["module", "name", "res_id", "noupdate"], limit=DATA_LIMIT)
    if rows and isinstance(rows[0], dict) and "_error" in rows[0]:
        return {"error": rows[0]["_error"]}
    by_module = {}
    protected = []
    for r in rows:
        by_module[r["module"]] = by_module.get(r["module"], 0) + 1
        if r.get("noupdate"):
            protected.append(f"{r['module']}.{r['name']} (res_id={r['res_id']})")
    return {
        "count_returned": len(rows),
        "limit": DATA_LIMIT,
        "by_module": by_module,
        "noupdate_records": protected,   # noupdate=True: protected from -u (XML edits won't apply; use a migration)
        "sample": [f"{r['module']}.{r['name']}" for r in rows[:25]],
    }


# --- 3. QWeb report internals (DEEP wiring) ----------------------------------
def reports():
    acts = _read("ir.actions.report", [("model", "=", MODEL)],
                 ["name", "report_name", "report_type", "paperformat_id"])
    out = []
    for a in acts:
        if "_error" in a:
            out.append(a)
            continue
        rname = a.get("report_name")
        templates = _read("ir.ui.view", [("type", "=", "qweb"), ("key", "=", rname)],
                          ["key", "name", "inherit_id"])
        # api.Environment has no .get(); membership uses __contains__, item uses __getitem__
        parser = env[f"report.{rname}"] if rname and f"report.{rname}" in env else None  # noqa: F821
        out.append({
            "name": a.get("name"),
            "report_name": rname,
            "report_type": a.get("report_type"),
            "paperformat": a.get("paperformat_id"),
            "qweb_templates": [t.get("key") for t in templates if "_error" not in t],
            "parser_model": f"report.{rname}" if parser is not None else None,
            "hint": "If customizing data, look for _get_report_values on the parser "
                    "model; if customizing layout, inherit the qweb template(s) above.",
        })
    return out


result = {
    "model": MODEL,
    "menu_graph": menu_graph(),
    "seeded_data": seeded_data(),
    "reports": reports(),
    "_warnings": WARNINGS,
}

payload = json.dumps(result, indent=2, default=str)
if OUT:
    with open(OUT, "w") as fh:
        fh.write(payload)
    print(f"WROTE {OUT}")
else:
    print("===ODOO_META_START===")
    print(payload)
    print("===ODOO_META_END===")
