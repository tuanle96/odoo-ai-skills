"""
Unit tests for changed_coverage_gate.py — pure-function tests (in-memory
diff-target / coverage.py JSON docs, no filesystem I/O except TestMain which
uses tempfile).
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import changed_coverage_gate as ccg  # noqa: E402


def _target(id="t1", model="sale.order", file="addons/sale/models/sale.py",
            method="action_confirm", changed_exec_lines=None, risk=None):
    t = {
        "id": id,
        "model": model,
        "method": method,
        "file": file,
        "changed_exec_lines": changed_exec_lines if changed_exec_lines is not None else [10, 11, 12],
    }
    if risk is not None:
        t["risk"] = risk
    return t


def _cov(files):
    return {"files": files}


# ---------------------------------------------------------------------------
# _is_test_context
# ---------------------------------------------------------------------------

class TestIsTestContext(unittest.TestCase):

    def test_empty_string_is_not_test(self):
        self.assertFalse(ccg._is_test_context(""))

    def test_none_is_not_test(self):
        self.assertFalse(ccg._is_test_context(None))

    def test_setup_marker_is_not_test(self):
        self.assertFalse(ccg._is_test_context("TestSaleOrder.setUpClass"))
        self.assertFalse(ccg._is_test_context("module.setup"))

    def test_import_marker_is_not_test(self):
        self.assertFalse(ccg._is_test_context("import"))

    def test_real_test_context_is_test(self):
        self.assertTrue(ccg._is_test_context("tests.test_sale.TestSaleOrder.test_action_confirm"))


# ---------------------------------------------------------------------------
# _match_file
# ---------------------------------------------------------------------------

class TestMatchFile(unittest.TestCase):

    def test_exact_match(self):
        files = {"addons/sale/models/sale.py": {}}
        self.assertEqual(ccg._match_file(files, "addons/sale/models/sale.py"),
                          "addons/sale/models/sale.py")

    def test_suffix_match(self):
        files = {"/home/ci/odoo/addons/sale/models/sale.py": {}}
        self.assertEqual(
            ccg._match_file(files, "addons/sale/models/sale.py"),
            "/home/ci/odoo/addons/sale/models/sale.py",
        )

    def test_no_match_returns_none(self):
        files = {"addons/other/models/other.py": {}}
        self.assertIsNone(ccg._match_file(files, "addons/sale/models/sale.py"))


# ---------------------------------------------------------------------------
# covered_lines_for_file
# ---------------------------------------------------------------------------

class TestCoveredLinesForFile(unittest.TestCase):

    def test_covered_by_real_test_context(self):
        files = {
            "addons/sale/models/sale.py": {
                "executed_lines": [10, 11, 12],
                "contexts": {
                    "10": ["tests.test_sale.TestSaleOrder.test_action_confirm"],
                    "11": ["tests.test_sale.TestSaleOrder.test_action_confirm"],
                    "12": ["tests.test_sale.TestSaleOrder.test_action_confirm"],
                },
            }
        }
        self.assertEqual(
            ccg.covered_lines_for_file(files, "addons/sale/models/sale.py"),
            {10, 11, 12},
        )

    def test_line_covered_only_by_setup_context_not_covered(self):
        files = {
            "addons/sale/models/sale.py": {
                "executed_lines": [10],
                "contexts": {"10": ["TestSaleOrder.setUpClass"]},
            }
        }
        self.assertEqual(ccg.covered_lines_for_file(files, "addons/sale/models/sale.py"), set())

    def test_no_contexts_falls_back_to_executed_lines(self):
        files = {
            "addons/sale/models/sale.py": {"executed_lines": [10, 11]},
        }
        self.assertEqual(
            ccg.covered_lines_for_file(files, "addons/sale/models/sale.py"), {10, 11}
        )

    def test_file_not_in_coverage_returns_empty(self):
        files = {"addons/other/models/other.py": {"executed_lines": [1]}}
        self.assertEqual(ccg.covered_lines_for_file(files, "addons/sale/models/sale.py"), set())


# ---------------------------------------------------------------------------
# evaluate_target
# ---------------------------------------------------------------------------

class TestEvaluateTarget(unittest.TestCase):

    def test_fully_covered_by_real_test_ok_true(self):
        target = _target(changed_exec_lines=[10, 11, 12], risk="normal")
        files = {
            "addons/sale/models/sale.py": {
                "executed_lines": [10, 11, 12],
                "contexts": {
                    "10": ["t.test_a"], "11": ["t.test_a"], "12": ["t.test_a"],
                },
            }
        }
        result = ccg.evaluate_target(target, files)
        self.assertTrue(result["ok"])
        self.assertEqual(result["ratio"], 1.0)
        self.assertEqual(result["missing_changed_exec_lines"], [])

    def test_critical_missing_one_line_fails(self):
        target = _target(changed_exec_lines=[10, 11, 12], risk="critical")
        files = {
            "addons/sale/models/sale.py": {
                "executed_lines": [10, 11],
                "contexts": {"10": ["t.test_a"], "11": ["t.test_a"]},
            }
        }
        result = ccg.evaluate_target(target, files)
        self.assertFalse(result["ok"])
        self.assertEqual(result["threshold"], 1.0)
        self.assertEqual(result["missing_changed_exec_lines"], [12])

    def test_normal_9_of_10_covered_ok(self):
        changed = list(range(1, 11))
        target = _target(changed_exec_lines=changed, risk="normal")
        files = {
            "addons/sale/models/sale.py": {
                "executed_lines": changed[:9],
                "contexts": {str(l): ["t.test_a"] for l in changed[:9]},
            }
        }
        result = ccg.evaluate_target(target, files)
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["ratio"], 0.9)

    def test_normal_8_of_10_covered_fails(self):
        changed = list(range(1, 11))
        target = _target(changed_exec_lines=changed, risk="normal")
        files = {
            "addons/sale/models/sale.py": {
                "executed_lines": changed[:8],
                "contexts": {str(l): ["t.test_a"] for l in changed[:8]},
            }
        }
        result = ccg.evaluate_target(target, files)
        self.assertFalse(result["ok"])
        self.assertAlmostEqual(result["ratio"], 0.8)

    def test_line_covered_only_by_setup_counts_as_missing(self):
        target = _target(changed_exec_lines=[10], risk="critical")
        files = {
            "addons/sale/models/sale.py": {
                "executed_lines": [10],
                "contexts": {"10": ["TestSaleOrder.setUpClass"]},
            }
        }
        result = ccg.evaluate_target(target, files)
        self.assertFalse(result["ok"])
        self.assertEqual(result["missing_changed_exec_lines"], [10])

    def test_suffix_file_path_matching(self):
        target = _target(file="addons/m/models/sale.py", changed_exec_lines=[5], risk="normal")
        files = {
            "/ci/checkout/odoo/addons/m/models/sale.py": {
                "executed_lines": [5],
                "contexts": {"5": ["t.test_x"]},
            }
        }
        result = ccg.evaluate_target(target, files)
        self.assertTrue(result["ok"])

    def test_empty_changed_exec_lines_ok_true_ratio_one(self):
        target = _target(changed_exec_lines=[], risk="critical")
        result = ccg.evaluate_target(target, {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["ratio"], 1.0)

    def test_file_absent_from_coverage_fails(self):
        target = _target(file="addons/sale/models/sale.py", changed_exec_lines=[10], risk="normal")
        files = {"addons/other/models/other.py": {"executed_lines": [1]}}
        result = ccg.evaluate_target(target, files)
        self.assertFalse(result["ok"])
        self.assertEqual(result["covered_changed_exec_lines"], [])

    def test_risk_tier_from_scenario_gen_when_no_explicit_risk(self):
        # account.move matches scenario_gen's critical prefix -> threshold 1.0
        target = _target(model="account.move", file="addons/a/models/account_move.py",
                          changed_exec_lines=[1, 2])
        files = {
            "addons/a/models/account_move.py": {
                "executed_lines": [1],
                "contexts": {"1": ["t.test_a"]},
            }
        }
        result = ccg.evaluate_target(target, files)
        self.assertEqual(result["risk"], "critical")
        self.assertFalse(result["ok"])


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class TestBuildReport(unittest.TestCase):

    def test_gate_pass_when_all_targets_ok(self):
        targets_doc = {"targets": [_target(id="t1", changed_exec_lines=[1], risk="normal")]}
        cov_doc = _cov({
            "addons/sale/models/sale.py": {
                "executed_lines": [1],
                "contexts": {"1": ["t.test_a"]},
            }
        })
        report = ccg.build_report(targets_doc, cov_doc)
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["gate"], "pass")
        self.assertEqual(report["summary"]["targets"], 1)
        self.assertEqual(report["summary"]["fully_covered"], 1)

    def test_gate_fails_if_any_target_fails(self):
        targets_doc = {"targets": [
            _target(id="t1", changed_exec_lines=[1], risk="normal"),
            _target(id="t2", changed_exec_lines=[99], risk="critical",
                     file="addons/sale/models/other.py"),
        ]}
        cov_doc = _cov({
            "addons/sale/models/sale.py": {
                "executed_lines": [1],
                "contexts": {"1": ["t.test_a"]},
            }
        })
        report = ccg.build_report(targets_doc, cov_doc)
        self.assertFalse(report["ok"])
        self.assertEqual(report["summary"]["gate"], "fail")
        self.assertEqual(report["summary"]["targets"], 2)
        self.assertEqual(report["summary"]["fully_covered"], 1)

    def test_missing_targets_key_warns_and_treats_as_empty(self):
        report = ccg.build_report({}, {"files": {}})
        self.assertEqual(report["summary"]["targets"], 0)
        self.assertTrue(report["_warnings"])
        self.assertTrue(report["ok"])


# ---------------------------------------------------------------------------
# main (CLI, filesystem I/O)
# ---------------------------------------------------------------------------

class TestMain(unittest.TestCase):

    def _run_main(self, targets_doc, coverage_doc, capsys=None):
        with tempfile.TemporaryDirectory() as d:
            targets_path = Path(d) / "diff_targets.json"
            coverage_path = Path(d) / "coverage.json"
            targets_path.write_text(json.dumps(targets_doc))
            coverage_path.write_text(json.dumps(coverage_doc))
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                ccg.main(["--targets", str(targets_path), "--coverage", str(coverage_path)])
            return json.loads(buf.getvalue())

    def test_main_happy_path(self):
        targets_doc = {"targets": [_target(id="t1", changed_exec_lines=[1], risk="normal")]}
        coverage_doc = {"files": {
            "addons/sale/models/sale.py": {
                "executed_lines": [1],
                "contexts": {"1": ["t.test_a"]},
            }
        }}
        out = self._run_main(targets_doc, coverage_doc)
        self.assertTrue(out["ok"])

    def test_main_missing_file_returns_ok_false(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ccg.main(["--targets", "/nonexistent/diff_targets.json",
                      "--coverage", "/nonexistent/coverage.json"])
        out = json.loads(buf.getvalue())
        self.assertFalse(out["ok"])
        self.assertTrue(out["_warnings"])


if __name__ == "__main__":
    unittest.main()
