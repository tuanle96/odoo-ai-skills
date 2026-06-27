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
import preflight    # noqa: E402  (import-safe: run() is gated on `env` in globals)
import state_capture  # noqa: E402  (import-safe: run() is gated on `env` in globals)


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
