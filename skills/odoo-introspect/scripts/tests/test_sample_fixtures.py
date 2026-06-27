"""
Validate the committed sample JSON fixtures in
`skills/odoo-introspect/references/samples/`.

These fixtures are the machine-readable companions to `references/sample-output.md`:
a realistic, valid JSON document per introspection layer. This test gives them
teeth — it (1) parses every fixture (catches invalid JSON / trailing-comma slips)
and (2) asserts each carries the REQUIRED top-level keys the corresponding script
actually emits, so the fixtures can't silently drift from the code.

The required-key sets below mirror the `result`/`out` dicts assembled in
model_brief / entrypoints / metadata / trace_flow / field_refs / preflight /
security_sim / state_capture `run()`. When you add a key to a script's output,
add it here too (and to a fixture) — that coupling is the point.

Run with pytest:   python -m pytest skills/odoo-introspect/scripts/tests -q
Or standalone:     python skills/odoo-introspect/scripts/tests/test_sample_fixtures.py
"""
import json
import sys
from pathlib import Path

SAMPLES = (Path(__file__).resolve().parent.parent.parent  # .../odoo-introspect
           / "references" / "samples")

# fixture filename -> required top-level keys (subset the script always emits)
REQUIRED_KEYS = {
    "sale_order.brief.json": {
        "identity", "field_count", "fields", "security", "auto_triggers",
        "overridden_methods", "methods", "manifest_depends", "_warnings", "_caveat"},
    "sale_order.entrypoints.json": {
        "model", "odoo_version", "views", "window_actions", "reports", "_caveat"},
    "sale_order.metadata.json": {
        "model", "menu_graph", "seeded_data", "reports", "_warnings"},
    "sale_order.trace.json": {
        "root", "committed", "error", "sql_count_enabled", "warnings",
        "total_addon_calls", "total_sql", "summary", "distinct_steps", "calls"},
    "sale_order.refs.json": {
        "model", "field", "field_exists", "path_resolution", "defining_modules",
        "reference_count", "severity_counts", "references", "_warnings", "_caveat"},
    "sale_confirm_guard.preflight.json": {
        "database", "has_demo_data", "addons_path", "shadow_warnings", "_warnings",
        "module", "module_loaded_from", "module_models_in_registry", "verdict"},
    "sale_order.security.json": {
        "model", "user", "company", "access_rights", "record_rules", "field_access",
        "_warnings", "_caveat"},
    "sale_order.state.json": {
        "root", "committed", "break_at", "break_line", "captured_fields", "redaction",
        "error", "breakpoint_hits", "breakpoints", "exception_stack", "_caveat"},
}


def _load(name):
    return json.loads((SAMPLES / name).read_text())


def test_all_fixtures_exist():
    missing = [n for n in REQUIRED_KEYS if not (SAMPLES / n).exists()]
    assert not missing, f"missing fixtures: {missing}"


def test_fixtures_are_valid_json_with_required_keys():
    for name, required in REQUIRED_KEYS.items():
        data = _load(name)
        assert isinstance(data, dict), f"{name}: top level must be an object"
        missing = required - set(data)
        assert not missing, f"{name}: missing required keys {sorted(missing)}"


def test_brief_internal_consistency():
    d = _load("sale_order.brief.json")
    assert d["field_count"] == len(d["fields"]), "field_count must match fields length"
    assert set(d["overridden_methods"]) == set(d["methods"]), \
        "overridden_methods must match methods keys"
    # the famous gotcha: confirmed state literal is 'sale', not 'confirmed'
    sel_values = {s["value"] for s in d["fields"]["state"]["selection"]}
    assert "sale" in sel_values and "confirmed" not in sel_values
    # by_location buckets are the path-based set (post-0.3.0)
    assert set(d["manifest_depends"]["by_location"]) == {"core", "enterprise", "local", "unknown"}


def test_refs_resolved_via_lands_on_target():
    d = _load("sale_order.refs.json")
    assert d["path_resolution"] == "graph-resolved"
    for r in d["references"]:
        if r["kind"] in ("stored_compute_depends", "related_field"):
            rv = r.get("resolved_via", {})
            assert rv.get("terminal_model") == d["model"]
            assert rv.get("terminal_field") == d["field"]


def test_trace_summary_shape():
    d = _load("sale_order.trace.json")
    s = d["summary"]
    assert {"call_counts", "top_self_sql", "max_depth", "writes_by_model",
            "exception_origin"} <= set(s)
    # self_sql never exceeds cumulative for any reported hotspot
    for h in s["top_self_sql"]:
        assert h["self_sql"] <= h["cumulative_sql"]
    # writes_by_model carries field NAMES only (strings), never value structures
    for model, w in s["writes_by_model"].items():
        assert all(isinstance(f, str) for f in w["fields"])


def test_security_modes_present():
    d = _load("sale_order.security.json")
    for mode in ("read", "write", "create", "unlink"):
        assert mode in d["access_rights"], f"access_rights missing {mode}"
        assert mode in d["record_rules"], f"record_rules missing {mode}"
        assert "effective_domain" in d["record_rules"][mode]
    assert d["user"]["is_superuser"] is False  # a meaningful (non-bypassed) sim


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
