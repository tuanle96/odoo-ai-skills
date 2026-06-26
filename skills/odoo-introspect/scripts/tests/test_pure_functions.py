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
