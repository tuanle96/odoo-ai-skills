"""
Unit tests for red_green_replay.py — pure-function tests (PART A: the
assembly/classification core). PART B (run_replay / make_odoo_test_runner) is
orchestration (real git + subprocess) and is intentionally NOT unit-tested
here, per the module's own docstring.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import red_green_replay as rr  # noqa: E402


# ---------------------------------------------------------------------------
# is_legit_red_failure
# ---------------------------------------------------------------------------

class TestIsLegitRedFailure(unittest.TestCase):

    def test_assertion_error_is_legit(self):
        text = (
            'Traceback (most recent call last):\n'
            '  File "test_sale.py", line 10, in test_confirm\n'
            "    self.assertEqual(order.state, 'sale')\n"
            "AssertionError: 'draft' != 'sale'"
        )
        ok, reason = rr.is_legit_red_failure(text)
        self.assertTrue(ok)
        self.assertIn("behavioral", reason)

    def test_todo_stub_is_fake(self):
        text = (
            'Traceback (most recent call last):\n'
            '  File "test_sale.py", line 5, in test_confirm\n'
            '    self.fail("TODO: implement test_confirm")\n'
            'AssertionError: TODO: implement test_confirm'
        )
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)
        self.assertIn("stub", reason)

    def test_import_error_is_fake(self):
        text = (
            'ImportError: No module named odoo.addons.sale_custom.models.sale_extra'
        )
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)

    def test_module_not_found_error_is_fake(self):
        text = "ModuleNotFoundError: No module named 'odoo.addons.sale_custom'"
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)

    def test_syntax_error_is_fake(self):
        text = 'SyntaxError: invalid syntax (test_sale.py, line 12)'
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)

    def test_indentation_error_is_fake(self):
        text = 'IndentationError: unexpected indent (test_sale.py, line 8)'
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)

    def test_collection_error_is_fake(self):
        text = (
            "ERROR: test_confirm (unittest.loader._FailedTest.test_confirm)\n"
            "ImportError: Failed to import test module: test_confirm"
        )
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)

    def test_setup_error_is_fake(self):
        text = (
            'Traceback (most recent call last):\n'
            '  File "test_sale.py", line 20, in setUp\n'
            "    self.partner = self.env['res.partner'].create({})\n"
            "ValidationError: A partner name is required."
        )
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)
        self.assertIn("setUp", reason)

    def test_setupclass_error_is_fake(self):
        text = (
            'ERROR: setUpClass (module.tests.test_sale.TestSale)\n'
            'Traceback (most recent call last):\n'
            '  File "test_sale.py", line 8, in setUpClass\n'
            "    raise ValueError('boom')\n"
        )
        ok, reason = rr.is_legit_red_failure(text)
        self.assertFalse(ok)

    def test_empty_text_is_fake(self):
        ok, reason = rr.is_legit_red_failure("")
        self.assertFalse(ok)

    def test_no_tests_ran_is_fake(self):
        ok, reason = rr.is_legit_red_failure("Ran 0 tests in 0.000s\n\nOK")
        self.assertFalse(ok)

    def test_no_tests_ran_phrase_is_fake(self):
        ok, reason = rr.is_legit_red_failure("no tests ran")
        self.assertFalse(ok)

    def test_user_error_is_legit(self):
        text = (
            'Traceback (most recent call last):\n'
            '  File "test_sale.py", line 15, in test_confirm\n'
            "    order.action_confirm()\n"
            "odoo.exceptions.UserError: You cannot confirm an order with no lines."
        )
        ok, reason = rr.is_legit_red_failure(text)
        self.assertTrue(ok)

    def test_validation_error_is_legit(self):
        text = "odoo.exceptions.ValidationError: The name is required."
        ok, reason = rr.is_legit_red_failure(text)
        self.assertTrue(ok)

    def test_unknown_text_is_conservatively_fake(self):
        ok, reason = rr.is_legit_red_failure("something weird happened here")
        self.assertFalse(ok)
        self.assertIn("could not confirm", reason)

    def test_none_text_is_fake(self):
        ok, reason = rr.is_legit_red_failure(None)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# parse_odoo_test_result
# ---------------------------------------------------------------------------

_SAMPLE_FAILED_OUTPUT = """\
======================================================================
FAIL: test_confirm (m.tests.test_sale.TestSale)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "test_sale.py", line 10, in test_confirm
    self.assertEqual(order.state, 'sale')
AssertionError: 'draft' != 'sale'

----------------------------------------------------------------------
Ran 3 tests in 0.123s

FAILED (failures=1)
"""

_SAMPLE_OK_OUTPUT = """\
----------------------------------------------------------------------
Ran 3 tests in 0.045s

OK
"""


class TestParseOdooTestResult(unittest.TestCase):

    def test_failed_block_parsed(self):
        result = rr.parse_odoo_test_result(_SAMPLE_FAILED_OUTPUT)
        self.assertTrue(result["failed"])
        self.assertFalse(result["passed"])
        self.assertIn("m.tests.test_sale.TestSale.test_confirm", result["test_ids"])
        self.assertEqual(len(result["failure_texts"]), 1)
        self.assertIn("AssertionError", result["failure_texts"][0])

    def test_clean_ok_run(self):
        result = rr.parse_odoo_test_result(_SAMPLE_OK_OUTPUT)
        self.assertTrue(result["passed"])
        self.assertFalse(result["failed"])
        self.assertEqual(result["test_ids"], [])
        self.assertEqual(result["failure_texts"], [])

    def test_empty_output_never_raises(self):
        result = rr.parse_odoo_test_result("")
        self.assertFalse(result["failed"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["test_ids"], [])

    def test_none_output_never_raises(self):
        result = rr.parse_odoo_test_result(None)
        self.assertFalse(result["failed"])
        self.assertFalse(result["passed"])

    def test_garbage_output_never_raises(self):
        result = rr.parse_odoo_test_result("\x00\x01 not a real test output %%% ===")
        self.assertIsInstance(result, dict)
        self.assertIn("failed", result)


# ---------------------------------------------------------------------------
# classify_replay
# ---------------------------------------------------------------------------

def _parsed(failed=False, passed=False, test_ids=None, failure_texts=None):
    return {
        "failed": failed, "passed": passed,
        "test_ids": test_ids or [], "failure_texts": failure_texts or [],
    }


_LEGIT_TEST_ID = "m.tests.test_sale.TestSale.test_confirm"
_LEGIT_FAILURE_TEXT = (
    'Traceback (most recent call last):\n'
    '  File "test_sale.py", line 10, in test_confirm\n'
    "    self.assertEqual(order.state, 'sale')\n"
    "AssertionError: 'draft' != 'sale'"
)


class TestClassifyReplay(unittest.TestCase):

    def test_legit_red_to_green_same_identity_is_ok(self):
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[_LEGIT_FAILURE_TEXT])
        head = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.classify_replay(base, head)
        self.assertTrue(report["ok"])
        self.assertTrue(report["base_failed"])
        self.assertTrue(report["head_passed"])
        self.assertTrue(report["same_identity"])
        self.assertTrue(report["red_is_legit"])

    def test_base_did_not_fail_is_not_ok(self):
        base = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        head = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.classify_replay(base, head)
        self.assertFalse(report["ok"])
        self.assertFalse(report["base_failed"])

    def test_head_still_failing_is_not_ok(self):
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[_LEGIT_FAILURE_TEXT])
        head = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.classify_replay(base, head)
        self.assertFalse(report["ok"])
        self.assertFalse(report["head_passed"])

    def test_todo_stub_red_is_not_legit(self):
        todo_text = 'self.fail("TODO: implement test_confirm")'
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[todo_text])
        head = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.classify_replay(base, head)
        self.assertFalse(report["ok"])
        self.assertFalse(report["red_is_legit"])

    def test_different_test_identities_is_not_ok(self):
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[_LEGIT_FAILURE_TEXT])
        head = _parsed(passed=True, test_ids=["m.tests.test_sale.TestSale.test_other"])
        report = rr.classify_replay(base, head)
        self.assertFalse(report["ok"])
        self.assertFalse(report["same_identity"])

    def test_additive_missing_attribute_red_is_legit(self):
        text = "AttributeError: 'res.partner' object has no attribute 'new_field'"
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[text])
        head = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.classify_replay(base, head, is_bugfix=False)
        self.assertTrue(report["red_is_legit"])
        self.assertTrue(report["ok"])

    def test_additive_missing_attribute_red_rejected_when_bugfix(self):
        text = "AttributeError: 'res.partner' object has no attribute 'new_field'"
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[text])
        head = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.classify_replay(base, head, is_bugfix=True)
        self.assertFalse(report["red_is_legit"])
        self.assertFalse(report["ok"])

    def test_no_failure_texts_is_not_legit(self):
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[])
        head = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.classify_replay(base, head)
        self.assertFalse(report["red_is_legit"])
        self.assertFalse(report["ok"])

    def test_none_inputs_never_raise(self):
        report = rr.classify_replay(None, None)
        self.assertFalse(report["ok"])


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class TestBuildReport(unittest.TestCase):

    def test_wraps_classify_replay(self):
        base = _parsed(failed=True, test_ids=[_LEGIT_TEST_ID], failure_texts=[_LEGIT_FAILURE_TEXT])
        head = _parsed(passed=True, test_ids=[_LEGIT_TEST_ID])
        report = rr.build_report(base, head)
        self.assertTrue(report["ok"])
        for key in ("ok", "base_failed", "head_passed", "same_identity",
                    "red_is_legit", "tests", "reasons", "_warnings"):
            self.assertIn(key, report)

    def test_malformed_inputs_do_not_raise(self):
        report = rr.build_report("not a dict", 12345)
        self.assertIsInstance(report, dict)
        self.assertFalse(report["ok"])

    def test_empty_dicts_do_not_raise(self):
        report = rr.build_report({}, {})
        self.assertIsInstance(report, dict)
        self.assertFalse(report["ok"])

    def test_missing_keys_do_not_raise(self):
        report = rr.build_report({"failed": True}, {"passed": True})
        self.assertIsInstance(report, dict)
        self.assertFalse(report["ok"])  # no test_ids -> same_identity False


if __name__ == "__main__":
    unittest.main()
