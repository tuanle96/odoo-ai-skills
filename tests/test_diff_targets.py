"""
Unit tests for diff_targets.py — pure-function tests (inline diff text and
in-memory source strings, no git/filesystem I/O).
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import diff_targets as dt  # noqa: E402


# ---------------------------------------------------------------------------
# parse_diff_changed_lines
# ---------------------------------------------------------------------------

class TestParseDiffChangedLines(unittest.TestCase):
    def test_multi_hunk_single_and_multi_line_forms(self):
        diff = """\
diff --git a/sale/models/sale_order.py b/sale/models/sale_order.py
index 1111111..2222222 100644
--- a/sale/models/sale_order.py
+++ b/sale/models/sale_order.py
@@ -10 +10 @@ def foo():
-    old_line
+    new_line
@@ -20,0 +21,3 @@ class SaleOrder(models.Model):
+    line_a
+    line_b
+    line_c
diff --git a/sale/tests/test_sale_order.py b/sale/tests/test_sale_order.py
index 3333333..4444444 100644
--- a/sale/tests/test_sale_order.py
+++ b/sale/tests/test_sale_order.py
@@ -5,0 +6,2 @@ class TestSaleOrder(TransactionCase):
+    def test_foo(self):
+        pass
"""
        result = dt.parse_diff_changed_lines(diff)
        self.assertEqual(result["sale/models/sale_order.py"], {10, 21, 22, 23})
        self.assertNotIn("sale/tests/test_sale_order.py", result)

    def test_deletion_only_hunk_contributes_no_lines(self):
        diff = """\
--- a/foo.py
+++ b/foo.py
@@ -5,2 +5,0 @@ def foo():
-    removed_a
-    removed_b
"""
        result = dt.parse_diff_changed_lines(diff)
        self.assertEqual(result["foo.py"], set())

    def test_test_prefixed_basename_excluded(self):
        diff = """\
--- a/models/test_helpers.py
+++ b/models/test_helpers.py
@@ -1 +1 @@
-old
+new
"""
        result = dt.parse_diff_changed_lines(diff)
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# map_lines_to_targets
# ---------------------------------------------------------------------------

class TestMapLinesToTargets(unittest.TestCase):
    def test_inherit_string_two_methods_only_one_changed(self):
        source = """\
from odoo import models

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        return True

    def action_cancel(self):
        return False
"""
        # Line 6 is `def action_confirm(self):` — changed line 7 is its body.
        targets = dt.map_lines_to_targets(source, {7}, "sale/models/sale_order.py")
        self.assertEqual(len(targets), 1)
        t = targets[0]
        self.assertEqual(t["model"], "sale.order")
        self.assertEqual(t["method"], "action_confirm")
        self.assertEqual(t["changed_exec_lines"], [7])
        self.assertEqual(t["kind"], "model_method")

    def test_name_takes_precedence_over_inherit(self):
        source = """\
from odoo import models

class SaleOrder(models.Model):
    _name = "x.custom"
    _inherit = "sale.order"

    def do_it(self):
        pass
"""
        targets = dt.map_lines_to_targets(source, {7}, "x/models.py")
        self.assertEqual(targets[0]["model"], "x.custom")

    def test_inherit_list_uses_first_element(self):
        source = """\
from odoo import models

class Foo(models.Model):
    _inherit = ["sale.order", "mail.thread"]

    def do_it(self):
        pass
"""
        targets = dt.map_lines_to_targets(source, {6}, "x/models.py")
        self.assertEqual(targets[0]["model"], "sale.order")

    def test_depends_decorator_triggers_compute(self):
        source = """\
from odoo import api, models

class SaleOrder(models.Model):
    _name = "sale.order"

    @api.depends("order_line")
    def _compute_amount(self):
        self.amount = 1
"""
        targets = dt.map_lines_to_targets(source, {7}, "sale/models/sale_order.py")
        self.assertEqual(len(targets), 1)
        self.assertIn("compute", targets[0]["triggers"])

    def test_onchange_decorator_requires_form(self):
        source = """\
from odoo import api, models

class SaleOrder(models.Model):
    _name = "sale.order"

    @api.onchange("partner_id")
    def _onchange_partner(self):
        self.name = "x"
"""
        targets = dt.map_lines_to_targets(source, {7}, "sale/models/sale_order.py")
        self.assertEqual(len(targets), 1)
        t = targets[0]
        self.assertIn("onchange", t["triggers"])
        self.assertTrue(t["requires_form"])

    def test_computed_field_backreference(self):
        source = """\
from odoo import fields, models

class SaleOrder(models.Model):
    _name = "sale.order"

    total = fields.Float(compute="_compute_total")

    def _compute_total(self):
        self.total = 1.0
"""
        # Line 6 is the `total = fields.Float(...)` assignment.
        targets = dt.map_lines_to_targets(source, {6}, "sale/models/sale_order.py")
        computed = [t for t in targets if t["kind"] == "computed_field"]
        self.assertEqual(len(computed), 1)
        self.assertEqual(computed[0]["method"], "_compute_total")
        self.assertEqual(computed[0]["model"], "sale.order")

    def test_module_level_change_outside_any_function(self):
        source = """\
import os

class Foo(models.Model):
    _name = "x.foo"

    def bar(self):
        pass
"""
        # Line 1 is the top-level `import os` — not inside any function/class body.
        targets = dt.map_lines_to_targets(source, {1}, "x/models.py")
        self.assertEqual(targets, [])

    def test_module_level_function_target(self):
        source = """\
def helper():
    return 1
"""
        targets = dt.map_lines_to_targets(source, {2}, "x/utils.py")
        self.assertEqual(len(targets), 1)
        t = targets[0]
        self.assertEqual(t["kind"], "function")
        self.assertIsNone(t["model"])
        self.assertEqual(t["method"], "helper")


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class TestBuildReport(unittest.TestCase):
    def test_aggregates_targets_files_models(self):
        files_map = {
            "sale/models/sale_order.py": {7},
            "purchase/models/purchase_order.py": {7},
        }
        sources = {
            "sale/models/sale_order.py": (
                "from odoo import models\n\n"
                "class SaleOrder(models.Model):\n"
                "    _name = \"sale.order\"\n\n"
                "    def confirm(self):\n"
                "        pass\n"
            ),
            "purchase/models/purchase_order.py": (
                "from odoo import models\n\n"
                "class PurchaseOrder(models.Model):\n"
                "    _name = \"purchase.order\"\n\n"
                "    def confirm(self):\n"
                "        pass\n"
            ),
        }
        report = dt.build_report(files_map, lambda p: sources.get(p))
        self.assertEqual(report["summary"]["targets"], 2)
        self.assertEqual(report["summary"]["files"], 2)
        self.assertEqual(report["summary"]["models"], ["purchase.order", "sale.order"])
        self.assertEqual(report["_warnings"], [])

    def test_warns_on_syntax_error_without_raising(self):
        files_map = {"broken.py": {1}}
        report = dt.build_report(files_map, lambda p: "def broken(:\n    pass\n")
        self.assertEqual(report["targets"], [])
        self.assertEqual(report["summary"]["targets"], 0)
        self.assertEqual(len(report["_warnings"]), 1)
        self.assertIn("broken.py", report["_warnings"][0])

    def test_warns_on_unreadable_file(self):
        files_map = {"missing.py": {1}}
        report = dt.build_report(files_map, lambda p: None)
        self.assertEqual(report["targets"], [])
        self.assertIn("missing.py", report["_warnings"][0])


if __name__ == "__main__":
    unittest.main()
