#!/usr/bin/env python3
"""Render odoo-ai introspection JSON into a self-contained HTML report with charts.

Turns the layer JSON that `odoo-ai` writes (Layer A brief / C metadata / D trace /
G security / K ESG) into the dependency-free chart blocks shipped by the
`html-report` skill — an MRO/super() ladder, a menu tree, SQL-hotspot bars, a
security matrix, and an ESG graph (rendered as bars + Mermaid text). No Odoo
connection, no JS, no CDN: the canonical `report.css` is inlined so the output is
one self-contained file that opens offline.

Usage
-----
    viz.py <bundle_dir | *.json ...> [--out report.html] [--no-open] [--title T]

Reads a directory (auto-discovering ``*.brief.json`` etc.) or explicit JSON files
(classified by filename suffix, then by content). Writes the HTML and opens it.
Pure standard library.
"""
import argparse
import html as _html
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# report.css ships with the sibling html-report skill: skills/html-report/assets.
CSS_PATH = Path(__file__).resolve().parents[2] / "html-report" / "assets" / "report.css"

LAYER_SUFFIX = {
    ".brief.json": "brief",
    ".metadata.json": "meta",
    ".trace.json": "trace",
    ".security.json": "security",
    ".esg.json": "esg",
}
LAYER_LABEL = {"brief": "A", "meta": "C", "trace": "D", "security": "G", "esg": "K"}


def esc(s) -> str:
    return _html.escape("" if s is None else str(s))


# --------------------------------------------------------------------------- #
# Discovery + classification
# --------------------------------------------------------------------------- #
def classify(path: Path, data) -> str | None:
    """Identify which introspection layer a JSON file holds (suffix, then content)."""
    name = path.name
    for suf, lay in LAYER_SUFFIX.items():
        if name.endswith(suf):
            return lay
    if isinstance(data, dict):
        if "methods" in data and "identity" in data:
            return "brief"
        if "menu_graph" in data:
            return "meta"
        if "calls" in data and "root" in data:
            return "trace"
        if "access_rights" in data and "record_rules" in data:
            return "security"
        if data.get("mode") == "esg" or "app_edges" in (data.get("graph") or {}):
            return "esg"
    return None


def collect(inputs) -> list[Path]:
    """Expand inputs (dirs and files) into a list of candidate JSON files."""
    files: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            files += sorted(f for f in p.glob("*.json") if f.is_file())
        elif p.is_file():
            files.append(p)
        else:
            print(f"WARN: not found, skipped: {p}", file=sys.stderr)
    return files


# --------------------------------------------------------------------------- #
# Small HTML helpers
# --------------------------------------------------------------------------- #
def panel(title: str, inner: str, cls: str = "", num=None) -> str:
    h2 = f'<h2>{f"<span class=\"num\">{num}</span>" if num else ""}{esc(title)}</h2>'
    klass = ("panel " + cls).strip()
    return f'<section class="{klass}">{h2}{inner}</section>'


def bars(items, label_of, value_of, tone_of=None, cap=12) -> str:
    items = list(items)[:cap]
    if not items:
        return ""
    mx = max((value_of(x) or 0) for x in items) or 1
    rows = []
    for i, x in enumerate(items):
        v = value_of(x) or 0
        pct = int(round(v * 100 / mx))
        tone = tone_of(i, x) if tone_of else ""
        cls = ("bar " + tone).strip()
        rows.append(
            f'<div class="{cls}"><span class="bl">{esc(label_of(x))}</span>'
            f'<span class="bt"><i style="width:{pct}%"></i></span>'
            f'<span class="bv">{esc(v)}</span></div>'
        )
    return '<div class="barchart">' + "".join(rows) + "</div>"


def cards(pairs) -> str:
    """pairs: list of (number, label, tone)."""
    out = []
    for num, label, tone in pairs:
        out.append(f'<div class="card {tone}"><span class="num">{esc(num)}</span>'
                   f'<span class="label">{esc(label)}</span></div>')
    return '<div class="cards">' + "".join(out) + "</div>"


# --------------------------------------------------------------------------- #
# Per-layer renderers — each returns a full <section class="panel"> (or "")
# --------------------------------------------------------------------------- #
def render_brief(data: dict, num) -> str:
    ident = data.get("identity") or {}
    methods = data.get("methods") or {}
    model = ident.get("model") or data.get("model") or "model"

    loc_of = {}
    for loc, addons in ((data.get("manifest_depends") or {}).get("by_location") or {}).items():
        for ad in (addons or []):
            loc_of[ad] = loc

    inner = [
        '<table class="kv">',
        f'<tr><th>Model</th><td><code>{esc(model)}</code> · table <code>{esc(ident.get("table"))}</code></td></tr>',
        f'<tr><th>Inherit</th><td>{esc(", ".join(ident.get("inherit") or []) or "—")}</td></tr>',
        f'<tr><th>Fields</th><td>{esc(data.get("field_count"))}</td></tr>',
        f'<tr><th>Overridden</th><td>{esc(", ".join(data.get("overridden_methods") or []) or "—")}</td></tr>',
        "</table>",
    ]

    for name, chain in methods.items():
        if not isinstance(chain, list) or not chain:
            continue
        rungs = []
        for e in chain:
            addon = e.get("addon") or "?"
            cls, notes = "rung", []
            if e.get("returns_before_super"):
                cls += " stop"
                notes.append("⛔ returns before super()")
            loc = loc_of.get(addon)
            if loc in ("core", "enterprise"):
                cls += " native"
                notes.append(loc)
            elif loc:
                notes.append(loc)
            if e.get("super_position") and "stop" not in cls:
                notes.append(e["super_position"])
            hooks = e.get("hooks_called") or []
            if hooks:
                notes.append("→ " + ", ".join(hooks))
            rungs.append(
                f'<div class="{cls}"><span class="who">{esc(addon)}</span>'
                f'<span class="note">{esc(" · ".join(notes))}</span></div>'
            )
        inner.append(f'<h3><code>{esc(name)}</code> — MRO / super() chain (outer → native)</h3>')
        inner.append('<div class="ladder">' + "".join(rungs) + "</div>")

    if not methods:
        inner.append('<p class="muted">No method MRO data (run <code>brief --methods</code> '
                     "to capture override chains).</p>")
    return panel(f"Layer A — model brief · {model}", "".join(inner), "info", num)


def _menu_tree_html(menus) -> str:
    root: dict = {}
    for m in menus:
        node = root
        for part in [p.strip() for p in (m.get("path") or "").split("/") if p.strip()]:
            node = node.setdefault(part, {})

    def render(node, is_root=False) -> str:
        if not node:
            return ""
        cls = ' class="tree"' if is_root else ""
        out = [f"<ul{cls}>"]
        for key, child in node.items():
            node_cls = "node" if child else "node cur"
            out.append(f'<li><span class="{node_cls}">{esc(key)}</span>{render(child)}</li>')
        out.append("</ul>")
        return "".join(out)

    return render(root, is_root=True)


def render_metadata(data: dict, num) -> str:
    model = data.get("model") or "model"
    menus = (data.get("menu_graph") or {}).get("menus") or []
    inner = []
    if menus:
        inner.append("<h3>Menu graph</h3>")
        inner.append(_menu_tree_html(menus))
    else:
        inner.append('<p class="muted">No menus reach this model.</p>')

    reports = data.get("reports") or []
    if reports:
        rows = "".join(
            f'<tr><td>{esc(r.get("name"))}</td><td><code>{esc(r.get("report_name"))}</code></td>'
            f'<td>{esc(r.get("report_type"))}</td></tr>'
            for r in reports
        )
        inner.append("<h3>Reports</h3>")
        inner.append('<table class="zebra"><thead><tr><th>Name</th><th>Template</th>'
                     f"<th>Type</th></tr></thead><tbody>{rows}</tbody></table>")

    seeded = data.get("seeded_data") or {}
    if seeded.get("by_module"):
        by = ", ".join(f"{esc(k)}: {esc(v)}" for k, v in seeded["by_module"].items())
        inner.append(f'<div class="callout info"><span><strong>Seeded data:</strong> {by}</span></div>')
    return panel(f"Layer C — metadata / menus · {model}", "".join(inner), "", num)


def render_trace(data: dict, num) -> str:
    summary = data.get("summary") or {}
    inner = [cards([
        (data.get("total_sql"), "SQL queries", "danger"),
        (data.get("total_addon_calls"), "Addon calls", "warn"),
        (summary.get("max_depth"), "Max depth", "info"),
        (len(data.get("distinct_steps") or []), "Distinct steps", "ok"),
    ])]

    tss = summary.get("top_self_sql") or []
    if tss:
        inner.append("<h3>SQL hotspots (self, excl. children)</h3>")
        inner.append(bars(
            tss,
            label_of=lambda x: f'{x.get("model","?")}.{x.get("method","?")}',
            value_of=lambda x: x.get("self_sql"),
            tone_of=lambda i, x: "danger" if i == 0 else ("warn" if i < 3 else ""),
        ))

    cc = summary.get("call_counts") or []
    if cc:
        inner.append("<h3>Most-invoked methods (loop / N+1 smell)</h3>")
        inner.append(bars(
            cc,
            label_of=lambda x: f'{x.get("model","?")}.{x.get("method","?")}',
            value_of=lambda x: x.get("count"),
            tone_of=lambda i, x: "warn" if i == 0 else "",
        ))

    calls = data.get("calls") or []
    if calls:
        rows = []
        for c in calls[:40]:
            d = max(0, int(c.get("depth", 0)))
            rows.append(
                f'<div style="padding-left:{d * 20}px;margin:3px 0">'
                f'<span class="tag">d{d}</span> '
                f'<code>{esc(c.get("model"))}.{esc(c.get("method"))}</code> '
                f'<span class="muted">{esc(c.get("addon"))} · sql {esc(c.get("sql_count"))}</span></div>'
            )
        inner.append("<h3>Call order (first 40)</h3>" + "".join(rows))

    cls = "danger" if data.get("error") else "warn"
    return panel(f'Layer D — runtime trace · {data.get("root") or ""}', "".join(inner), cls, num)


def render_security(data: dict, num) -> str:
    ar = data.get("access_rights") or {}
    user = (data.get("user") or {}).get("login") or "acting user"

    def cell(v):
        return '<td class="y">✓</td>' if v else '<td class="n">✗</td>'

    rows = [f'<tr><th>{esc(user)} (effective)</th>{cell(ar.get("read"))}{cell(ar.get("write"))}'
            f'{cell(ar.get("create"))}{cell(ar.get("unlink"))}</tr>']
    for acl in ar.get("contributing_acl") or []:
        nm = acl.get("group") or acl.get("name") or "?"
        rows.append(f'<tr><th>{esc(nm)}</th>{cell(acl.get("perm_read"))}{cell(acl.get("perm_write"))}'
                    f'{cell(acl.get("perm_create"))}{cell(acl.get("perm_unlink"))}</tr>')
    inner = ['<h3>Effective access (additive ACL)</h3>',
             '<table class="matrix"><thead><tr><th></th><th>read</th><th>write</th>'
             '<th>create</th><th>unlink</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"]

    rr = data.get("record_rules") or {}
    rule_rows = []
    for mode in ("read", "write", "create", "unlink"):
        dom = (rr.get(mode) or {}).get("effective_domain")
        if dom is not None:
            rule_rows.append(f'<tr><th>{mode}</th><td><code>{esc(json.dumps(dom))}</code></td></tr>')
    if rule_rows:
        inner.append("<h3>Record-rule effective domain</h3>")
        inner.append('<table class="kv">' + "".join(rule_rows) + "</table>")

    restricted = (data.get("field_access") or {}).get("restricted") or []
    if restricted:
        chips = " ".join(f'<span class="badge gray">{esc(r.get("field"))}</span>' for r in restricted)
        inner.append(f'<div class="callout warn"><span><strong>Group-restricted fields:</strong> {chips}</span></div>')
    return panel(f'Layer G — effective security · {data.get("model") or ""}', "".join(inner), "danger", num)


def _mermaid(edges, cap=40) -> str:
    lines = ["flowchart LR"]
    for e in edges[:cap]:
        a, b = e.get("from"), e.get("to")
        if not a or not b:
            continue
        ai, bi = str(a).replace(".", "_"), str(b).replace(".", "_")
        lines.append(f'  {ai}["{a}"] --> {bi}["{b}"]')
    return "<pre><code>" + esc("\n".join(lines)) + "</code></pre>"


def render_esg(data: dict, num) -> str:
    graph = data.get("graph") or {}
    edges = graph.get("edges") or []
    app_edges = graph.get("app_edges") or []
    summary = data.get("summary") or {}
    inner = [cards([
        (summary.get("models_touched"), "Models touched", "info"),
        (summary.get("model_edges"), "Model edges", "warn"),
        (summary.get("cross_app_edges"), "App edges", "danger"),
        (summary.get("seeds_traced"), "Seeds traced", "ok"),
    ])]
    if edges:
        inner.append("<h3>Top model → model edges (by weight)</h3>")
        inner.append(bars(
            sorted(edges, key=lambda e: -(e.get("weight") or 0)),
            label_of=lambda e: f'{e.get("from","?")} → {e.get("to","?")}',
            value_of=lambda e: e.get("weight"),
            tone_of=lambda i, e: "danger" if i == 0 else "",
        ))
        inner.append("<h3>model → model graph (Mermaid — paste into a renderer)</h3>")
        inner.append(_mermaid(edges))
    if app_edges:
        inner.append("<h3>app → app graph (Mermaid)</h3>")
        inner.append(_mermaid(app_edges))
    if not edges and not app_edges:
        inner.append('<p class="muted">No cross-model edges sampled.</p>')
    return panel("Layer K — execution surface graph (ESG)", "".join(inner), "info", num)


RENDERERS = {
    "brief": render_brief,
    "meta": render_metadata,
    "trace": render_trace,
    "security": render_security,
    "esg": render_esg,
}


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def read_css() -> str:
    if not CSS_PATH.is_file():
        print(f"ERROR: stylesheet not found: {CSS_PATH}", file=sys.stderr)
        sys.exit(1)
    return CSS_PATH.read_text(encoding="utf-8")


def build_html(sections: list[str], title: str, meta_items: list[str]) -> str:
    css = read_css()
    meta = "".join(f"<span>{esc(m)}</span>" for m in meta_items)
    body = "\n".join(sections)
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
{css}
</style>
</head>
<body>
<header><div class="head-inner">
<span class="eyebrow">Introspection</span>
<h1>{esc(title)}</h1>
<div class="meta">{meta}</div>
</div></header>
<main>
{body}
</main>
<footer><div class="foot-inner">
<span class="foot-brand"><span class="dot"></span>Odoo-AI</span>
<span>·</span><span>odoo-ai viz</span>
</div></footer>
</body>
</html>
"""


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        cmd = ["open", str(path)]
    elif sys.platform.startswith("win"):
        cmd = ["cmd", "/c", "start", "", str(path)]
    else:
        cmd = ["xdg-open", str(path)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"WARN: could not open browser: {exc}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render odoo-ai introspection JSON into an HTML report.")
    ap.add_argument("inputs", nargs="+", help="a bundle dir and/or layer JSON files")
    ap.add_argument("--out", default="", help="output HTML path (default: <dir>/report.html)")
    ap.add_argument("--title", default="", help="report title")
    ap.add_argument("--no-open", action="store_true", help="do not open the report in a browser")
    args = ap.parse_args(argv)

    files = collect(args.inputs)
    if not files:
        print("ERROR: no JSON files found.", file=sys.stderr)
        return 1

    found = []  # (layer, data, source)
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: skipping {f.name}: {exc}", file=sys.stderr)
            continue
        layer = classify(f, data)
        if layer in RENDERERS:
            found.append((layer, data, f.name))

    if not found:
        print("ERROR: none of the inputs matched a known layer "
              "(brief/metadata/trace/security/esg).", file=sys.stderr)
        return 1

    order = ["brief", "meta", "trace", "security", "esg"]
    found.sort(key=lambda t: order.index(t[0]))
    sections, layers_present = [], []
    for i, (layer, data, _src) in enumerate(found, start=1):
        sections.append(RENDERERS[layer](data, i))
        layers_present.append(LAYER_LABEL[layer])

    models = sorted({(d.get("model") or (d.get("identity") or {}).get("model"))
                     for _l, d, _s in found if (d.get("model") or (d.get("identity") or {}).get("model"))})
    title = args.title or ("Odoo introspection — " + (", ".join(models) or "report"))
    meta_items = [
        "📅 " + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "🎯 layers: " + ", ".join(layers_present),
        f"🔬 {len(found)} artifact(s)",
    ]
    html = build_html(sections, title, meta_items)

    if args.out:
        out = Path(args.out)
    else:
        first_dir = next((Path(p) for p in args.inputs if Path(p).is_dir()), None)
        out = (first_dir / "report.html") if first_dir else Path.cwd() / "odoo-ai-viz.html"
    out.write_text(html, encoding="utf-8")
    print(f"==> wrote {out}  (layers: {', '.join(layers_present)}, {len(html)} bytes)")

    if not args.no_open:
        open_file(out)
        print(f"==> opened {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
