"""
Unit tests for the PURE functions in the introspection scripts — the bits that
need no running Odoo. The scripts are import-safe (env-dependent work only runs
when `env` is present, i.e. inside `odoo-bin shell`), so we can import them here.

Run with pytest:   python -m pytest skills/odoo-introspect/scripts/tests -q
Or standalone:     python skills/odoo-introspect/scripts/tests/test_pure_functions.py
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent   # .../odoo-introspect/scripts
sys.path.insert(0, str(SCRIPTS_DIR))

import model_brief  # noqa: E402  (import-safe: run() is gated on `env` in globals)
import entrypoints  # noqa: E402  (import-safe: run() is gated on `env` in globals)
import field_refs   # noqa: E402  (import-safe: run() is gated on `env` in globals)
import trace_flow   # noqa: E402  (import-safe: run() is gated on `env` in globals)
import preflight    # noqa: E402  (import-safe: run() is gated on `env` in globals)
import state_capture  # noqa: E402  (import-safe: run() is gated on `env` in globals)
import security_sim  # noqa: E402  (import-safe: run() is gated on `env` in globals)


def _load_odoo_ai():
    """`odoo-ai` has no .py extension; load it by explicit source loader."""
    path = SCRIPTS_DIR / "odoo-ai"
    loader = importlib.machinery.SourceFileLoader("odoo_ai", str(path))
    spec = importlib.util.spec_from_loader("odoo_ai", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)   # __name__ == "odoo_ai" → its main() guard does not fire
    return mod


odoo_ai = _load_odoo_ai()


# --- model_brief.analyze_source ---------------------------------------------
def test_analyze_source_empty():
    out = model_brief.analyze_source("")
    assert out["has_super"] is False
    assert out["heuristic"] is True
    assert out["super_position"] is None
    assert out["hooks_called"] == []


def test_analyze_source_no_super():
    out = model_brief.analyze_source("def f(self):\n    return 1\n")
    assert out["has_super"] is False
    assert out["super_position"] is None


def test_analyze_source_detects_super_and_hooks():
    src = (
        "def write(self, vals):\n"
        "    self._prepare_stuff()\n"
        "    res = super().write(vals)\n"
        "    self._sync_later()\n"
        "    return res\n"
    )
    out = model_brief.analyze_source(src)
    assert out["has_super"] is True
    assert "_prepare_stuff" in out["hooks_called"]
    assert "_sync_later" in out["hooks_called"]
    assert out["super_position"] is not None


def test_analyze_source_ignores_commented_super():
    # A commented-out super() must NOT register (comment-stripping heuristic).
    src = "def f(self):\n    # res = super().f()  # disabled\n    return 1\n"
    out = model_brief.analyze_source(src)
    assert out["has_super"] is False


def test_analyze_source_returns_before_super():
    src = (
        "def action_confirm(self):\n"
        "    if self.state == 'done':\n"
        "        return True\n"
        "    return super().action_confirm()\n"
    )
    out = model_brief.analyze_source(src)
    assert out["has_super"] is True
    assert out["returns_before_super"] is True


# --- model_brief.normalize_selection ----------------------------------------
def test_normalize_selection_pairs():
    out = model_brief.normalize_selection(
        [("draft", "Quotation"), ("sale", "Sales Order"), ("done", "Locked")])
    assert out == [
        {"value": "draft", "label": "Quotation"},
        {"value": "sale", "label": "Sales Order"},
        {"value": "done", "label": "Locked"},
    ]


def test_normalize_selection_dynamic_and_empty():
    assert model_brief.normalize_selection("_compute_states") == {"_dynamic": "method:_compute_states"}
    assert model_brief.normalize_selection(lambda self: []) == {"_dynamic": "callable"}
    assert model_brief.normalize_selection(None) is None
    assert model_brief.normalize_selection([]) is None


def test_normalize_selection_truncates():
    big = [(str(i), f"L{i}") for i in range(70)]
    out = model_brief.normalize_selection(big, max_items=60)
    assert len(out) == 61
    assert out[-1] == {"_truncated": "+10 more"}


def test_repr_domain():
    assert model_brief.repr_domain(None) is None
    assert model_brief.repr_domain(lambda self: []) == "<callable>"
    assert model_brief.repr_domain("[('active','=',True)]") == "[('active','=',True)]"
    assert model_brief.repr_domain([("a", "=", 1)]) == "[('a', '=', 1)]"
    long = model_brief.repr_domain("x" * 400, max_len=300)
    assert long.endswith("…") and len(long) == 301


def test_classify_addon_path():
    core = "/opt/odoo/odoo/addons"
    # core: under the base addons dir
    assert model_brief.classify_addon_path("/opt/odoo/odoo/addons/sale", core) == "core"
    assert model_brief.classify_addon_path(core, core) == "core"
    # enterprise: an `enterprise` path segment
    assert model_brief.classify_addon_path("/mnt/extra-addons/enterprise/web_studio", core) == "enterprise"
    # local: custom path (even if author claims Odoo S.A.)
    assert model_brief.classify_addon_path("/mnt/extra-addons/bestmix-addons/bm_account", core) == "local"
    # unknown: no path
    assert model_brief.classify_addon_path(None, core) == "unknown"
    # substring trap: a dir literally named like core but not under it
    assert model_brief.classify_addon_path("/opt/odoo/odoo/addons_custom/x", core) == "local"


# --- model_brief.gate_code ---------------------------------------------------
def test_gate_code_redacts_by_default():
    recs = [{"name": "a", "code": "x = secret_token\n" + "y" * 500}]
    out = model_brief.gate_code(recs, want_code=False, preview_len=100)
    rec = out[0]
    assert "code" not in rec                       # full body removed
    assert rec["code_present"] is True
    assert rec["code_len"] == len("x = secret_token\n") + 500
    assert rec["code_preview"].endswith("…")       # truncated preview
    assert len(rec["code_preview"]) == 101


def test_gate_code_keeps_body_when_wanted():
    recs = [{"name": "a", "code": "do_stuff()"}]
    out = model_brief.gate_code(recs, want_code=True)
    rec = out[0]
    assert rec["code"] == "do_stuff()"             # full body kept
    assert rec["code_present"] is True
    assert rec["code_len"] == len("do_stuff()")
    assert "code_preview" not in rec


def test_gate_code_handles_empty_and_missing():
    out = model_brief.gate_code([{"name": "a", "code": False}, {"name": "b"}], want_code=False)
    assert out[0]["code_present"] is False and "code" not in out[0]
    assert "code" not in out[1]                    # no code key → untouched body-wise
    # _safe_read error markers (no 'code' key) must pass through unharmed
    errs = model_brief.gate_code([{"_error": "boom"}], want_code=False)
    assert errs == [{"_error": "boom"}]
    assert model_brief.gate_code(None, want_code=False) is None


# --- entrypoints.order_inheritance_chain ------------------------------------
def test_inheritance_chain_orders_by_priority():
    views = [
        {"id": 1, "inherit_id": None, "priority": 16, "xmlid": "sale.form"},
        {"id": 3, "inherit_id": 1, "priority": 30, "xmlid": "custom.form_inherit"},
        {"id": 2, "inherit_id": 1, "priority": 20, "xmlid": "sale_stock.form_inherit"},
    ]
    out = entrypoints.order_inheritance_chain(views, root_id=1)
    assert [v["xmlid"] for v in out] == [
        "sale.form", "sale_stock.form_inherit", "custom.form_inherit"]


def test_inheritance_chain_nested_and_unreachable():
    views = [
        {"id": 1, "inherit_id": None, "priority": 16},
        {"id": 2, "inherit_id": 1, "priority": 20},
        {"id": 3, "inherit_id": 2, "priority": 10},   # grandchild
        {"id": 9, "inherit_id": 99, "priority": 5},    # unreachable from root 1
    ]
    out = entrypoints.order_inheritance_chain(views, root_id=1)
    assert [v["id"] for v in out] == [1, 2, 3]         # 9 excluded, parents first


def test_inheritance_chain_missing_root():
    assert entrypoints.order_inheritance_chain(
        [{"id": 2, "inherit_id": 1, "priority": 16}], root_id=1) == []


# --- field_refs pure helpers -------------------------------------------------
def test_depends_hit_local_and_dotted():
    assert field_refs.depends_hit(["commitment_date"], "commitment_date") is True
    assert field_refs.depends_hit(["order_id.commitment_date"], "commitment_date") is True
    assert field_refs.depends_hit(["order_id.date_order"], "commitment_date") is False
    assert field_refs.depends_hit([], "commitment_date") is False
    assert field_refs.depends_hit(None, "x") is False


def test_mentions_field_whole_identifier():
    assert field_refs.mentions_field("<field name='date'/>", "date") is True
    assert field_refs.mentions_field("<field name='commitment_date'/>", "date") is False
    assert field_refs.mentions_field("[('user_id','=',uid)]", "user_id") is True
    assert field_refs.mentions_field("", "date") is False


def test_classify_severity():
    assert field_refs.classify_severity("stored_compute_depends") == "high"
    assert field_refs.classify_severity("related_field") == "high"
    assert field_refs.classify_severity("view") == "medium"
    assert field_refs.classify_severity("record_rule") == "medium"
    assert field_refs.classify_severity("anything_else") == "low"


# --- field_refs.resolve_dotted_path (graph-aware) ---------------------------
# A tiny schema: comodel of each relational field; non-relational fields absent.
_SCHEMA = {
    "sale.order":  {"partner_id": "res.partner", "commitment_date": None},
    "res.partner": {"country_id": "res.country", "name": None},
    "res.country": {"code": None},
}


def _comodel_of(model, field):
    return _SCHEMA.get(model, {}).get(field)


def test_resolve_dotted_local_field():
    out = field_refs.resolve_dotted_path("sale.order", "commitment_date", _comodel_of)
    assert out["resolved"] is True
    assert out["terminal_model"] == "sale.order"
    assert out["terminal_field"] == "commitment_date"


def test_resolve_dotted_multi_hop():
    out = field_refs.resolve_dotted_path("sale.order", "partner_id.country_id.code", _comodel_of)
    assert out["resolved"] is True
    assert out["terminal_model"] == "res.country"
    assert out["terminal_field"] == "code"


def test_resolve_dotted_untraversable_hop():
    # 'commitment_date' is non-relational, so we can't traverse past it.
    out = field_refs.resolve_dotted_path("sale.order", "commitment_date.foo", _comodel_of)
    assert out["resolved"] is False
    assert "cannot traverse" in out["reason"]


def test_resolve_dotted_empty():
    out = field_refs.resolve_dotted_path("sale.order", "", _comodel_of)
    assert out["resolved"] is False and out["reason"] == "empty path"


def test_path_hits_target_disambiguates_same_last_segment():
    # Two paths ending in the SAME segment 'name' resolve to DIFFERENT models —
    # the text heuristic can't tell them apart, graph resolution can.
    schema = {
        "sale.order": {"partner_id": "res.partner", "company_id": "res.company"},
        "res.partner": {"name": None},
        "res.company": {"name": None},
    }
    cof = lambda m, f: schema.get(m, {}).get(f)  # noqa: E731
    hit = field_refs.path_hits_target(
        "sale.order", ["partner_id.name", "company_id.name"],
        "res.partner", "name", cof)
    assert hit is not None and hit["path"] == "partner_id.name"
    # target on res.company picks the OTHER path
    hit2 = field_refs.path_hits_target(
        "sale.order", ["partner_id.name", "company_id.name"],
        "res.company", "name", cof)
    assert hit2 is not None and hit2["path"] == "company_id.name"
    # no path lands on the target field
    assert field_refs.path_hits_target(
        "sale.order", ["partner_id.name"], "res.partner", "email", cof) is None


# --- trace_flow pure helpers -------------------------------------------------
def test_compute_self_sql_subtracts_children():
    # root(10) -> childA(6) -> grandchild(4); childB(1). depths preorder.
    calls = [
        {"depth": 0, "sql_count": 10},   # root: self = 10 - (6 + 1) = 3
        {"depth": 1, "sql_count": 6},    # childA: self = 6 - 4 = 2
        {"depth": 2, "sql_count": 4},    # grandchild: self = 4
        {"depth": 1, "sql_count": 1},    # childB: self = 1
    ]
    assert trace_flow.compute_self_sql(calls) == [3, 2, 4, 1]


def test_compute_self_sql_handles_none_and_empty():
    assert trace_flow.compute_self_sql([]) == []
    assert trace_flow.compute_self_sql([{"depth": 0, "sql_count": None}]) == [0]


def test_summarize_calls_counts_and_hotspots():
    calls = [
        {"depth": 0, "model": "sale.order", "method": "action_confirm", "addon": "sale",
         "line": 10, "sql_count": 12},
        {"depth": 1, "model": "stock.move", "method": "_action_done", "addon": "stock",
         "line": 20, "sql_count": 9},
        {"depth": 1, "model": "stock.move", "method": "_action_done", "addon": "stock",
         "line": 20, "sql_count": 1},
    ]
    out = trace_flow.summarize_calls(calls)
    # most-invoked pair first
    assert out["call_counts"][0] == {"model": "stock.move", "method": "_action_done",
                                     "addon": "stock", "count": 2}
    assert out["max_depth"] == 1
    # self-sql hotspot: root self = 12 - (9 + 1) = 2; the depth-1 frames keep their own
    top = {(r["method"], r["self_sql"]) for r in out["top_self_sql"]}
    assert ("_action_done", 9) in top
    # every reported hotspot has positive self_sql
    assert all(r["self_sql"] > 0 for r in out["top_self_sql"])


def test_summarize_calls_empty():
    assert trace_flow.summarize_calls([]) == {"call_counts": [], "top_self_sql": [], "max_depth": 0}


def test_aggregate_writes_groups_by_model_fields_only():
    events = [
        {"model": "sale.order", "method": "write", "fields": ["state"]},
        {"model": "sale.order", "method": "write", "fields": ["state", "date_order"]},
        {"model": "stock.move", "method": "create", "fields": ["product_id"]},
    ]
    out = trace_flow.aggregate_writes(events)
    assert out["sale.order"] == {"creates": 0, "writes": 2,
                                 "fields": ["date_order", "state"]}
    assert out["stock.move"] == {"creates": 1, "writes": 0, "fields": ["product_id"]}
    assert trace_flow.aggregate_writes([]) == {}


def test_vals_field_names_dict_and_list_no_values():
    assert trace_flow._vals_field_names({"state": "sale", "x": 1}) == ["state", "x"]
    assert trace_flow._vals_field_names([{"a": 1}, {"b": 2, "a": 3}]) == ["a", "b"]
    assert trace_flow._vals_field_names(None) == []
    assert trace_flow._vals_field_names("not-a-dict") == []


# --- security_sim pure helpers ----------------------------------------------
def test_effective_acl_additive():
    rows = [
        {"perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False},
        {"perm_read": True, "perm_write": True, "perm_create": False, "perm_unlink": False},
    ]
    eff = security_sim.effective_acl(rows)
    assert eff == {"read": True, "write": True, "create": False, "unlink": False}


def test_effective_acl_empty_denies_all():
    assert security_sim.effective_acl([]) == {
        "read": False, "write": False, "create": False, "unlink": False}
    assert security_sim.effective_acl(None)["read"] is False


def test_parse_field_groups():
    out = security_sim.parse_field_groups("base.group_user, !base.group_portal ,sale.group_x")
    assert out["positive"] == ["base.group_user", "sale.group_x"]
    assert out["negative"] == ["base.group_portal"]
    assert security_sim.parse_field_groups("") == {"positive": [], "negative": []}
    assert security_sim.parse_field_groups(None) == {"positive": [], "negative": []}


def test_field_visible_positive_groups():
    # no spec → always visible
    assert security_sim.field_visible(None, []) is True
    assert security_sim.field_visible("", ["base.group_user"]) is True
    # positive: needs at least one
    assert security_sim.field_visible("base.group_system", ["base.group_user"]) is False
    assert security_sim.field_visible("base.group_system", ["base.group_system"]) is True


def test_field_visible_negative_groups():
    # negated: hidden FROM members of that group
    assert security_sim.field_visible("!base.group_portal", ["base.group_portal"]) is False
    assert security_sim.field_visible("!base.group_portal", ["base.group_user"]) is True
    # mixed: must be in a positive AND in no negative
    assert security_sim.field_visible(
        "base.group_user,!base.group_portal", ["base.group_user"]) is True
    assert security_sim.field_visible(
        "base.group_user,!base.group_portal", ["base.group_user", "base.group_portal"]) is False


# --- preflight pure helpers --------------------------------------------------
def test_parse_addons_path():
    assert preflight.parse_addons_path("/a, /b ,/c") == ["/a", "/b", "/c"]
    assert preflight.parse_addons_path("") == []
    assert preflight.parse_addons_path(None) == []


def test_shadow_paths_detects_duplicate_and_datadir():
    flags = preflight.shadow_paths(["/opt/odoo/addons", "/opt/odoo/addons/"])
    assert any(f["reason"].startswith("duplicate") for f in flags)
    flags2 = preflight.shadow_paths(["/home/u/.local/share/Odoo/addons/18.0"])
    assert any("data-dir" in f["reason"] for f in flags2)
    assert preflight.shadow_paths(["/opt/a", "/opt/b"]) == []


# --- odoo-ai.extract ---------------------------------------------------------
def test_extract_basic():
    s = 'noise\n===S===\n{"a": 1}\n===E===\ntail'
    assert odoo_ai.extract(s, "===S===", "===E===") == '{"a": 1}'


def test_extract_missing_returns_none():
    assert odoo_ai.extract("no markers here", "===S===", "===E===") is None


def test_extract_tolerates_end_marker_in_body():
    # The JSON body itself contains the end sentinel (e.g. via --source). rsplit
    # on the LAST end marker must keep the full body, not truncate at the first.
    body = '{"src": "print(\'===E===\')"}'
    s = f"pre\n===S===\n{body}\n===E===\npost"
    assert odoo_ai.extract(s, "===S===", "===E===") == body


# --- odoo-ai._summ -----------------------------------------------------------
def test_summ_brief():
    d = {"field_count": 42, "overridden_methods": ["write", "create"]}
    out = odoo_ai._summ("brief", d)
    assert "42 fields" in out and "2 methods" in out


def test_summ_entrypoints():
    d = {"views": {"form": {"buttons": [1, 2]}, "list": {"buttons": [3]}}, "reports": [1]}
    out = odoo_ai._summ("entrypoints", d)
    assert "3 buttons" in out and "1 reports" in out


def test_summ_metadata():
    d = {"menu_graph": {"menus": [1, 2, 3]}, "seeded_data": {"noupdate_records": ["a"]}}
    out = odoo_ai._summ("metadata", d)
    assert "3 menu paths" in out and "1 protected" in out


def test_summ_handles_bad_shape():
    # Missing keys are swallowed → "" (never raises, never returns junk).
    assert odoo_ai._summ("brief", {}) == ""
    assert odoo_ai._summ("unknown-step", {}) == ""


def test_summ_state():
    d = {"breakpoint_hits": 2, "exception_stack": [{"a": 1}], "error": "ValueError: x"}
    out = odoo_ai._summ("state", d)
    assert "2 snapshots" in out and "1 exc frames" in out and "error=yes" in out
    out2 = odoo_ai._summ("state", {"breakpoint_hits": 0, "exception_stack": [], "error": None})
    assert "error=no" in out2


# --- state_capture pure helpers ---------------------------------------------
class _FakeRecordset:
    """Duck-typed Odoo recordset for serialization tests (no Odoo needed)."""
    def __init__(self, name, ids):
        self._name = name
        self.ids = ids

    def browse(self, *a, **k):   # presence is part of the duck-type check
        return self


def test_state_truncate():
    assert state_capture.truncate("abc", 10) == "abc"
    long = state_capture.truncate("x" * 50, 10)
    assert long.startswith("x" * 10) and "+40 chars" in long
    assert state_capture.truncate(12345, 3).startswith("123")


def test_state_should_break():
    # model.method
    assert state_capture.should_break("sale.order", "action_confirm", "sale.order.action_confirm")
    assert not state_capture.should_break("sale.order", "write", "sale.order.action_confirm")
    # bare method (any model)
    assert state_capture.should_break("res.partner", "create", "create")
    assert not state_capture.should_break("res.partner", "write", "create")
    # model-only wildcard
    assert state_capture.should_break("sale.order", "anything", "sale.order.*")
    assert not state_capture.should_break("stock.move", "anything", "sale.order.*")
    # empty spec never breaks
    assert not state_capture.should_break("sale.order", "write", "")


def test_state_addon_from_module():
    # real-world: custom/enterprise addons resolve by module name, not file path
    assert state_capture.addon_from_module("odoo.addons.bm_account.models.res_partner") == "bm_account"
    assert state_capture.addon_from_module("odoo.addons.sale.models.sale_order") == "sale"
    # core ORM plumbing is NOT an addon frame
    assert state_capture.addon_from_module("odoo.models") is None
    assert state_capture.addon_from_module("odoo.api") is None
    assert state_capture.addon_from_module("") is None
    assert state_capture.addon_from_module(None) is None


def test_state_is_recordset():
    assert state_capture.is_recordset(_FakeRecordset("sale.order", [1, 2]))
    assert not state_capture.is_recordset({"_name": "x"})       # no ids/browse
    assert not state_capture.is_recordset("sale.order")
    assert not state_capture.is_recordset(None)


def test_state_summarize_recordset():
    rs = _FakeRecordset("sale.order", list(range(25)))
    out = state_capture.summarize_recordset(rs, max_records=10)
    assert out["__recordset__"] == "sale.order"
    assert out["len"] == 25 and out["truncated"] is True
    assert out["ids"] == list(range(10))


def test_state_serialize_primitives_and_truncation():
    assert state_capture.serialize_value(None) is None
    assert state_capture.serialize_value(True) is True
    assert state_capture.serialize_value(7) == 7
    assert state_capture.serialize_value("hi") == "hi"
    big = state_capture.serialize_value("y" * 300, max_string=50)
    assert "+250 chars" in big


def test_state_serialize_recordset_and_containers():
    rs = _FakeRecordset("res.partner", [5])
    val = state_capture.serialize_value({"partner": rs, "tags": [1, 2, 3]})
    assert val["partner"]["__recordset__"] == "res.partner"
    assert val["tags"] == [1, 2, 3]
    # element cap on long lists
    out = state_capture.serialize_value(list(range(100)), max_items=10)
    assert len(out) == 11 and "more items" in out[-1]


def test_state_serialize_depth_and_unreprable():
    deep = {"a": {"b": {"c": {"d": 1}}}}
    out = state_capture.serialize_value(deep, max_depth=2)
    # beyond max_depth the inner value collapses to a type marker string
    assert isinstance(out["a"]["b"], str) and out["a"]["b"].startswith("<dict>")

    class Boom:
        def __repr__(self):
            raise RuntimeError("no repr")
    assert "unreprable" in state_capture.serialize_value(Boom())


def test_state_serialize_locals_skips_dunder_and_caps():
    frame_locals = {"__doc__": "x", "self": _FakeRecordset("a.b", [1]),
                    "vals": {"k": "v"}, "n": 3}
    out = state_capture.serialize_locals(frame_locals, max_locals=40)
    assert "__doc__" not in out
    assert out["self"]["__recordset__"] == "a.b"
    assert out["vals"] == {"k": "v"} and out["n"] == 3
    capped = state_capture.serialize_locals({f"v{i}": i for i in range(50)}, max_locals=5)
    assert "__truncated__" in capped


# --- state_capture redaction -------------------------------------------------
def test_state_is_sensitive_key():
    keys = state_capture.DEFAULT_REDACT_KEYS
    assert state_capture.is_sensitive_key("password", keys)
    assert state_capture.is_sensitive_key("db_password", keys)        # substring
    assert state_capture.is_sensitive_key("API_KEY", keys)            # case-insensitive
    assert state_capture.is_sensitive_key("access_token", keys)
    assert not state_capture.is_sensitive_key("partner_id", keys)
    # empty redact set disables redaction (NO_REDACT path)
    assert not state_capture.is_sensitive_key("password", frozenset())
    assert not state_capture.is_sensitive_key(None, keys)


def test_state_serialize_redacts_dict_keys():
    keys = state_capture.DEFAULT_REDACT_KEYS
    val = state_capture.serialize_value(
        {"login": "joe", "password": "hunter2", "api_key": "sk-123"}, redact_keys=keys)
    assert val["login"] == "joe"
    assert val["password"] == state_capture.REDACTED
    assert val["api_key"] == state_capture.REDACTED


def test_state_serialize_redacts_nested():
    keys = state_capture.DEFAULT_REDACT_KEYS
    val = state_capture.serialize_value(
        {"ctx": {"db_password": "x", "uid": 2}}, redact_keys=keys)
    assert val["ctx"]["db_password"] == state_capture.REDACTED
    assert val["ctx"]["uid"] == 2


def test_state_serialize_no_redact_when_disabled():
    val = state_capture.serialize_value({"password": "hunter2"}, redact_keys=frozenset())
    assert val["password"] == "hunter2"


def test_state_serialize_locals_redacts_by_name():
    keys = state_capture.DEFAULT_REDACT_KEYS
    out = state_capture.serialize_locals(
        {"password": "hunter2", "vals": {"token": "abc", "name": "ok"}, "n": 1},
        redact_keys=keys)
    assert out["password"] == state_capture.REDACTED       # local name redacted
    assert out["vals"]["token"] == state_capture.REDACTED  # nested dict key redacted
    assert out["vals"]["name"] == "ok"
    assert out["n"] == 1


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
