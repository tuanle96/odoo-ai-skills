"""
Unit tests for claim_verify.py (BYO-index claim verifier) pure helpers.
Import-safe: run() is gated on `env`. native_check resolves because we put the
scripts dir on sys.path first (same as the shell's SCRIPTS_DIR).
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import claim_verify as cv  # noqa: E402


class ClaimTargetTests(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(cv.claim_target({"model": "sale.order", "field": "x"}), "sale.order.x")
        self.assertEqual(cv.claim_target({"model": "sale.order", "method": "action_confirm"}),
                         "sale.order.action_confirm()")
        self.assertEqual(cv.claim_target({"model": "sale.order"}), "sale.order")
        self.assertEqual(cv.claim_target({"xmlid": "base.main_company"}), "base.main_company")
        self.assertEqual(cv.claim_target({"claim": "use a wizard"}), "use a wizard")


class ClaimToProbeTests(unittest.TestCase):
    def test_explicit_probe_wins(self):
        p = {"kind": "selection_has_value", "model": "sale.order", "field": "state", "value": "sale"}
        self.assertEqual(cv.claim_to_probe({"probe": p}), p)

    def test_invalid_explicit_probe_falls_through(self):
        c = {"probe": {"kind": "not_a_kind"}, "model": "sale.order"}
        self.assertEqual(cv.claim_to_probe(c), {"kind": "model_exists", "model": "sale.order"})

    def test_inference_priority(self):
        self.assertEqual(cv.claim_to_probe({"model": "m", "field": "f"}),
                         {"kind": "field_exists", "model": "m", "field": "f"})
        self.assertEqual(cv.claim_to_probe({"model": "m", "method": "x"}),
                         {"kind": "method_exists", "model": "m", "method": "x"})
        self.assertEqual(cv.claim_to_probe({"xmlid": "a.b"}),
                         {"kind": "xmlid_exists", "xmlid": "a.b"})
        self.assertEqual(cv.claim_to_probe({"model": "m"}), {"kind": "model_exists", "model": "m"})
        self.assertIsNone(cv.claim_to_probe({"claim": "subjective"}))


class NeedsRuntimeTests(unittest.TestCase):
    def test_behaviour_claims_need_runtime(self):
        for txt in ["safe override point", "the right hook", "calls super()",
                    "reacts on confirm", "needs @api.depends", "runtime order"]:
            self.assertTrue(cv.needs_runtime({"claim": txt}), txt)

    def test_existence_claims_do_not(self):
        for txt in ["this field exists", "model is present", ""]:
            self.assertFalse(cv.needs_runtime({"claim": txt}), txt)


class ClassifyTests(unittest.TestCase):
    def test_no_probe(self):
        self.assertEqual(cv.classify({"claim": "best practice"}, None, False), "needs_human")
        self.assertEqual(cv.classify({}, None, False), "absent")

    def test_probe_outcomes(self):
        probe = {"kind": "field_exists", "model": "m", "field": "f"}
        self.assertEqual(cv.classify({}, probe, False), "contradicted")
        self.assertEqual(cv.classify({"claim": "field exists"}, probe, True), "confirmed")
        self.assertEqual(cv.classify({"claim": "safe override point"}, probe, True), "needs_shell")


class RecommendTests(unittest.TestCase):
    def test_recommendations(self):
        self.assertIn("brief", cv.recommend_for("needs_shell", {"model": "sale.order", "method": "x"}))
        self.assertIn("do NOT", cv.recommend_for("contradicted", {}))
        self.assertIsNotNone(cv.recommend_for("needs_human", {}))
        self.assertIsNone(cv.recommend_for("confirmed", {}))


class PipelineTests(unittest.TestCase):
    """End-to-end over the pure path using a fake checker (no Odoo)."""
    def test_eval_probe_with_fake_registry(self):
        import native_check as nc
        present = {("sale.order", "commitment_date")}
        handlers = {"field_exists": lambda l: (
            (l["model"], l["field"]) in present,
            {"check": f"{l['model']}.{l['field']}", "found": (l["model"], l["field"]) in present})}

        def checker(leaf):
            return nc.dispatch_leaf(leaf, handlers)

        ok, _ = nc.eval_probe(cv.claim_to_probe({"model": "sale.order", "field": "commitment_date"}), checker)
        self.assertEqual(cv.classify({}, {"kind": "field_exists"}, ok), "confirmed")
        ok2, _ = nc.eval_probe(cv.claim_to_probe({"model": "sale.order", "field": "x_made_up"}), checker)
        self.assertEqual(cv.classify({}, {"kind": "field_exists"}, ok2), "contradicted")


class V091Tests(unittest.TestCase):
    """v0.9.1: probe errors ≠ contradictions; method/hook claims default needs_shell."""

    def test_unevaluable_probe_is_needs_human_not_contradicted(self):
        import native_check as nc

        def boom(_leaf):
            raise KeyError("field")
        checker = lambda leaf: nc.dispatch_leaf(leaf, {"field_exists": boom})
        probe = {"kind": "field_exists", "model": "m"}  # missing 'field' → handler errors
        passed, ev = nc.eval_probe(probe, checker)
        eval_ok = not any(e.get("status") in ("error", "unknown_kind") for e in ev)
        self.assertFalse(eval_ok)
        self.assertEqual(cv.classify({"claim": "x"}, probe, passed, eval_ok), "needs_human")

    def test_unknown_kind_marks_status(self):
        import native_check as nc
        _passed, ev = nc.dispatch_leaf({"kind": "no_such_kind"}, {})
        self.assertEqual(ev.get("status"), "unknown_kind")

    def test_method_claim_defaults_needs_shell(self):
        self.assertEqual(cv.classify({"claim": "exists"}, {"kind": "method_exists"}, True),
                         "needs_shell")

    def test_claim_type_existence_short_circuits(self):
        self.assertEqual(cv.classify({"claim_type": "existence"}, {"kind": "method_exists"}, True),
                         "confirmed")

    def test_claim_type_security_forces_needs_shell(self):
        self.assertEqual(cv.classify({"claim_type": "security"}, {"kind": "field_exists"}, True),
                         "needs_shell")


if __name__ == "__main__":
    unittest.main()
