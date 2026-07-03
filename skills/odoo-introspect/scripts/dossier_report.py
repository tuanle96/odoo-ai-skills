"""
Instance Dossier report renderer (local, no Odoo).

Turns a dossier JSON (produced by ``dossier.py`` inside ``odoo-bin shell``) into
ONE self-contained HTML page — the takeover / due-diligence read-out a consultant
hands over. Reuses the sibling ``viz.py`` chart helpers (esc / panel / bars /
cards / build_html) so it inlines the canonical report.css and opens offline.

Renders a top upgrade-risk-flag panel, then a panel per section (module class
distribution, Studio footprint, custom fields, server actions, automations,
crons, security record rules, view overrides, data-volume bars, integration
surface, companies). Any missing section is skipped silently, so a PARTIAL
dossier renders fine.

Usage
-----
    python3 dossier_report.py dossier.json
    python3 dossier_report.py dossier.json --out /tmp/instance-dossier.html

Output: JSON to stdout — {"ok": true, "html_path": "..."} (exit 0 always).
"""
import os
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from viz import esc, panel, bars, cards, build_html  # noqa: E402


# --------------------------------------------------------------------------- #
# Small table helpers (every cell escaped → injection-safe by construction)
# --------------------------------------------------------------------------- #
def _row(*cells):
    return "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in cells) + "</tr>"


def _table(headers, rows_html, cls="zebra"):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    return (f'<table class="{cls}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table>')


def _chips(names, cap=80):
    return " ".join(f'<span class="badge gray">{esc(n)}</span>' for n in list(names)[:cap])


def _int(v):
    return v if isinstance(v, int) else (v or 0)


# --------------------------------------------------------------------------- #
# Per-section renderers — each takes the whole dossier, returns "" if absent
# --------------------------------------------------------------------------- #
def render_flags(dossier):
    flags = dossier.get("upgrade_risk_flags") or []
    order = {"high": 0, "warn": 1, "info": 2}
    flags = sorted(flags, key=lambda f: order.get(f.get("severity"), 3))
    if not flags:
        return panel("🚩 Upgrade risk flags",
                     '<p class="muted">No elevated upgrade-risk flags detected.</p>', "ok")
    high = sum(1 for f in flags if f.get("severity") == "high")
    warn = sum(1 for f in flags if f.get("severity") == "warn")
    kpi = cards([(high, "high", "danger"), (warn, "warn", "warn"),
                 (len(flags), "total flags", "info")])
    rows = [_row(f.get("severity"), f.get("flag"), f.get("detail")) for f in flags]
    tbl = _table(["Severity", "Flag", "Detail"], rows)
    return panel("🚩 Upgrade risk flags", kpi + tbl, "danger")


def render_overview(dossier):
    im = dossier.get("installed_modules") or {}
    cs = dossier.get("custom_summary") or {}
    st = dossier.get("studio_footprint") or {}
    sec = dossier.get("security") or {}
    mc = dossier.get("multi_company") or {}
    if not any((im, cs, st, sec, mc)):
        return ""
    pairs = [
        (_int(im.get("total")), "modules", "info"),
        (_int(cs.get("custom")), "custom modules", "warn" if _int(cs.get("custom")) else "ok"),
        (_int(st.get("manual_field_count")), "manual fields",
         "warn" if _int(st.get("manual_field_count")) else "ok"),
        (_int(sec.get("rules_total")), "record rules", "info"),
        (_int(mc.get("company_count")), "companies", "info"),
    ]
    return panel("Instance overview", cards(pairs), "info")


def render_meta(dossier):
    m = dossier.get("meta")
    if not m:
        return ""
    rows = [
        f'<tr><th>Database</th><td><code>{esc(m.get("db_name"))}</code></td></tr>',
        f'<tr><th>Odoo version</th><td>{esc(m.get("odoo_version"))}</td></tr>',
        f'<tr><th>Internal users</th><td>{esc(m.get("internal_user_count"))}</td></tr>',
        f'<tr><th>Companies</th><td>{esc(m.get("company_count"))}</td></tr>',
        f'<tr><th>Generated</th><td>{esc(m.get("generated_at"))}</td></tr>',
        f'<tr><th>DB uuid</th><td><code>{esc(m.get("db_uuid"))}</code></td></tr>',
    ]
    return panel("Instance metadata", '<table class="kv">' + "".join(rows) + "</table>", "")


def render_modules(dossier):
    cs = dossier.get("custom_summary")
    if not cs:
        return ""
    items = [("standard", _int(cs.get("standard"))), ("oca", _int(cs.get("oca"))),
             ("custom", _int(cs.get("custom")))]
    b = bars(items, label_of=lambda x: x[0], value_of=lambda x: x[1],
             tone_of=lambda i, x: "danger" if x[0] == "custom" else "")
    names = cs.get("custom_modules") or []
    inner = b + (f"<h3>Custom modules ({len(names)})</h3><div>{_chips(names)}</div>"
                 if names else "")
    return panel("📦 Module footprint (by author class)", inner, "")


def render_studio(dossier):
    st = dossier.get("studio_footprint")
    if not st:
        return ""
    kpi = cards([
        (1 if st.get("web_studio_installed") else 0, "web_studio",
         "danger" if st.get("web_studio_installed") else "ok"),
        (_int(st.get("manual_field_count")), "manual fields", "warn"),
        (_int(st.get("x_studio_field_count")), "x_studio fields", "warn"),
        (_int(st.get("studio_view_count")), "studio views", "info"),
    ])
    per = st.get("per_model_manual") or []
    b = bars(per, label_of=lambda x: x.get("model"), value_of=lambda x: x.get("count"),
             tone_of=lambda i, x: "warn" if i < 3 else "")
    inner = kpi + (f"<h3>Manual fields by model (top {len(per)})</h3>{b}" if per else "")
    return panel("🎨 Studio footprint", inner, "warn")


def render_custom_fields(dossier):
    cf = dossier.get("custom_fields")
    if not cf:
        return ""
    rows = [_row(f.get("model"), f.get("name"), f.get("ttype"), f.get("relation") or "")
            for f in cf[:200]]
    return panel(f"🧬 Custom fields ({len(cf)})",
                 _table(["Model", "Field", "Type", "Relation"], rows), "")


def render_server_actions(dossier):
    sa = dossier.get("server_actions")
    if not sa:
        return ""
    rows = [_row(a.get("model"), a.get("name"), a.get("state"), a.get("usage"),
                 "custom" if a.get("custom") else "std") for a in sa[:100]]
    return panel(f"⚙️ Server actions ({len(sa)})",
                 _table(["Model", "Name", "Type", "Usage", "Origin"], rows), "")


def render_automations(dossier):
    au = dossier.get("automations")
    if not au:
        return ""
    rows = [_row(a.get("model"), a.get("name"), a.get("trigger"),
                 "on" if a.get("active") else "off") for a in au]
    return panel(f"🤖 Automated actions ({len(au)})",
                 _table(["Model", "Name", "Trigger", "Active"], rows), "")


def render_crons(dossier):
    cr = dossier.get("crons")
    if not cr:
        return ""
    rows = [_row(c.get("name"), c.get("model"),
                 f"{c.get('interval_number')} {c.get('interval_type')}",
                 "on" if c.get("active") else "off") for c in cr[:100]]
    return panel(f"⏰ Scheduled actions ({len(cr)})",
                 _table(["Name", "Model", "Interval", "Active"], rows), "")


def render_security(dossier):
    sec = dossier.get("security")
    if not sec:
        return ""
    kpi = cards([
        (_int(sec.get("groups_total")), "groups", "info"),
        (_int(sec.get("custom_groups")), "custom groups", "warn"),
        (_int(sec.get("rules_total")), "record rules", "info"),
        (_int(sec.get("models_with_rules")), "models w/ rules", "info"),
    ])
    rules = sec.get("record_rules") or []
    rows = []
    for r in rules[:200]:
        scope = "global" if r.get("is_global") else "grouped"
        mc = "✔" if r.get("multi_company") else ""
        rows.append(_row(r.get("model"), r.get("name"), scope, mc, r.get("domain") or ""))
    tbl = _table(["Model", "Rule", "Scope", "Multi-co", "Domain"], rows) if rows else ""
    return panel("🔒 Security — record rules", kpi + tbl, "danger")


def render_view_overrides(dossier):
    vo = dossier.get("view_overrides")
    if not vo:
        return ""
    views = vo.get("views") or []
    rows = [_row(v.get("model"), v.get("name"), v.get("inherit_of")) for v in views]
    inner = cards([(_int(vo.get("count")), "custom view overrides", "warn")])
    inner += _table(["Model", "View", "Inherits"], rows) if rows else ""
    return panel("🧩 View overrides", inner, "")


def render_data_volumes(dossier):
    dv = dossier.get("data_volumes")
    if not dv:
        return ""
    items = sorted(dv.items(), key=lambda kv: -(kv[1] or 0))
    b = bars(items, label_of=lambda x: x[0], value_of=lambda x: x[1],
             tone_of=lambda i, x: "info")
    return panel("📊 Data volumes", b, "")


def render_config(dossier):
    cfg = dossier.get("config_surface")
    if not cfg:
        return ""
    keys = cfg.get("integration_keys") or []
    kpi = cards([
        (_int(cfg.get("outgoing_mail_servers")), "outgoing mail servers", "info"),
        (_int(cfg.get("integration_keys_total")), "integration config keys", "info"),
    ])
    inner = kpi + (f"<h3>Integration config keys (names only — no values)</h3>"
                   f"<div>{_chips(keys)}</div>" if keys else "")
    return panel("🔌 Integration surface", inner, "")


def render_multi_company(dossier):
    mc = dossier.get("multi_company")
    if not mc:
        return ""
    names = mc.get("company_names") or []
    kpi = cards([
        (_int(mc.get("company_count")), "companies", "info"),
        (_int(mc.get("models_with_company_field")), "models w/ company_id", "info"),
    ])
    rows = [_row(n) for n in names]
    inner = kpi + (_table(["Company"], rows) if rows else "")
    return panel("🏢 Companies", inner, "")


RENDERERS = [
    render_flags, render_overview, render_meta, render_modules, render_studio,
    render_custom_fields, render_server_actions, render_automations, render_crons,
    render_security, render_view_overrides, render_data_volumes, render_config,
    render_multi_company,
]


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def build_report(dossier):
    """Render the dossier dict → self-contained HTML string. Never raises on a
    single bad section (partial dossiers render fine)."""
    sections = []
    for renderer in RENDERERS:
        try:
            html = renderer(dossier)
        except Exception:  # noqa: BLE001 — one bad section must not kill the report
            html = ""
        if html:
            sections.append(html)

    meta = dossier.get("meta") or {}
    db = meta.get("db_name") or "instance"
    flags = dossier.get("upgrade_risk_flags") or []
    title = f"Instance Dossier — {db}"
    meta_items = [
        "📅 " + str(meta.get("generated_at") or ""),
        "🗄 " + str(db),
        f"🚩 {len(flags)} risk flag(s)",
    ]
    return build_html(sections, title, meta_items)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Render an Instance Dossier JSON into a self-contained HTML report.")
    ap.add_argument("dossier", help="path to the dossier JSON produced by dossier.py")
    ap.add_argument("--out", default="", help="output HTML path (default: alongside input)")
    args = ap.parse_args(argv)

    src = Path(args.dossier)
    try:
        dossier = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return 0

    try:
        html = build_report(dossier)
        out = Path(args.out) if args.out else src.with_suffix(".html")
        out.write_text(html, encoding="utf-8")
        print(json.dumps({"ok": True, "html_path": str(out.resolve())}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
