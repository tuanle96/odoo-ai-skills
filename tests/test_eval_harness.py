"""
Unit tests for the pure scoring helpers in eval_harness.py (Layer K — hallucination
eval) and integrity of the shipped benchmark. Import-safe: run() is gated on `env`.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import eval_harness as EV  # noqa: E402
import native_check as NC  # noqa: E402  (for PROBE_KINDS — benchmark integrity)

BENCHMARK = SCRIPTS_DIR.parent / "references" / "eval-benchmark.json"


class ClassifyCaseTests(unittest.TestCase):
    def test_present(self):
        self.assertEqual(EV.classify_case("present", True), "truth_confirmed")
        self.assertEqual(EV.classify_case("present", False), "truth_missed")

    def test_absent(self):
        self.assertEqual(EV.classify_case("absent", False), "hallucination_caught")
        self.assertEqual(EV.classify_case("absent", True), "hallucination_leaked")


class ScoreAndMetricsTests(unittest.TestCase):
    def _ev(self, *triples):
        # (expected, found, applicable) -> evaluated case
        out = []
        for expected, found, applicable in triples:
            v = EV.classify_case(expected, found) if applicable else "skipped"
            out.append({"expected": expected, "found": found,
                        "applicable": applicable, "verdict": v, "category": "model"})
        return out

    def test_perfect_gate_is_sound(self):
        ev = self._ev(("present", True, True), ("present", True, True),
                      ("absent", False, True), ("absent", False, True),
                      ("present", True, False))  # skipped (module absent)
        conf = EV.score_cases(ev)
        self.assertEqual(conf["truth_confirmed"], 2)
        self.assertEqual(conf["hallucination_caught"], 2)
        self.assertEqual(conf["skipped"], 1)
        m = EV.compute_metrics(conf)
        self.assertEqual(m["detection_rate"], 1.0)
        self.assertEqual(m["truth_recall"], 1.0)
        self.assertTrue(m["gate_sound"])
        self.assertEqual(m["applicable"], 4)

    def test_leak_makes_gate_unsound(self):
        ev = self._ev(("absent", True, True),    # leak!
                      ("absent", False, True),
                      ("present", True, True))
        conf = EV.score_cases(ev)
        m = EV.compute_metrics(conf)
        self.assertEqual(conf["hallucination_leaked"], 1)
        self.assertEqual(m["detection_rate"], 0.5)
        self.assertFalse(m["gate_sound"])

    def test_miss_makes_gate_unsound(self):
        ev = self._ev(("present", False, True),  # real rejected
                      ("absent", False, True))
        m = EV.compute_metrics(EV.score_cases(ev))
        self.assertEqual(m["truth_recall"], 0.0)
        self.assertFalse(m["gate_sound"])

    def test_empty(self):
        m = EV.compute_metrics(EV.score_cases([]))
        self.assertIsNone(m["detection_rate"])
        self.assertIsNone(m["truth_recall"])
        self.assertTrue(m["gate_sound"])      # vacuously: no leaks, no misses

    def test_per_category_split(self):
        ev = [{"expected": "absent", "found": False, "applicable": True,
               "verdict": "hallucination_caught", "category": "field"},
              {"expected": "present", "found": True, "applicable": True,
               "verdict": "truth_confirmed", "category": "method"}]
        cats = EV.per_category(ev)
        self.assertIn("field", cats)
        self.assertIn("method", cats)
        self.assertEqual(cats["field"]["detection_rate"], 1.0)


class BenchmarkIntegrityTests(unittest.TestCase):
    """The shipped benchmark must be well-formed: known probe kinds, valid labels,
    balanced (has both present and absent), and the classic hallucinations present."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(BENCHMARK.read_text())
        cls.cases = cls.data["cases"]

    def test_probe_kinds_known(self):
        for c in self.cases:
            kind = c["probe"]["kind"]
            self.assertIn(kind, NC.PROBE_KINDS, f"{c['id']} uses unknown kind {kind}")

    def test_labels_valid(self):
        for c in self.cases:
            self.assertIn(c["expected"], ("present", "absent"), c["id"])
            self.assertIn("category", c)
            self.assertTrue(c.get("id"))

    def test_balanced_and_has_classics(self):
        labels = [c["expected"] for c in self.cases]
        self.assertGreaterEqual(labels.count("present"), 4)
        self.assertGreaterEqual(labels.count("absent"), 4)
        ids = {c["id"] for c in self.cases}
        # the canonical LLM Odoo hallucinations must be in the benchmark
        for must in ("absent.model.account_invoice", "absent.field.partner_customer_id",
                     "absent.method.fields_view_get"):
            self.assertIn(must, ids)

    def test_unique_ids(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
