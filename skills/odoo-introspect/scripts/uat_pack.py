"""
UAT Pack generator (local, no Odoo) — turns instance-grounded artifacts into
role-based UAT scripts a consultant hands to a client.

Consumes two artifacts already produced by earlier CLI runs:

  * entrypoint_surface.py  — the ranked LIVE entrypoint surface (object buttons,
                             window/server actions, crons, automations, reports,
                             routes) discovered from the running instance.
  * scenario_gen.py        — the risk-classified test scenarios for ONE model
                             ({model, methods, risk, scenarios:[{key, why}], ...}).

For each risk scenario it pairs the scenario with the entrypoints that live on
the scenario's model and emits a hand-to-client UAT case: role, data setup,
numbered steps, expected result, evidence slot, and a sign-off line. This is the
"killer consultant deliverable" — a concrete, instance-grounded acceptance test
a client can execute, not a generic checklist.

Everything here is heuristic and ALPHA: role derivation depends on the surface
carrying group data (it usually does not yet, so role falls back to a TODO), and
menu paths are only rendered when present. A consultant must review the pack
before it goes to a client. Nothing is dropped silently — scenarios that find no
entrypoint (or are squeezed out by the case cap) are listed in ``coverage_note``.

Usage
-----
    python3 uat_pack.py --surface <entrypoint_surface.json> \\
        --scenarios <scenario_gen.json> [--dossier <dossier.json>] \\
        [--title "..."] [--out-dir DIR] [--html]

Output: pure JSON (the pack) to stdout. Also writes ``<out-dir>/uat-pack.md``
(a table of cases + per-case detail blocks with checkbox sign-off lines) and,
with ``--html``, a self-contained ``<out-dir>/uat-pack.html`` (one panel per
case, via the sibling viz helpers). Always exits 0; errors are emitted as
``{"error": ..., "usage": ...}`` JSON so a non-zero return never hides them.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Sibling reuse: viz (esc / panel / build_html) for the optional HTML render.
sys.path.insert(0, os.path.dirname(__file__))

USAGE = ("uat_pack.py --surface <surface.json> --scenarios <scenarios.json> "
         "[--dossier <dossier.json>] [--title T] [--out-dir DIR] [--html]")

MAX_CASES = 10        # hard cap on total UAT cases per pack (spec)
FALLBACK_TOP_N = 10   # entrypoints used when the surface has no model match

# model → suggested fixture recipe id (matches fixture_factory recipe ids). The
# base-model prefix rule maps e.g. account.move.line onto the account.move recipe.
_FIXTURE_BY_MODEL = (
    ("sale.order", "sale_order_stockable"),
    ("account.move", "invoice_posted"),
    ("purchase.order", "purchase_to_receipt"),
    ("stock.picking", "delivery_with_lot"),
    ("mrp.production", "mo_with_bom"),
)
_DEFAULT_FIXTURE = "customer_basic"

CAVEAT = ("Alpha: role/menu derivation is heuristic; a consultant must review "
          "before client use.")


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo, no I/O — unit-testable)
# ---------------------------------------------------------------------------

def suggest_fixture_recipe(model):
    """Suggest a fixture_factory recipe id for *model* by domain heuristic.

    Exact match first, then base-model prefix (account.move.line → invoice_posted),
    else the generic customer_basic recipe.
    """
    m = (model or "").strip()
    for key, recipe in _FIXTURE_BY_MODEL:
        if m == key or m.startswith(key + "."):
            return recipe
    return _DEFAULT_FIXTURE


def _humanize_key(key):
    """'non_admin' → 'Non Admin'; empty/None → 'Scenario'."""
    return str(key or "").replace("_", " ").strip().title() or "Scenario"


def entrypoint_name(entry):
    """A human label for an entrypoint (label → ref → method → type)."""
    return (entry.get("label") or entry.get("ref") or entry.get("method")
            or entry.get("type") or "entrypoint")


def derive_role(entry):
    """Derive a client-facing role from an entrypoint's ``groups`` if present.

    The current surface scanner does not emit group data, so this almost always
    returns the TODO sentinel — kept defensive for surfaces that DO carry groups
    (e.g. a group xmlid 'sales_team.group_sale_salesman' → 'Sale Salesman').
    """
    groups = entry.get("groups")
    if isinstance(groups, (list, tuple)) and groups:
        names = []
        for g in groups:
            if not g:
                continue
            seg = str(g).split(".")[-1]
            if seg.startswith("group_"):
                seg = seg[len("group_"):]
            seg = seg.replace("_", " ").strip().title()
            if seg:
                names.append(seg)
        if names:
            # preserve order, drop dupes
            return " / ".join(dict.fromkeys(names))
    return "TODO: assign role"


def _module_names(value):
    """Coerce a dossier module list (of names or dicts) into a name set, or None."""
    if not isinstance(value, (list, tuple)):
        return None
    out = set()
    for item in value:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("module") or item.get("technical_name")
            if isinstance(name, str):
                out.add(name)
    return out or None


def installed_modules(dossier):
    """Best-effort installed-module name set from an opaque dossier dict.

    The dossier is written by another tool and treated as opaque; probe a few
    plausible shapes and return a set of module names, or None if none found.
    """
    if not isinstance(dossier, dict):
        return None
    modules = dossier.get("modules") if isinstance(dossier.get("modules"), dict) else {}
    meta = dossier.get("meta") if isinstance(dossier.get("meta"), dict) else {}
    for candidate in (
        dossier.get("installed_modules"),
        modules.get("installed"),
        meta.get("installed_modules"),
    ):
        names = _module_names(candidate)
        if names:
            return names
    return None


def build_preconditions(entry, installed):
    """Preconditions for a case: required module status + a company/user note."""
    mod = entry.get("module")
    if not mod:
        mod_line = "verify the required module is installed"
    elif installed is None:
        mod_line = f"verify module '{mod}' is installed"
    elif mod in installed:
        mod_line = f"module '{mod}' installed (confirmed in dossier)"
    else:
        mod_line = f"module '{mod}' NOT in dossier installed list — verify install"
    return [mod_line, "test company / user context prepared"]


def build_steps(entry):
    """Numbered UAT steps derived from the entrypoint kind (as a list of strings)."""
    kind = entry.get("type")
    name = entrypoint_name(entry)
    model = entry.get("model")
    steps = []

    path = entry.get("menu_path") or entry.get("path")
    if path and kind != "route":
        steps.append(f"Navigate: {path}")

    if kind == "object_button":
        steps.append(f"Open a {model} record (or create one from the data setup above).")
        method = entry.get("method") or entry.get("ref")
        steps.append(f"Click the '{name}' button" + (f" (method {method})." if method else "."))
        steps.append("Observe the resulting state change, chatter, and any downstream documents.")
    elif kind == "window_action":
        steps.append(f"Open the '{name}' menu/action for {model} "
                     f"(views: {entry.get('view_mode') or 'default'}).")
        steps.append("Create or select a record and confirm it opens for the assigned role.")
    elif kind == "server_action":
        usage = entry.get("usage")
        steps.append(f"Trigger server action '{name}'" + (f" (usage {usage})." if usage else "."))
        steps.append("Confirm the automated logic completes without error.")
    elif kind == "report":
        steps.append(f"Select a {model} record.")
        steps.append(f"Print/generate report '{name}' ({entry.get('report_type') or 'report'}).")
        steps.append("Verify the document renders with the correct data.")
    elif kind == "cron":
        steps.append(f"Open scheduled action '{name}' ({entry.get('trigger') or 'scheduled'}).")
        steps.append("Run it manually (Run Manually) to exercise the batch path.")
        steps.append("Verify the batch effect and check the log for errors.")
    elif kind == "automation":
        steps.append(f"Perform the triggering operation "
                     f"({entry.get('trigger') or 'create/write'}) on a {model} record.")
        steps.append(f"Verify automation rule '{name}' fired and applied its action.")
    elif kind == "route":
        methods = entry.get("methods") or ["GET"]
        steps.append(f"Send a {', '.join(str(m) for m in methods)} request to "
                     f"{entry.get('ref') or name} (auth: {entry.get('auth')}).")
        steps.append("Verify the HTTP status and response payload.")
    else:
        steps.append(f"Exercise entrypoint '{name}' on {model}.")
        steps.append("Verify the expected behaviour and no access error.")
    return steps


def build_expected(scenario):
    """Expected result: the scenario rationale + the universal acceptance clause."""
    why = str(scenario.get("why") or "").strip()
    suffix = "Record state transitions correctly; no access error for the assigned role."
    return (why + " " + suffix).strip() if why else suffix


def _rank(entry):
    r = entry.get("rank")
    return r if isinstance(r, (int, float)) and not isinstance(r, bool) else 0.0


def build_case(index, scenario, entry, doc_model, risk_tier, installed):
    """Assemble one UAT case dict from a (scenario, entrypoint) pair."""
    model = entry.get("model") or scenario.get("model") or doc_model
    recipe = suggest_fixture_recipe(model)
    return {
        "uat_id": f"UAT-{index:03d}",
        "title": f"{_humanize_key(scenario.get('key'))} · {entrypoint_name(entry)}"
                 + (f" ({model})" if model else ""),
        "model": model,
        "role": derive_role(entry),
        "preconditions": build_preconditions(entry, installed),
        "data_setup": {
            "suggested_fixture_recipe": recipe,
            "note": f"generate with: odoo-ai fixture {recipe}",
        },
        "steps": build_steps(entry),
        "expected_result": build_expected(scenario),
        "evidence": {"placeholder": "attach screenshot / evidence bundle path"},
        "sign_off": {"owner": "TODO", "date": None},
        "risk": scenario.get("risk") or risk_tier,
        # provenance of the derivation (additive — helps a reviewer trust the case)
        "scenario_key": scenario.get("key"),
        "entrypoint": {
            "type": entry.get("type"), "ref": entry.get("ref"),
            "method": entry.get("method"), "rank": entry.get("rank"),
        },
    }


def build_pack(surface, scenarios_doc, dossier, title, generated_at, source_artifacts):
    """Turn the two artifacts into the UAT pack dict (never raises).

    Cases are allocated round-robin across scenarios (each scenario contributes
    its top-ranked entrypoint before any scenario gets a second) so the ≤10-case
    budget maximises scenario coverage rather than exhausting on one scenario.
    """
    raw_eps = surface.get("entrypoints") if isinstance(surface, dict) else None
    entrypoints = [e for e in raw_eps if isinstance(e, dict)] if isinstance(raw_eps, list) else []
    ranked = sorted(entrypoints, key=lambda e: -_rank(e))

    doc_model = scenarios_doc.get("model") if isinstance(scenarios_doc, dict) else None
    risk = scenarios_doc.get("risk") if isinstance(scenarios_doc, dict) else None
    risk_tier = risk.get("tier") if isinstance(risk, dict) else None
    raw_scen = scenarios_doc.get("scenarios") if isinstance(scenarios_doc, dict) else None
    scenarios = [s for s in raw_scen if isinstance(s, dict)] if isinstance(raw_scen, list) else []

    installed = installed_modules(dossier)

    # per-scenario matching entrypoints (by model), highest rank first
    def matches_for(scen):
        m = scen.get("model") or doc_model
        return [e for e in ranked if e.get("model") == m]

    queues = [matches_for(s) for s in scenarios]
    fallback = False
    if scenarios and not any(queues) and ranked:
        # surface has no model match for ANY scenario → fall back to top-ranked
        fallback = True
        top = ranked[:FALLBACK_TOP_N]
        queues = [list(top) for _ in scenarios]

    # round-robin allocation, capped at MAX_CASES
    produced = [0] * len(scenarios)
    cases = []
    idx = 1
    max_depth = max((len(q) for q in queues), default=0)
    for depth in range(max_depth):
        for i, q in enumerate(queues):
            if depth < len(q) and len(cases) < MAX_CASES:
                cases.append(build_case(idx, scenarios[i], q[depth], doc_model, risk_tier, installed))
                produced[i] += 1
                idx += 1
        if len(cases) >= MAX_CASES:
            break

    # coverage_note: any scenario that produced zero cases — never dropped silently
    coverage_note = []
    for i, s in enumerate(scenarios):
        if produced[i] == 0:
            if not queues[i]:
                reason = ("no entrypoint in the surface matches model "
                          f"'{s.get('model') or doc_model}'")
            else:
                reason = f"dropped — the {MAX_CASES}-case cap was reached before this scenario's turn"
            coverage_note.append({"scenario": s.get("key"), "reason": reason})

    return {
        "title": title,
        "generated_at": generated_at,
        "source_artifacts": source_artifacts,
        "cases": cases,
        "coverage_note": coverage_note,
        "_fallback": fallback,
        "_caveat": CAVEAT,
    }


# ---------------------------------------------------------------------------
# Markdown render
# ---------------------------------------------------------------------------

def _md_cell(text):
    return str("" if text is None else text).replace("|", "\\|").replace("\n", " ")


def render_markdown(pack):
    """Render the pack as consultant-ready markdown (table + per-case blocks)."""
    lines = [f"# {pack.get('title')}", ""]
    lines.append(f"_Generated: {pack.get('generated_at')}_")
    src = pack.get("source_artifacts") or {}
    src_line = f"_Surface: `{src.get('surface')}` · Scenarios: `{src.get('scenarios')}`"
    if src.get("dossier"):
        src_line += f" · Dossier: `{src.get('dossier')}`"
    lines += [src_line + "_", "", f"> {pack.get('_caveat')}", ""]
    if pack.get("_fallback"):
        lines += ["> ⚠ No entrypoint matched the scenario model — cases use the "
                  "top-ranked entrypoints as a fallback.", ""]

    cases = pack.get("cases") or []
    lines += ["## Cases", "", "| UAT ID | Title | Model | Role | Risk |",
              "| --- | --- | --- | --- | --- |"]
    for c in cases:
        lines.append(f"| {c.get('uat_id')} | {_md_cell(c.get('title'))} | "
                     f"`{c.get('model')}` | {_md_cell(c.get('role'))} | {c.get('risk') or '—'} |")
    lines.append("")

    for c in cases:
        lines += [f"### {c.get('uat_id')} — {_md_cell(c.get('title'))}", ""]
        lines.append(f"- **Model:** `{c.get('model')}`")
        lines.append(f"- **Role:** {c.get('role')}")
        lines.append(f"- **Risk:** {c.get('risk') or '—'}")
        ds = c.get("data_setup") or {}
        lines += [f"- **Data setup:** `{ds.get('suggested_fixture_recipe')}` — {ds.get('note')}", ""]
        lines.append("**Preconditions:**")
        for p in c.get("preconditions") or []:
            lines.append(f"- [ ] {p}")
        lines += ["", "**Steps:**"]
        for i, s in enumerate(c.get("steps") or [], start=1):
            lines.append(f"{i}. {s}")
        lines += ["", f"**Expected result:** {c.get('expected_result')}", ""]
        ev = c.get("evidence") or {}
        lines += [f"**Evidence:** {ev.get('placeholder')}", "", "**Sign-off:**",
                  "- [ ] Tester: __________________  Date: __________",
                  "- [ ] Reviewer: ________________  Date: __________", ""]

    cov = pack.get("coverage_note") or []
    if cov:
        lines += ["## Coverage gaps (no silent drops)", ""]
        for g in cov:
            lines.append(f"- **{g.get('scenario')}** — {g.get('reason')}")
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTML render (optional — via sibling viz helpers)
# ---------------------------------------------------------------------------

def _case_panel(viz, c, num):
    esc = viz.esc
    ds = c.get("data_setup") or {}
    kv = [
        '<table class="kv">',
        f'<tr><th>Model</th><td><code>{esc(c.get("model"))}</code></td></tr>',
        f'<tr><th>Role</th><td>{esc(c.get("role"))}</td></tr>',
        f'<tr><th>Risk</th><td>{esc(c.get("risk") or "—")}</td></tr>',
        f'<tr><th>Data setup</th><td><code>{esc(ds.get("suggested_fixture_recipe"))}</code>'
        f' — {esc(ds.get("note"))}</td></tr>',
        "</table>",
    ]
    pre = "".join(f"<li>{esc(p)}</li>" for p in c.get("preconditions") or [])
    steps = "".join(f"<li>{esc(s)}</li>" for s in c.get("steps") or [])
    inner = "".join(kv)
    if pre:
        inner += f"<h3>Preconditions</h3><ul>{pre}</ul>"
    if steps:
        inner += f"<h3>Steps</h3><ol>{steps}</ol>"
    inner += f'<h3>Expected result</h3><p>{esc(c.get("expected_result"))}</p>'
    inner += ('<div class="callout info"><span><strong>Evidence:</strong> '
              f'{esc((c.get("evidence") or {}).get("placeholder"))}</span></div>')
    inner += ('<h3>Sign-off</h3><table class="kv">'
              '<tr><th>Tester</th><td>____________________  Date: __________</td></tr>'
              '<tr><th>Reviewer</th><td>__________________  Date: __________</td></tr></table>')
    return viz.panel(f'{c.get("uat_id")} — {c.get("title")}', inner, "info", num)


def render_html(pack):
    """Render the pack as a self-contained HTML report (one panel per case).

    May raise (incl. SystemExit if viz cannot find its stylesheet) — the caller
    guards this so an HTML failure degrades to a warning, never a crash.
    """
    import viz  # sibling
    sections = []
    for i, c in enumerate(pack.get("cases") or [], start=1):
        sections.append(_case_panel(viz, c, i))
    if not (pack.get("cases") or []):
        sections.append(viz.panel("No cases",
                                   '<p class="muted">No UAT cases were generated.</p>',
                                   "warn", None))
    cov = pack.get("coverage_note") or []
    if cov:
        rows = "".join(f'<li><strong>{viz.esc(g.get("scenario"))}</strong> — '
                       f'{viz.esc(g.get("reason"))}</li>' for g in cov)
        sections.append(viz.panel("Coverage gaps (no silent drops)",
                                  f"<ul>{rows}</ul>", "warn", None))
    meta_items = ["📅 " + str(pack.get("generated_at")),
                  f"🧪 {len(pack.get('cases') or [])} UAT case(s)"]
    return viz.build_html(sections, pack.get("title") or "UAT Pack", meta_items)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_json(path):
    """Read + parse a JSON file. Returns (doc, warning_or_None)."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        return None, f"{path}: could not read file — {type(exc).__name__}: {exc}"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"{path}: parse error — {type(exc).__name__}: {exc}"


def _now_iso():
    try:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    except Exception:  # noqa: BLE001 — clock quirks must not crash a report
        return None


def _default_title(scenarios_doc):
    model = scenarios_doc.get("model") if isinstance(scenarios_doc, dict) else None
    return f"UAT Pack — {model}" if model else "UAT Pack"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="uat_pack.py", add_help=True)
    parser.add_argument("--surface")
    parser.add_argument("--scenarios")
    parser.add_argument("--dossier")
    parser.add_argument("--title", default="")
    parser.add_argument("--out-dir", dest="out_dir", default=".")
    parser.add_argument("--html", action="store_true")
    # parse_known_args so stray flags never trigger argparse's non-zero exit.
    args, _unknown = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if not args.surface or not args.scenarios:
        print(json.dumps({"error": "both --surface and --scenarios are required",
                          "usage": USAGE}, indent=2))
        return 0

    surface, warn_s = _load_json(args.surface)
    scenarios_doc, warn_c = _load_json(args.scenarios)
    fatal = [w for w in (warn_s, warn_c) if w]
    if fatal:
        print(json.dumps({"error": "; ".join(fatal), "usage": USAGE}, indent=2, default=str))
        return 0

    warnings = []
    dossier = None
    if args.dossier:
        dossier, warn_d = _load_json(args.dossier)
        if warn_d:
            warnings.append(warn_d)
            dossier = None

    source_artifacts = {
        "surface": args.surface,
        "scenarios": args.scenarios,
        "dossier": args.dossier or None,
    }
    title = args.title or _default_title(scenarios_doc)
    pack = build_pack(surface, scenarios_doc, dossier, title, _now_iso(), source_artifacts)

    # Write the markdown deliverable (and optional HTML). Failures degrade to
    # warnings — the JSON pack is always printed.
    out_dir = args.out_dir or "."
    outputs = {"markdown": None, "html": None}
    try:
        os.makedirs(out_dir, exist_ok=True)
        md_path = os.path.join(out_dir, "uat-pack.md")
        with open(md_path, "w") as fh:
            fh.write(render_markdown(pack))
        outputs["markdown"] = md_path
    except OSError as exc:
        warnings.append(f"could not write markdown: {type(exc).__name__}: {exc}")

    if args.html:
        try:
            html = render_html(pack)
            html_path = os.path.join(out_dir, "uat-pack.html")
            with open(html_path, "w") as fh:
                fh.write(html)
            outputs["html"] = html_path
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — viz/css may be absent
            warnings.append(f"html generation failed: {type(exc).__name__}: {exc}")

    pack["outputs"] = outputs
    if warnings:
        pack["_warnings"] = warnings
    print(json.dumps(pack, indent=2, default=str, allow_nan=False))
    return 0


if __name__ == "__main__":
    main()
