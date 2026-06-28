"""
Unit tests for the pure helpers in entrypoint_surface.py (Layer K — discovery).
Import-safe outside an Odoo shell: run() is gated on `env`, so importing here
exercises only the pure ranking/classification functions.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import entrypoint_surface as S  # noqa: E402  (import-safe: run() gated on `env`)


class IsActionMethodTests(unittest.TestCase):
    def test_action_shaped_kept(self):
        for n in ("action_confirm", "button_validate", "toggle_active",
                  "open_website_url", "print_quotation"):
            self.assertTrue(S.is_action_method(n), n)

    def test_orm_verbs_and_private_dropped(self):
        for n in ("write", "create", "unlink", "read", "_action_confirm",
                  "_compute_amount", "__init__", "action", "open"):
            self.assertFalse(S.is_action_method(n), n)

    def test_empty(self):
        self.assertFalse(S.is_action_method(""))
        self.assertFalse(S.is_action_method(None))


class IsTechnicalModelTests(unittest.TestCase):
    def test_plumbing_models_flagged(self):
        for m in ("ir.cron", "ir.ui.view", "ir.model.data", "bus.bus",
                  "base.automation", "mail.message", "res.config.settings"):
            self.assertTrue(S.is_technical_model(m), m)

    def test_business_models_kept(self):
        for m in ("sale.order", "stock.picking", "account.move", "res.partner",
                  "mrp.production"):
            self.assertFalse(S.is_technical_model(m), m)

    def test_empty_is_technical(self):
        self.assertTrue(S.is_technical_model(""))
        self.assertTrue(S.is_technical_model(None))


class ModuleCentralityTests(unittest.TestCase):
    def test_business_apps_top(self):
        for m in ("sale", "stock", "account", "purchase", "mrp", "crm"):
            self.assertEqual(S.module_centrality(m), 1.0, m)

    def test_localization_prefix(self):
        self.assertEqual(S.module_centrality("l10n_vn"), 1.0)        # l10n_ is prefix-matched
        self.assertEqual(S.module_centrality("payment"), 1.0)       # exact business-app entry
        self.assertEqual(S.module_centrality("payment_stripe"), 0.6)  # provider addon → custom tier

    def test_plumbing_low(self):
        for m in ("base", "web", "bus", "base_import"):
            self.assertEqual(S.module_centrality(m), 0.3, m)

    def test_unknown_custom_middle(self):
        self.assertEqual(S.module_centrality("bm_custom"), 0.6)
        self.assertEqual(S.module_centrality(None), 0.5)


class ScoreEntrypointTests(unittest.TestCase):
    def test_object_button_business_cross_app_ranks_highest(self):
        e = {"type": "object_button", "model": "sale.order", "ref": "action_confirm",
             "module": "sale", "n_relations": 30}
        score, reasons = S.score_entrypoint(e)
        self.assertIn("business-app", reasons)
        self.assertIn("cross-app-flow", reasons)
        # window action on a plumbing model should score strictly lower
        low, _ = S.score_entrypoint({"type": "window_action", "model": "res.lang",
                                     "ref": None, "module": "base"})
        self.assertGreater(score, low)

    def test_inactive_penalised(self):
        active, _ = S.score_entrypoint({"type": "cron", "module": "sale", "active": True})
        inactive, reasons = S.score_entrypoint({"type": "cron", "module": "sale", "active": False})
        self.assertGreater(active, inactive)
        self.assertIn("inactive", reasons)


class RankAndSeedTests(unittest.TestCase):
    def setUp(self):
        self.ents = [
            {"type": "object_button", "model": "sale.order", "method": "action_confirm",
             "ref": "action_confirm", "module": "sale", "n_relations": 20},
            {"type": "object_button", "model": "sale.order", "method": "action_draft",
             "ref": "action_draft", "module": "sale", "n_relations": 20},
            {"type": "window_action", "model": "res.lang", "ref": None, "module": "base"},
            {"type": "route", "ref": "/shop", "module": "website_sale"},
        ]

    def test_rank_orders_and_truncates(self):
        ranked, trunc = S.rank_entrypoints(self.ents, limit=2)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(trunc, 2)
        self.assertEqual(ranked[0]["ref"], "action_confirm")  # cross-app wins
        for e in ranked:
            self.assertIn("rank", e)
            self.assertIn("why", e)

    def test_seeds_only_object_buttons_deduped(self):
        ranked, _ = S.rank_entrypoints(self.ents, limit=0)
        seeds = S.pick_trace_seeds(ranked)
        self.assertTrue(all("model" in s and "method" in s for s in seeds))
        keys = [(s["model"], s["method"]) for s in seeds]
        self.assertEqual(len(keys), len(set(keys)))      # de-duped
        self.assertEqual(seeds[0]["method"], "action_confirm")
        # routes / window actions never become trace seeds
        self.assertTrue(all(s["model"] for s in seeds))

    def test_empty(self):
        ranked, trunc = S.rank_entrypoints([], limit=10)
        self.assertEqual(ranked, [])
        self.assertEqual(trunc, 0)
        self.assertEqual(S.pick_trace_seeds([]), [])


if __name__ == "__main__":
    unittest.main()
