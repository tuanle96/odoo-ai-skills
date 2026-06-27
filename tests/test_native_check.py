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

    def test_dispatch_leaf_routes_and_is_defensive(self):
        handlers = {
            "model_exists": lambda l: (l["model"] == "ok.model",
                                       {"check": l["model"], "found": l["model"] == "ok.model"}),
        }
        warns = []
        ok, _ = native_check.dispatch_leaf(
            {"kind": "model_exists", "model": "ok.model"}, handlers, on_error=warns.append)
        self.assertTrue(ok)
        # unknown kind -> False + a surfaced warning, never a crash
        ok2, _ = native_check.dispatch_leaf({"kind": "nope"}, handlers, on_error=warns.append)
        self.assertFalse(ok2)
        self.assertTrue(any("unknown probe kind" in w for w in warns))

        # a handler that raises is caught and reported, not propagated
        def _boom(_leaf):
            raise KeyError("missing field")
        ok3, ev3 = native_check.dispatch_leaf(
            {"kind": "boom"}, {"boom": _boom}, on_error=warns.append)
        self.assertFalse(ok3)
        self.assertFalse(ev3["found"])

    def test_eval_probe_with_extended_kinds(self):
        # the new (v0.8) probe grammar composes through any/all + dispatch_leaf
        handlers = {
            "edition": lambda l: (l["edition"] == "enterprise",
                                  {"check": "edition", "found": l["edition"] == "enterprise"}),
            "sequence_exists": lambda l: (l["code"] == "sale.order",
                                          {"check": "seq", "found": l["code"] == "sale.order"}),
            "mixin_inherited": lambda l: (l["mixin"] == "mail.thread",
                                          {"check": "mixin", "found": l["mixin"] == "mail.thread"}),
        }
        def checker(leaf):
            return native_check.dispatch_leaf(leaf, handlers)
        passing = {"all": [{"kind": "sequence_exists", "code": "sale.order"},
                           {"kind": "mixin_inherited", "mixin": "mail.thread"}]}
        ok, ev = native_check.eval_probe(passing, checker)
        self.assertTrue(ok)
        self.assertEqual(len(ev), 2)
        failing = {"all": [{"kind": "edition", "edition": "community"},
                           {"kind": "sequence_exists", "code": "sale.order"}]}
        self.assertFalse(native_check.eval_probe(failing, checker)[0])


class TfidfAndLearningTests(unittest.TestCase):
    def test_tfidf_cosine(self):
        cards = [{"id": "a", "title": "down payment", "domain": "sale", "primitive": "w", "intents": ["down payment"]},
                 {"id": "b", "title": "scrap", "domain": "stock", "primitive": "w", "intents": ["scrap"]}]
        idf = native_check.corpus_idf(cards)
        v = native_check.tfidf_vector(["down", "payment"], idf)
        self.assertAlmostEqual(native_check.cosine(v, v), 1.0)
        self.assertEqual(native_check.cosine(native_check.tfidf_vector(["down"], idf),
                                             native_check.tfidf_vector(["scrap"], idf)), 0.0)

    def test_match_cards_ranks_and_empty_for_nonsense(self):
        cards = [{"id": "sale.dp", "title": "Down payment", "domain": "sale", "primitive": "w",
                  "intents": ["down payment", "đặt cọc"]},
                 {"id": "stock.scrap", "title": "Scrap", "domain": "stock", "primitive": "w",
                  "intents": ["scrap goods"]}]
        out = native_check.match_cards("hóa đơn đặt cọc", cards, top_k=5)
        self.assertEqual(out[0][1]["id"], "sale.dp")
        self.assertEqual(native_check.match_cards("xyzzy plugh frobnicate", cards), [])

    def test_merge_learned_round_trip(self):
        cards = [{"id": "u.act", "title": "t", "domain": "u", "primitive": "m",
                  "intents": ["reminder"], "probe": {}}]
        req = "ping the rep about stalled opportunities"
        self.assertEqual(native_check.match_cards(req, cards), [])
        merged, _ = native_check.merge_learned(
            [dict(c, intents=list(c["intents"])) for c in cards],
            [{"id": "u.act", "learned_intents": [req]}])
        out = native_check.match_cards(req, merged, top_k=2)
        self.assertTrue(out and out[0][1]["id"] == "u.act")


class ShippedCardCorpusTests(unittest.TestCase):
    """Guards the curated cards in always-on CI (tests.yml)."""
    VALID_KINDS = set(native_check.PROBE_KINDS)
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
