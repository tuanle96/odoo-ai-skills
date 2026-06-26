"""
Odoo entrypoint / view introspector (Layer B) — run INSIDE `odoo-bin shell`.

Answers "how is this model reached and what does the UI do to it" — the layer
model_brief.py doesn't cover. Many Odoo bugs come not from a model method but
from a form button, a view modifier (readonly/invisible/required), a window
action's context/domain, or a report. This reads the *inheritance-resolved*
view arch via get_view(), so you see what actually renders, not raw XML files.

NOTE: get_view() requires Odoo v16+ (it replaced fields_view_get; see
references/version-matrix.md). On v15 and below this layer won't run — fall back
to fields_view_get or the raw ir.ui.view arch.

NOTE: the `reports` block here is the QUICK list — name / report_name /
report_type only, to confirm a report exists. For the DEEP wiring (QWeb template
xmlids, paperformat, parser model / _get_report_values), run metadata.py.

Usage
-----
    MODEL=sale.order odoo-bin shell -d <DB> --no-http < entrypoints.py
    MODEL=sale.order VIEWS=form,list OUT=/tmp/ep.json odoo-bin shell -d <DB> < entrypoints.py

Output: pure JSON wrapped in ===ODOO_EP_START=== / ===ODOO_EP_END===.
"""
import os
import json
from xml.etree import ElementTree as ET

MODEL = os.environ.get("MODEL")
if not MODEL:
    raise SystemExit("Set MODEL, e.g. MODEL=sale.order")

VIEWS = [v.strip() for v in os.environ.get("VIEWS", "form,list").split(",") if v.strip()]
OUT = os.environ.get("OUT")

model = env[MODEL]            # noqa: F821  (env comes from the odoo shell)
MODIFIERS = ("invisible", "readonly", "required", "column_invisible")


def _odoo_version():
    try:
        import odoo
        return ".".join(str(x) for x in odoo.release.version_info[:2])
    except Exception:
        return None


def parse_view(view_type):
    # get_view() replaced fields_view_get in v16; guard proactively with a clear
    # message instead of leaking a raw AttributeError on older instances.
    if not hasattr(model, "get_view"):
        return {"_error": f"get_view() unavailable — requires Odoo v16+ (this instance is "
                          f"{_odoo_version()}); fall back to fields_view_get / raw ir.ui.view "
                          "arch. See references/version-matrix.md."}
    try:
        arch = model.get_view(view_type=view_type)["arch"]
        root = ET.fromstring(arch)
    except Exception as e:
        return {"_error": str(e)}

    buttons = []
    for b in root.iter("button"):
        a = b.attrib
        buttons.append({
            "name": a.get("name"),
            "type": a.get("type"),              # "object" -> calls a method; "action" -> runs an action
            "string": a.get("string") or (b.text or "").strip() or None,
            "context": a.get("context"),
            "groups": a.get("groups"),
            "invisible": a.get("invisible"),
            "confirm": a.get("confirm"),
        })

    fields = []
    for f in root.iter("field"):
        a = f.attrib
        mods = {k: a[k] for k in MODIFIERS if k in a}
        fields.append({
            "name": a.get("name"),
            "widget": a.get("widget"),
            "groups": a.get("groups"),
            "modifiers": mods or None,
        })

    return {
        "buttons": [b for b in buttons if b["name"]],
        "fields": fields,
    }


def _safe_read(model_name, domain, cols):
    try:
        return env[model_name].sudo().search_read(domain, cols)  # noqa: F821
    except Exception as e:
        return [{"_error": str(e)}]


result = {
    "model": MODEL,
    "odoo_version": _odoo_version(),
    "views": {vt: parse_view(vt) for vt in VIEWS},
    "window_actions": _safe_read(
        "ir.actions.act_window", [("res_model", "=", MODEL)],
        ["name", "view_mode", "domain", "context", "target"],
    ),
    "reports": _safe_read(           # QUICK list — see metadata.py for deep wiring
        "ir.actions.report", [("model", "=", MODEL)],
        ["name", "report_name", "report_type"],
    ),
}

payload = json.dumps(result, indent=2, default=str)
if OUT:
    with open(OUT, "w") as fh:
        fh.write(payload)
    print(f"WROTE {OUT}")
else:
    print("===ODOO_EP_START===")
    print(payload)
    print("===ODOO_EP_END===")
