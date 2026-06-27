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

    # render+resolve a SPECIFIC view (for xpath work you need its chain):
    MODEL=sale.order VIEW_XMLID=sale.view_order_form odoo-bin shell -d <DB> < entrypoints.py
    MODEL=sale.order VIEW_ID=423 odoo-bin shell -d <DB> < entrypoints.py

Each rendered view includes `inheritance_chain` — the base view + every applied
extension view in application order (parents before children, siblings by
priority), with xmlid + priority. That's what you need before writing an xpath:
the resolved arch alone doesn't tell you which view to inherit or where your
xpath lands relative to other inheritors.

The env-dependent work lives in run(); the pure helper (order_inheritance_chain)
is module-level so it's importable/unit-testable without Odoo. run() executes
only when `env` is present (i.e. inside `odoo-bin shell`).

Output: pure JSON wrapped in ===ODOO_EP_START=== / ===ODOO_EP_END===.
"""
import os
import json
from xml.etree import ElementTree as ET

MODIFIERS = ("invisible", "readonly", "required", "column_invisible")


# --- Pure helper (no Odoo needed — unit-testable) ----------------------------
def order_inheritance_chain(views, root_id):
    """Order an inheritance graph for display: root first, then transitive
    extension children, parents before children, siblings by (priority, id).

    `views` is a list of dicts each with at least `id`, `inherit_id` (parent id
    or None), and `priority`. Returns the subset reachable from `root_id`.
    """
    by_parent = {}
    root = None
    for v in views:
        if v.get("id") == root_id:
            root = v
        by_parent.setdefault(v.get("inherit_id"), []).append(v)
    if root is None:
        return []
    out, seen = [], set()

    def walk(node):
        if node["id"] in seen:
            return
        seen.add(node["id"])
        out.append(node)
        kids = sorted(by_parent.get(node["id"], []),
                      key=lambda x: (x.get("priority", 16), x.get("id", 0)))
        for k in kids:
            walk(k)

    walk(root)
    return out


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    if not MODEL:
        raise SystemExit("Set MODEL, e.g. MODEL=sale.order")

    VIEWS = [v.strip() for v in os.environ.get("VIEWS", "form,list").split(",") if v.strip()]
    VIEW_ID = os.environ.get("VIEW_ID")
    VIEW_XMLID = os.environ.get("VIEW_XMLID")
    OUT = os.environ.get("OUT")

    model = env[MODEL]            # noqa: F821  (env comes from the odoo shell)

    def _odoo_version():
        try:
            import odoo
            return ".".join(str(x) for x in odoo.release.version_info[:2])
        except Exception:
            return None

    def _safe_read(model_name, domain, cols):
        try:
            return env[model_name].sudo().search_read(domain, cols)  # noqa: F821
        except Exception as e:
            return [{"_error": str(e)}]

    def inheritance_chain(view_type, root_id):
        """Resolve base + applied extension views for (MODEL, view_type)."""
        if not root_id:
            return None
        try:
            recs = env["ir.ui.view"].sudo().search(            # noqa: F821
                [("model", "=", MODEL), ("type", "=", view_type)])
            xmlids = recs.get_external_id() if recs else {}
            raw = [{
                "id": v.id,
                "name": v.name,
                "priority": v.priority,
                "mode": v.mode,
                "inherit_id": v.inherit_id.id or None,
                "xmlid": xmlids.get(v.id),
            } for v in recs]
            ordered = order_inheritance_chain(raw, root_id)
            return [{"xmlid": v["xmlid"], "name": v["name"],
                     "priority": v["priority"], "mode": v["mode"]} for v in ordered]
        except Exception as e:
            return [{"_error": str(e)}]

    def parse_arch(arch):
        try:
            root = ET.fromstring(arch)
        except Exception as e:
            return {"_error": str(e)}, None

        buttons = []
        for b in root.iter("button"):
            a = b.attrib
            buttons.append({
                "name": a.get("name"),
                "type": a.get("type"),          # "object" -> method; "action" -> action
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
        return {"buttons": [b for b in buttons if b["name"]], "fields": fields}, root

    def parse_view(view_type=None, view_id=None):
        # get_view() replaced fields_view_get in v16; guard with a clear message.
        if not hasattr(model, "get_view"):
            return {"_error": f"get_view() unavailable — requires Odoo v16+ (this instance is "
                              f"{_odoo_version()}); fall back to fields_view_get / raw ir.ui.view "
                              "arch. See references/version-matrix.md."}
        try:
            gv = model.get_view(view_id=view_id, view_type=view_type or "form")
        except Exception as e:
            return {"_error": str(e)}
        parsed, _ = parse_arch(gv["arch"])
        if isinstance(parsed, dict) and "_error" in parsed:
            return parsed
        root_id = gv.get("id")
        parsed["root_view_id"] = root_id
        parsed["inheritance_chain"] = inheritance_chain(gv.get("type") or view_type, root_id)
        return parsed

    # Specific-view mode (VIEW_XMLID / VIEW_ID) overrides the per-type sweep.
    if VIEW_XMLID or VIEW_ID:
        if VIEW_XMLID:
            try:
                vrec = env.ref(VIEW_XMLID)                      # noqa: F821
                vid, vtype = vrec.id, vrec.type
            except Exception as e:
                vid, vtype = None, None
                views_out = {VIEW_XMLID: {"_error": f"ref failed: {e}"}}
            else:
                views_out = {VIEW_XMLID: parse_view(view_type=vtype, view_id=vid)}
        else:
            vid = int(VIEW_ID)
            vtype = env["ir.ui.view"].sudo().browse(vid).type   # noqa: F821
            views_out = {f"id:{vid}": parse_view(view_type=vtype, view_id=vid)}
    else:
        views_out = {vt: parse_view(view_type=vt) for vt in VIEWS}

    result = {
        "model": MODEL,
        "odoo_version": _odoo_version(),
        "views": views_out,
        "window_actions": _safe_read(
            "ir.actions.act_window", [("res_model", "=", MODEL)],
            ["name", "view_mode", "domain", "context", "target", "view_id"],
        ),
        "reports": _safe_read(       # QUICK list — see metadata.py for deep wiring
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


# `env` is injected by `odoo-bin shell`; its presence means we're running for
# real. Absent (e.g. an import in a unit test) → run() is skipped and only the
# pure helper above is exposed.
if "env" in globals():
    run()
