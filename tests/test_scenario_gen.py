"""
Unit tests for scenario_gen pure helpers — import-safe outside an Odoo shell
(run() is gated on `env` so it never executes here).
"""
import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import scenario_gen  # noqa: E402  (import-safe: run() gated on `env`)


class ClassifyRiskTests(unittest.TestCase):
    def test_critical_exact_account_move(self):
        r = scenario_gen.classify_model_risk("account.move")
        self.assertEqual(r["tier"], "critical")
        self.assertTrue(r["reasons"])

    def test_critical_sub_model_bank_statement(self):
        # account.bank.statement.line starts with critical prefix account.bank.statement
        r = scenario_gen.classify_model_risk("account.bank.statement.line")
        self.assertEqual(r["tier"], "critical")

    def test_critical_payment_transaction(self):
        r = scenario_gen.classify_model_risk("payment.transaction")
        self.assertEqual(r["tier"], "critical")

    def test_critical_pos_order(self):
        r = scenario_gen.classify_model_risk("pos.order")
        self.assertEqual(r["tier"], "critical")

    def test_critical_hr_payslip(self):
        r = scenario_gen.classify_model_risk("hr.payslip")
        self.assertEqual(r["tier"], "critical")

    def test_high_sale_order(self):
        r = scenario_gen.classify_model_risk("sale.order")
        self.assertEqual(r["tier"], "high")

    def test_high_purchase_order(self):
        r = scenario_gen.classify_model_risk("purchase.order")
        self.assertEqual(r["tier"], "high")

    def test_account_move_line_is_high_not_critical(self):
        # account.move.line starts with the critical prefix 'account.move' but is
        # explicitly listed as high — high exact-match must take precedence.
        r = scenario_gen.classify_model_risk("account.move.line")
        self.assertEqual(r["tier"], "high")

    def test_normal_res_partner(self):
        r = scenario_gen.classify_model_risk("res.partner")
        self.assertEqual(r["tier"], "normal")

    def test_normal_product_template(self):
        r = scenario_gen.classify_model_risk("product.template")
        self.assertEqual(r["tier"], "normal")


class RequiredScenariosTests(unittest.TestCase):
    def _keys(self, model, methods, **kwargs):
        return [s["key"] for s in scenario_gen.required_scenarios(model, methods, **kwargs)]

    def test_always_includes_non_admin(self):
        self.assertIn("non_admin", self._keys("res.partner", ["read"]))

    def test_always_includes_install_check(self):
        self.assertIn("at_install_vs_post_install", self._keys("res.partner", ["read"]))

    def test_multi_company_present_when_has_company_id(self):
        self.assertIn("multi_company", self._keys("sale.order", ["write"], has_company_id=True))

    def test_multi_company_absent_when_no_company_id(self):
        self.assertNotIn("multi_company", self._keys("sale.order", ["write"], has_company_id=False))

    def test_batch_for_action_confirm(self):
        self.assertIn("batch", self._keys("sale.order", ["action_confirm"], has_company_id=False))

    def test_batch_for_create(self):
        self.assertIn("batch", self._keys("res.partner", ["create"], has_company_id=False))

    def test_batch_for_unlink(self):
        self.assertIn("batch", self._keys("res.partner", ["unlink"], has_company_id=False))

    def test_batch_absent_for_readonly_method(self):
        self.assertNotIn("batch", self._keys("res.partner", ["name_get"], has_company_id=False))

    def test_upgrade_present_for_non_transient(self):
        keys = self._keys("sale.order", ["write"], has_company_id=False, is_transient=False)
        self.assertIn("upgrade_i_and_u", keys)

    def test_upgrade_absent_for_transient(self):
        keys = self._keys("account.payment.register", ["create"],
                          has_company_id=False, is_transient=True)
        self.assertNotIn("upgrade_i_and_u", keys)

    def test_locked_period_for_critical_account_model(self):
        keys = self._keys("account.move", ["action_post"])
        self.assertIn("locked_period", keys)

    def test_locked_period_absent_for_high_model(self):
        # sale.order is high (not critical) → no locked_period
        keys = self._keys("sale.order", ["action_confirm"])
        self.assertNotIn("locked_period", keys)

    def test_locked_period_absent_for_non_account_critical(self):
        # hr.payslip is critical but does NOT start with 'account.'
        keys = self._keys("hr.payslip", ["action_payslip_done"])
        self.assertNotIn("locked_period", keys)

    def test_record_rules_present_when_has_company_id(self):
        self.assertIn("record_rules", self._keys("sale.order", ["write"], has_company_id=True))

    def test_record_rules_absent_when_no_company_id(self):
        self.assertNotIn("record_rules", self._keys("sale.order", ["write"], has_company_id=False))

    def test_each_scenario_has_key_and_why(self):
        for sc in scenario_gen.required_scenarios("account.move", ["action_post"]):
            self.assertIn("key", sc)
            self.assertIn("why", sc)
            self.assertIsInstance(sc["why"], str)
            self.assertTrue(sc["why"])


class RenderSkeletonTests(unittest.TestCase):
    def _build(self, model="sale.order", methods=None, **kwargs):
        if methods is None:
            methods = ["create", "write"]
        scenarios = scenario_gen.required_scenarios(model, methods, **kwargs)
        code = scenario_gen.render_test_skeleton(model, methods, scenarios)
        return code, scenarios

    def test_skeleton_is_syntactically_valid_python(self):
        code, _ = self._build()
        ast.parse(code)  # raises SyntaxError if invalid

    def test_one_test_method_per_scenario(self):
        code, scenarios = self._build()
        for sc in scenarios:
            self.assertIn(f"def test_{sc['key']}(self):", code)

    def test_class_name_from_sale_order(self):
        code, _ = self._build("sale.order", ["write"])
        self.assertIn("class TestSaleOrderScenarios(TransactionCase):", code)

    def test_class_name_from_account_move_line(self):
        scenarios = scenario_gen.required_scenarios("account.move.line", ["write"])
        code = scenario_gen.render_test_skeleton("account.move.line", ["write"], scenarios)
        self.assertIn("class TestAccountMoveLineScenarios(TransactionCase):", code)

    def test_stubs_contain_self_fail(self):
        code, _ = self._build()
        self.assertIn("self.fail(", code)

    def test_tagged_decorator_present(self):
        code, _ = self._build()
        self.assertIn("@tagged('post_install', '-at_install')", code)

    def test_non_admin_setup_present(self):
        code, _ = self._build()
        self.assertIn("setUpClass", code)
        self.assertIn("non_admin", code)

    def test_account_move_skeleton_valid_and_has_locked_period(self):
        code, scenarios = self._build("account.move", ["action_post", "write"])
        ast.parse(code)  # must be valid Python
        keys = [s["key"] for s in scenarios]
        self.assertIn("locked_period", keys)
        self.assertIn("def test_locked_period(self):", code)

    def test_no_extra_tests_beyond_scenarios(self):
        code, scenarios = self._build("res.partner", ["name_get"], has_company_id=False)
        # Count 'def test_' occurrences — must equal scenario count
        test_count = code.count("    def test_")
        self.assertEqual(test_count, len(scenarios))


class BuildReportTests(unittest.TestCase):
    def test_report_has_required_top_level_keys(self):
        r = scenario_gen.build_report("sale.order", ["create"])
        for k in ("model", "methods", "risk", "scenarios", "skeleton", "_caveat"):
            self.assertIn(k, r)

    def test_report_skeleton_is_valid_python(self):
        r = scenario_gen.build_report("account.move", ["action_post"])
        ast.parse(r["skeleton"])

    def test_report_risk_tier_matches_classify(self):
        r = scenario_gen.build_report("hr.payslip", ["write"])
        self.assertEqual(r["risk"]["tier"],
                         scenario_gen.classify_model_risk("hr.payslip")["tier"])

    def test_report_methods_passthrough(self):
        methods = ["create", "action_post"]
        r = scenario_gen.build_report("account.move", methods)
        self.assertEqual(r["methods"], methods)

    def test_caveat_is_non_empty_string(self):
        r = scenario_gen.build_report("res.partner", ["write"])
        self.assertIsInstance(r["_caveat"], str)
        self.assertTrue(r["_caveat"])


if __name__ == "__main__":
    unittest.main()
