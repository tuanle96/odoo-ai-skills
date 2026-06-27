"""
Unit tests for the pure helpers in the `odoo-ai` CLI.

The CLI only imports the stdlib and guards execution under `if __name__ ==
"__main__"`, so it can be imported safely outside an Odoo shell. We load it by
path because the file has no `.py` extension.
"""
import importlib.util
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ODOO_AI = REPO_ROOT / "skills" / "odoo-introspect" / "scripts" / "odoo-ai"


def _load_odoo_ai():
    # The file has no .py extension, so name a SourceFileLoader explicitly.
    loader = SourceFileLoader("odoo_ai_cli", str(ODOO_AI))
    spec = importlib.util.spec_from_loader("odoo_ai_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


odoo_ai = _load_odoo_ai()


class ExtractTests(unittest.TestCase):
    def test_extracts_body_between_sentinels(self):
        out = "noise\n===A===\n{\"x\": 1}\n===B===\ntrailing"
        self.assertEqual(odoo_ai.extract(out, "===A===", "===B==="), '{"x": 1}')

    def test_strips_surrounding_whitespace(self):
        out = "===A===\n\n  payload  \n\n===B==="
        self.assertEqual(odoo_ai.extract(out, "===A===", "===B==="), "payload")

    def test_returns_none_when_start_missing(self):
        self.assertIsNone(odoo_ai.extract("===B===", "===A===", "===B==="))

    def test_returns_none_when_end_missing(self):
        self.assertIsNone(odoo_ai.extract("===A=== body", "===A===", "===B==="))

    def test_returns_none_on_empty_input(self):
        self.assertIsNone(odoo_ai.extract("", "===A===", "===B==="))

    def test_does_not_truncate_when_body_contains_end_sentinel(self):
        # extract() uses rsplit on the end marker, so a payload that itself
        # contains the end sentinel (e.g. via --source) is kept intact up to the
        # LAST marker rather than truncated early.
        out = "===A===first===B===second===B==="
        self.assertEqual(odoo_ai.extract(out, "===A===", "===B==="), "first===B===second")


class SummTests(unittest.TestCase):
    def test_brief_summary(self):
        data = {"field_count": 42, "overridden_methods": ["a", "b", "c"]}
        self.assertEqual(odoo_ai._summ("brief", data), "(42 fields, 3 methods)")

    def test_entrypoints_summary_counts_buttons_and_reports(self):
        data = {
            "views": {
                "form": {"buttons": [{"name": "x"}, {"name": "y"}]},
                "list": {"buttons": [{"name": "z"}]},
            },
            "reports": [{"name": "r1"}],
        }
        self.assertEqual(odoo_ai._summ("entrypoints", data), "(3 buttons, 1 reports)")

    def test_entrypoints_summary_ignores_non_dict_views(self):
        data = {"views": {"form": "oops", "list": {"buttons": []}}, "reports": []}
        self.assertEqual(odoo_ai._summ("entrypoints", data), "(0 buttons, 0 reports)")

    def test_metadata_summary(self):
        data = {
            "menu_graph": {"menus": ["m1", "m2"]},
            "seeded_data": {"noupdate_records": ["a.b", "c.d", "e.f"]},
        }
        self.assertEqual(odoo_ai._summ("metadata", data), "(2 menu paths, 3 protected records)")

    def test_trace_summary(self):
        data = {"total_addon_calls": 10, "total_sql": 5, "distinct_steps": ["a", "b"]}
        self.assertEqual(odoo_ai._summ("trace", data), "(10 calls, 5 SQL, 2 distinct steps)")

    def test_returns_empty_string_on_missing_keys(self):
        # _summ must never raise — a malformed payload yields "".
        self.assertEqual(odoo_ai._summ("brief", {}), "")
        self.assertEqual(odoo_ai._summ("trace", {"total_addon_calls": 1}), "")

    def test_returns_empty_string_for_unknown_step(self):
        self.assertEqual(odoo_ai._summ("nope", {"anything": 1}), "")

    def test_capabilities_module_summary(self):
        data = {"mode": "module", "module": "sale", "found": True, "state": "installed",
                "_summary": {"models": 3, "wizards": 2, "window_actions": 5,
                             "automation_rules": 1}}
        out = odoo_ai._summ("capabilities", data)
        self.assertIn("module=sale", out)
        self.assertIn("3 models", out)
        self.assertIn("2 wizards", out)

    def test_capabilities_model_summary(self):
        data = {"mode": "model", "model": "sale.order",
                "_summary": {"functional_fields": 40, "bound_actions": 3, "reports": 2}}
        out = odoo_ai._summ("capabilities", data)
        self.assertIn("model=sale.order", out)
        self.assertIn("40 fn-fields", out)

    def test_capabilities_not_installed_summary(self):
        data = {"mode": "module", "module": "x", "found": True,
                "state": "uninstalled", "_summary": {}}
        self.assertIn("not enumerable", odoo_ai._summ("capabilities", data))

    def test_native_check_summary(self):
        data = {"confirmed_candidates": [{"id": "account.payment_register"}],
                "unconfirmed_candidates": [{"id": "x"}, {"id": "y"}], "considered": 5}
        out = odoo_ai._summ("native_check", data)
        self.assertIn("1 present / 2 not-here", out)
        self.assertIn("account.payment_register", out)

    def test_native_check_summary_empty(self):
        data = {"confirmed_candidates": [], "unconfirmed_candidates": [], "considered": 0}
        self.assertIn("top=—", odoo_ai._summ("native_check", data))


if __name__ == "__main__":
    unittest.main()
