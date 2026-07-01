"""
Unit tests for test_quality_gate.py — pure AST-lint tests (in-memory source
strings, no filesystem I/O except TestMain which uses tempfile).
"""
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import test_quality_gate as tq  # noqa: E402


def _rules(findings):
    return [f["rule"] for f in findings]


def _blocking(findings):
    return [f for f in findings if f["severity"] == "blocking"]


def _src(text):
    return textwrap.dedent(text).strip() + "\n"


# ---------------------------------------------------------------------------
# lint_source — good test (negative case)
# ---------------------------------------------------------------------------

class GoodTestTests(unittest.TestCase):
    def test_real_assertion_after_action_has_zero_blocking(self):
        source = _src("""
            class TestSaleOrder(TransactionCase):
                def test_confirm_sets_state(self):
                    order = self.env['sale.order'].create({'partner_id': self.partner.id})
                    order.action_confirm()
                    self.assertEqual(order.state, 'sale')
        """)
        findings = tq.lint_source(source, "test_sale.py")
        self.assertEqual(_blocking(findings), [])

    def test_assertRaisesRegex_specific_error_is_clean(self):
        source = _src("""
            class TestSaleOrder(TransactionCase):
                def test_locked_period_raises(self):
                    with self.assertRaisesRegex(UserError, "locked"):
                        self.move.action_post()
        """)
        findings = tq.lint_source(source, "test_sale.py")
        self.assertEqual(_blocking(findings), [])
        self.assertNotIn("broad_assertRaises", _rules(findings))

    def test_external_service_patch_requests_not_flagged(self):
        source = _src("""
            from unittest.mock import patch

            class TestPayment(TransactionCase):
                @patch("requests.post")
                def test_gateway_call(self, mock_post):
                    mock_post.return_value.status_code = 200
                    result = self.tx._send_to_gateway()
                    self.assertEqual(result, 'ok')
        """)
        findings = tq.lint_source(source, "test_payment.py")
        self.assertNotIn("mock_model_under_test", _rules(findings))


# ---------------------------------------------------------------------------
# vacuous_assert
# ---------------------------------------------------------------------------

class VacuousAssertTests(unittest.TestCase):
    def test_assertTrue_true(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertTrue(True)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("vacuous_assert", _rules(findings))
        vac = [f for f in findings if f["rule"] == "vacuous_assert"][0]
        self.assertEqual(vac["severity"], "blocking")

    def test_bare_assert_true(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    assert True
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("vacuous_assert", _rules(findings))

    def test_assertFalse_false(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertFalse(False)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("vacuous_assert", _rules(findings))

    def test_assertEqual_same_expression(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertEqual(order.state, order.state)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("vacuous_assert", _rules(findings))

    def test_assertIn_self_referential_list(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertIn(order.state, [order.state])
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("vacuous_assert", _rules(findings))

    def test_assertEqual_different_expressions_not_flagged(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertEqual(order.state, 'sale')
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertNotIn("vacuous_assert", _rules(findings))


# ---------------------------------------------------------------------------
# no_assertion
# ---------------------------------------------------------------------------

class NoAssertionTests(unittest.TestCase):
    def test_no_assert_at_all(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.env['sale.order'].create({})
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("no_assertion", _rules(findings))
        self.assertEqual(_blocking(findings)[0]["severity"], "blocking")

    def test_non_test_method_ignored(self):
        source = _src("""
            class T(TransactionCase):
                def helper(self):
                    self.env['sale.order'].create({})
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertEqual(findings, [])


# ---------------------------------------------------------------------------
# weak_only_assert (warning, not blocking)
# ---------------------------------------------------------------------------

class WeakOnlyAssertTests(unittest.TestCase):
    def test_assertIsNotNone_only_is_warning_not_blocking(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    order = self.env['sale.order'].create({})
                    self.assertIsNotNone(order)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("weak_only_assert", _rules(findings))
        weak = [f for f in findings if f["rule"] == "weak_only_assert"][0]
        self.assertEqual(weak["severity"], "warning")
        self.assertEqual(_blocking(findings), [])

    def test_assertTrue_of_call_only_is_weak(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertTrue(order.exists())
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("weak_only_assert", _rules(findings))

    def test_assertTrue_of_comparison_not_weak(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertTrue(order.amount_total > 0)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertNotIn("weak_only_assert", _rules(findings))


# ---------------------------------------------------------------------------
# swallowed_exception
# ---------------------------------------------------------------------------

class SwallowedExceptionTests(unittest.TestCase):
    def test_bare_except_pass(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    try:
                        rec.action_post()
                    except Exception:
                        pass
                    self.assertTrue(True)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("swallowed_exception", _rules(findings))

    def test_except_logging_only(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    try:
                        rec.action_post()
                    except Exception:
                        _logger.error("failed")
                    self.assertEqual(rec.state, 'posted')
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("swallowed_exception", _rules(findings))

    def test_except_with_reraise_not_flagged(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    try:
                        rec.action_post()
                    except Exception:
                        raise
                    self.assertEqual(rec.state, 'posted')
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertNotIn("swallowed_exception", _rules(findings))

    def test_except_non_business_error_not_flagged(self):
        # a non-business specific error (KeyError) swallow is not the target of
        # this rule — only broad + Odoo business exceptions are.
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    try:
                        rec.action_post()
                    except KeyError:
                        pass
                    self.assertEqual(rec.state, 'posted')
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertNotIn("swallowed_exception", _rules(findings))

    def test_swallowed_business_error_flagged(self):
        # Oracle final review: `except UserError: pass` hides the exact runtime
        # failure the method raises → must be flagged.
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    try:
                        rec.action_post()
                    except UserError:
                        pass
                    self.assertIsNotNone(rec)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("swallowed_exception", _rules(findings))


# ---------------------------------------------------------------------------
# broad_assertRaises
# ---------------------------------------------------------------------------

class BroadAssertRaisesTests(unittest.TestCase):
    def test_with_assertRaises_exception(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    with self.assertRaises(Exception):
                        rec.action_post()
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("broad_assertRaises", _rules(findings))
        f = [x for x in findings if x["rule"] == "broad_assertRaises"][0]
        self.assertEqual(f["severity"], "blocking")

    def test_direct_call_assertRaises_base_exception(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    self.assertRaises(BaseException, rec.action_post)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("broad_assertRaises", _rules(findings))

    def test_assertRaisesRegex_specific_not_flagged(self):
        source = _src("""
            class T(TransactionCase):
                def test_x(self):
                    with self.assertRaisesRegex(UserError, "locked"):
                        rec.action_post()
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertNotIn("broad_assertRaises", _rules(findings))


# ---------------------------------------------------------------------------
# mock_model_under_test
# ---------------------------------------------------------------------------

class MockModelUnderTestTests(unittest.TestCase):
    def test_patch_object_on_env_model(self):
        source = _src("""
            from unittest.mock import patch

            class T(TransactionCase):
                def test_x(self):
                    with patch.object(self.env['sale.order'], 'action_confirm', return_value=True):
                        self.order.action_confirm()
                    self.assertTrue(self.order.action_confirm.called)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("mock_model_under_test", _rules(findings))
        f = [x for x in findings if x["rule"] == "mock_model_under_test"][0]
        self.assertEqual(f["severity"], "blocking")

    def test_patch_requests_post_not_flagged(self):
        source = _src("""
            from unittest.mock import patch

            class T(TransactionCase):
                @patch("requests.post")
                def test_x(self, mock_post):
                    mock_post.return_value.status_code = 200
                    result = self.tx._send_to_gateway()
                    self.assertEqual(result, 'ok')
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertNotIn("mock_model_under_test", _rules(findings))

    def test_patch_odoo_addons_model_path_flagged(self):
        source = _src("""
            from unittest.mock import patch

            class T(TransactionCase):
                @patch("odoo.addons.sale.models.sale_order.SaleOrder.action_confirm")
                def test_x(self, mock_confirm):
                    self.order.action_confirm()
                    self.assertTrue(mock_confirm.called)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("mock_model_under_test", _rules(findings))

    def test_magicmock_assigned_to_env_flagged(self):
        source = _src("""
            from unittest.mock import MagicMock

            class T(TransactionCase):
                def test_x(self):
                    self.env = MagicMock()
                    self.assertTrue(self.env.called)
        """)
        findings = tq.lint_source(source, "test_x.py")
        self.assertIn("mock_model_under_test", _rules(findings))


# ---------------------------------------------------------------------------
# parse_error
# ---------------------------------------------------------------------------

class ParseErrorTests(unittest.TestCase):
    def test_syntax_error_does_not_raise(self):
        source = "def test_x(self:\n    self.assertTrue(True)\n"
        findings = tq.lint_source(source, "test_broken.py")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "parse_error")
        self.assertEqual(findings[0]["severity"], "warning")


# ---------------------------------------------------------------------------
# check_tests_init
# ---------------------------------------------------------------------------

class CheckTestsInitTests(unittest.TestCase):
    def test_missing_import_is_blocking(self):
        init_source = "from . import test_other\n"
        findings = tq.check_tests_init(init_source, ["test_sale", "test_other"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "not_imported")
        self.assertEqual(findings[0]["severity"], "blocking")
        self.assertIn("test_sale", findings[0]["message"])

    def test_all_imported_is_clean(self):
        init_source = "from . import test_sale, test_other\n"
        findings = tq.check_tests_init(init_source, ["test_sale", "test_other"])
        self.assertEqual(findings, [])

    def test_from_dot_module_import_form_counts(self):
        init_source = "from .test_sale import TestSaleOrder\n"
        findings = tq.check_tests_init(init_source, ["test_sale"])
        self.assertEqual(findings, [])

    def test_syntax_error_in_init_does_not_raise(self):
        findings = tq.check_tests_init("from . import (\n", ["test_sale"])
        self.assertEqual(findings[0]["rule"], "parse_error")
        self.assertEqual(findings[0]["severity"], "warning")


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class BuildReportTests(unittest.TestCase):
    def test_summary_counts_and_sort_order(self):
        good = _src("""
            class T(TransactionCase):
                def test_ok(self):
                    self.assertEqual(1, 1 + 0)
        """)
        # 1+0 constant-folds only at eval time, not AST-equal to bare 1 -> not vacuous
        bad = _src("""
            class T(TransactionCase):
                def test_bad(self):
                    self.assertTrue(True)
        """)
        report = tq.build_report({"b_bad.py": bad, "a_good.py": good})
        self.assertEqual(report["summary"]["files"], 2)
        self.assertEqual(report["summary"]["blocking"], 1)
        self.assertEqual(report["summary"]["warning"], 0)
        self.assertEqual(len(report["findings"]), 1)
        # sorted by file: a_good.py has no findings, b_bad.py's finding is present
        self.assertEqual(report["findings"][0]["file"], "b_bad.py")

    def test_empty_files_dict(self):
        report = tq.build_report({})
        self.assertEqual(report["summary"], {"files": 0, "blocking": 0, "warning": 0})
        self.assertEqual(report["findings"], [])

    def test_summary_blocking_is_int(self):
        report = tq.build_report({})
        self.assertIsInstance(report["summary"]["blocking"], int)


# ---------------------------------------------------------------------------
# main() — directory recursion + tests/__init__.py integration
# ---------------------------------------------------------------------------

class MainTests(unittest.TestCase):
    def test_directory_recursion_and_init_check(self):
        with tempfile.TemporaryDirectory() as td:
            tests_dir = Path(td) / "tests"
            tests_dir.mkdir()
            (tests_dir / "__init__.py").write_text("from . import test_other\n")
            (tests_dir / "test_sale.py").write_text(_src("""
                class T(TransactionCase):
                    def test_x(self):
                        self.assertTrue(True)
            """))
            (tests_dir / "test_other.py").write_text(_src("""
                class T(TransactionCase):
                    def test_y(self):
                        self.assertEqual(1, 2)
            """))

            import io
            from contextlib import redirect_stdout
            import json

            buf = io.StringIO()
            with redirect_stdout(buf):
                tq.main([str(td)])
            report = json.loads(buf.getvalue())

            rules = [f["rule"] for f in report["findings"]]
            self.assertIn("vacuous_assert", rules)
            self.assertIn("not_imported", rules)
            self.assertIsInstance(report["summary"]["blocking"], int)
            self.assertGreaterEqual(report["summary"]["blocking"], 2)

    def test_no_args_does_not_raise(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            tq.main([])
        self.assertIn("no paths given", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
