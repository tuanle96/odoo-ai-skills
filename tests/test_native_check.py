"""
Unit tests for native_check (Layer H gate-then-rank) pure helpers, and a
validator for the shipped curated capability cards. Import-safe outside an Odoo
shell (run() is gated on `env`); the card validation needs no Odoo either.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
CARDS_DIR = REPO_ROOT / "skills" / "odoo-capabilities" / "references" / "cards"
sys.path.insert(0, str(SCRIPTS_DIR))

import native_check  # noqa: E402  (import-safe: run() gated on `env`)


class PureHelperTests(unittest.TestCase):
    def test_strip_diacritics_vietnamese(self):
        self.assertEqual(native_check.strip_diacritics("Đặt cọc"), "dat coc")
        self.assertEqual(native_check.strip_diacritics("Hóa đơn"), "hoa don")

    def test_tokenize(self):
        self.assertEqual(native_check.tokenize("Khi đặt cọc cho đơn hàng"),
                         ["dat", "coc", "don", "hang"])
        self.assertEqual(native_check.tokenize("sale.order"), ["sale", "order"])

    def test_recall_score(self):
        card = {"id": "x", "title": "Down payment", "domain": "sale",
                "primitive": "wizard", "intents": ["down payment", "đặt cọc"]}
        self.assertGreaterEqual(native_check.recall_score("a down payment please", card), 2)
        self.assertGreaterEqual(native_check.recall_score("hóa đơn đặt cọc", card), 2)
        self.assertEqual(native_check.recall_score("delivery address", card), 0)

    def test_eval_probe(self):
        def checker(leaf):
            ok = leaf.get("model") == "good.model"
            return ok, {"check": leaf.get("model"), "found": ok}
        ok, ev = native_check.eval_probe(
            {"any": [{"model": "bad"}, {"model": "good.model"}]}, checker)
        self.assertTrue(ok)
        self.assertEqual(len(ev), 2)
        ok2, _ = native_check.eval_probe(
            {"all": [{"model": "good.model"}, {"model": "bad"}]}, checker)
        self.assertFalse(ok2)


class ShippedCardCorpusTests(unittest.TestCase):
    """Guards the curated cards in always-on CI (tests.yml)."""
    VALID_KINDS = {"module_installed", "model_exists", "field_exists", "method_exists"}
    REQUIRED = ("id", "title", "domain", "primitive", "intents", "modules",
                "models", "reuse_advice", "when_not_enough", "probe")

    @classmethod
    def _probe_kinds(cls, p):
        if "any" in p:
            return [k for s in p["any"] for k in cls._probe_kinds(s)]
        if "all" in p:
            return [k for s in p["all"] for k in cls._probe_kinds(s)]
        return [p.get("kind")]

    def test_corpus_loads_clean(self):
        cards, warns = native_check.load_cards(str(CARDS_DIR))
        self.assertEqual(warns, [], f"card warnings: {warns}")
        self.assertGreaterEqual(len(cards), 30)

    def test_every_card_valid_and_unique(self):
        cards, _ = native_check.load_cards(str(CARDS_DIR))
        seen = set()
        for c in cards:
            for key in self.REQUIRED:
                self.assertIn(key, c, f"{c.get('id')} missing {key}")
            self.assertNotIn(c["id"], seen, f"duplicate id {c['id']}")
            seen.add(c["id"])
            self.assertIsInstance(c["intents"], list)
            self.assertGreaterEqual(len(c["intents"]), 3, c["id"])
            for k in self._probe_kinds(c["probe"]):
                self.assertIn(k, self.VALID_KINDS, f"{c['id']}: bad probe kind {k}")


if __name__ == "__main__":
    unittest.main()
