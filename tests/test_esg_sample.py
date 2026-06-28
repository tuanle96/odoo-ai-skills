"""
Unit tests for the pure helpers in esg_sample.py (Layer K — Execution Surface
Graph). Import-safe: the env-bound tracer runs only inside odoo-bin shell; the
merge/summary logic is pure. esg_sample imports sibling pure helpers, so the
scripts dir must be importable (added below, same as the script does at runtime).
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import esg_sample as E  # noqa: E402  (import-safe: run() gated on `env`)


SEEDS = [
    {"traced": True, "label": "sale.order.action_confirm",
     "touched_models": ["sale.order", "stock.picking", "stock.move"],
     "model_edges": [["sale.order", "stock.picking"], ["stock.picking", "stock.move"],
                     ["sale.order", "stock.picking"]],
     "app_edges": [["sale", "stock"], ["sale", "stock"], ["sale_stock", "stock"]],
     "writes": {"stock.move": {"creates": 1, "writes": 0,
                               "fields": ["product_id", "product_uom_qty"]},
                "sale.order": {"creates": 0, "writes": 1, "fields": ["state"]}}},
    {"traced": True, "label": "account.move.action_post",
     "touched_models": ["account.move", "account.move.line"],
     "model_edges": [["account.move", "account.move.line"]],
     "app_edges": [["account", "account"]],          # same-addon edge is dropped by source
     "writes": {"account.move": {"creates": 0, "writes": 1, "fields": ["state"]}}},
    {"traced": False, "label": "x.y.action_z", "reason": "no record to trace"},
]


class MergeSkeletonsTests(unittest.TestCase):
    def setUp(self):
        self.g = E.merge_skeletons(SEEDS)

    def test_models_unioned_with_provenance(self):
        models = {m["model"]: m["touched_by"] for m in self.g["models"]}
        self.assertIn("stock.move", models)
        self.assertEqual(models["sale.order"], ["sale.order.action_confirm"])
        # account.move touched by its own seed only
        self.assertEqual(models["account.move"], ["account.move.action_post"])

    def test_edges_weighted_and_sorted(self):
        top = self.g["edges"][0]
        self.assertEqual((top["from"], top["to"], top["weight"]),
                         ("sale.order", "stock.picking", 2))
        # every weight is a positive int, sorted descending
        weights = [e["weight"] for e in self.g["edges"]]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_cross_app_edges_weighted(self):
        app = {(e["from"], e["to"]): e["weight"] for e in self.g["app_edges"]}
        self.assertEqual(app[("sale", "stock")], 2)
        self.assertEqual(app[("sale_stock", "stock")], 1)
        # account->account self edge was never emitted as cross-app by the tracer,
        # but if present it still merges; here it is present in app_edges input
        self.assertIn(("account", "account"), app)

    def test_writes_unioned_field_names(self):
        w = self.g["writes"]
        self.assertEqual(w["stock.move"]["creates"], 1)
        self.assertEqual(w["sale.order"]["writes"], 1)
        self.assertEqual(w["sale.order"]["fields"], ["state"])

    def test_untraced_seeds_ignored(self):
        # the failed seed contributes no models/edges
        self.assertNotIn("x.y", [m["model"] for m in self.g["models"]])


class UnsafeMethodTests(unittest.TestCase):
    def test_external_effect_methods_blocked(self):
        for m in ("action_send", "action_quotation_send", "send_mail", "message_post",
                  "action_invoice_send", "_send_sms", "action_capture_payment",
                  "action_refund", "action_print_report", "unlink", "action_cancel",
                  "action_email_invoice"):
            self.assertTrue(E.is_unsafe_to_autotrace(m), m)

    def test_safe_cross_app_flows_allowed(self):
        for m in ("action_confirm", "action_post", "button_validate", "action_done",
                  "action_assign", "button_confirm", "action_draft"):
            self.assertFalse(E.is_unsafe_to_autotrace(m), m)

    def test_empty(self):
        self.assertFalse(E.is_unsafe_to_autotrace(""))
        self.assertFalse(E.is_unsafe_to_autotrace(None))


class SummarizeEsgTests(unittest.TestCase):
    def test_counts(self):
        g = E.merge_skeletons(SEEDS)
        s = E.summarize_esg(g, SEEDS)
        self.assertEqual(s["seeds_considered"], 3)
        self.assertEqual(s["seeds_traced"], 2)
        self.assertEqual(s["seeds_skipped"], 1)
        self.assertEqual(s["models_touched"], 5)   # sale.order, stock.picking, stock.move, account.move, account.move.line
        self.assertGreaterEqual(s["cross_app_edges"], 2)

    def test_empty(self):
        g = E.merge_skeletons([])
        self.assertEqual(g["models"], [])
        self.assertEqual(g["edges"], [])
        s = E.summarize_esg(g, [])
        self.assertEqual(s["seeds_traced"], 0)
        self.assertEqual(s["models_touched"], 0)


if __name__ == "__main__":
    unittest.main()
