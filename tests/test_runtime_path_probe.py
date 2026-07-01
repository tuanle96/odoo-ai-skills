"""
Unit tests for runtime_path_probe.py — Part A (pure decision layer) only.
Import-safe outside an Odoo shell (run() is gated on `env` so it never
executes here).
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import runtime_path_probe as rp  # noqa: E402  (import-safe: run() gated on `env`)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _target(tid="sale.order:action_confirm:sale_order.py:10", model="sale.order",
            method="action_confirm", file="sale_order.py", span=(10, 20)):
    return {
        "id": tid, "model": model, "method": method, "file": file,
        "method_span": list(span), "changed_exec_lines": [12, 13],
    }


def _event(target_id, model="sale.order", method="action_confirm", file="sale_order.py",
           firstlineno=10, uid=2, is_superuser=False, company_id=1, allowed=None,
           rec_len=1, exception=None, self_name="sale.order"):
    return {
        "target_id": target_id, "test": "test_x", "model": model, "method": method,
        "file": file, "firstlineno": firstlineno, "uid": uid, "is_superuser": is_superuser,
        "company_id": company_id, "allowed_company_ids": [1] if allowed is None else allowed,
        "recordset_len": rec_len, "exception": exception, "self_name": self_name,
        "in_registry_mro": True,
    }


def _registry_fact(target_id, in_mro=True, file="sale_order.py", firstlineno=10):
    return {"target_id": target_id, "in_mro": in_mro, "mro_file": file, "mro_firstlineno": firstlineno}


# ---------------------------------------------------------------------------
# evaluate_target
# ---------------------------------------------------------------------------

class EvaluateTargetBoundTests(unittest.TestCase):
    def test_bound_true_when_all_conditions_hold(self):
        t = _target()
        ev = _event(t["id"])
        rf = _registry_fact(t["id"])
        result = rp.evaluate_target(t, [ev], rf)
        self.assertTrue(result["bound"])
        self.assertTrue(result["in_mro"])
        self.assertTrue(result["called"])
        self.assertTrue(result["self_name_match"])
        self.assertTrue(result["covered_changed_lines"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["target_id"], t["id"])


class EvaluateTargetNotInMroTests(unittest.TestCase):
    def test_in_mro_false_blocks_bound_with_mro_reason(self):
        t = _target()
        ev = _event(t["id"])
        rf = _registry_fact(t["id"], in_mro=False)
        result = rp.evaluate_target(t, [ev], rf)
        self.assertFalse(result["bound"])
        self.assertFalse(result["in_mro"])
        self.assertIn("target not in live registry MRO", result["reasons"])

    def test_missing_registry_fact_treated_as_not_in_mro(self):
        t = _target()
        ev = _event(t["id"])
        result = rp.evaluate_target(t, [ev], None)
        self.assertFalse(result["bound"])
        self.assertFalse(result["in_mro"])
        self.assertIn("target not in live registry MRO", result["reasons"])


class EvaluateTargetMockStubTests(unittest.TestCase):
    def test_self_name_mismatch_flags_mock_stub(self):
        t = _target()
        ev = _event(t["id"], self_name="MagicMock")
        rf = _registry_fact(t["id"])
        result = rp.evaluate_target(t, [ev], rf)
        self.assertFalse(result["bound"])
        self.assertFalse(result["self_name_match"])
        self.assertIn(
            "target never called through a recordset of its own model (mock/stub?)",
            result["reasons"],
        )

    def test_self_name_none_also_flags_mock_stub(self):
        t = _target()
        ev = _event(t["id"], self_name=None)
        rf = _registry_fact(t["id"])
        result = rp.evaluate_target(t, [ev], rf)
        self.assertFalse(result["bound"])
        self.assertFalse(result["self_name_match"])

    def test_model_unset_skips_self_name_check(self):
        t = _target(model=None)
        ev = _event(t["id"], self_name="whatever")
        rf = _registry_fact(t["id"])
        result = rp.evaluate_target(t, [ev], rf)
        self.assertTrue(result["self_name_match"])


class EvaluateTargetNoEventsTests(unittest.TestCase):
    def test_no_events_blocks_bound_with_not_entered_reason(self):
        t = _target()
        rf = _registry_fact(t["id"])
        result = rp.evaluate_target(t, [], rf)
        self.assertFalse(result["bound"])
        self.assertFalse(result["called"])
        self.assertIn("no test entered the target method", result["reasons"])


class EvaluateTargetLocationMismatchTests(unittest.TestCase):
    def test_wrong_location_blocks_bound(self):
        t = _target(span=(10, 20))
        ev = _event(t["id"], firstlineno=999)
        rf = _registry_fact(t["id"], firstlineno=999)  # registry also mismatched
        result = rp.evaluate_target(t, [ev], rf)
        self.assertFalse(result["covered_changed_lines"])
        self.assertFalse(result["bound"])
        self.assertIn("call did not resolve to the changed source location", result["reasons"])

    def test_registry_mro_location_can_satisfy_coverage(self):
        # Event's own firstlineno is outside the span, but the registry_fact's
        # mro_firstlineno (the actual live-resolved location) is inside it —
        # the OR-branch should still mark coverage satisfied.
        t = _target(span=(10, 20))
        ev = _event(t["id"], firstlineno=999)
        rf = _registry_fact(t["id"], firstlineno=12)
        result = rp.evaluate_target(t, [ev], rf)
        self.assertTrue(result["covered_changed_lines"])


class EvaluateTargetNoneInputTests(unittest.TestCase):
    def test_none_inputs_do_not_raise(self):
        result = rp.evaluate_target(None, None, None)
        self.assertFalse(result["bound"])
        self.assertIsNone(result["target_id"])
        self.assertIn("no test entered the target method", result["reasons"])


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

class BuildReportSummaryTests(unittest.TestCase):
    def test_summary_counts_and_unbound_targets(self):
        t1 = _target(tid="t1")
        t2 = _target(tid="t2")
        t3 = _target(tid="t3", model="account.move")

        events = [_event("t1"), _event("t2", self_name="MagicMock")]  # t3 has none
        registry_facts = [_registry_fact("t1"), _registry_fact("t2"), _registry_fact("t3", in_mro=False)]

        report = rp.build_report([t1, t2, t3], events, registry_facts)

        self.assertEqual(report["summary"], {"targets": 3, "bound": 1, "unbound": 2})
        self.assertEqual(sorted(report["unbound_targets"]), ["t2", "t3"])
        self.assertEqual(len(report["targets"]), 3)
        self.assertIsInstance(report["summary"]["unbound"], int)
        self.assertIsInstance(report["unbound_targets"], list)


class BuildReportObservationsTests(unittest.TestCase):
    def test_merged_observations_shape_and_aggregation(self):
        t1 = _target(tid="t1")
        t2 = _target(tid="t2", model="account.move")

        events = [
            _event("t1", uid=2, company_id=1, allowed=[1], rec_len=1, exception=None),
            _event("t1", uid=5, company_id=2, allowed=[1, 2], rec_len=3, exception="UserError"),
            _event("t2", uid=2, company_id=2, allowed=[1, 2], rec_len=7, exception="ValidationError"),
        ]
        registry_facts = [_registry_fact("t1"), _registry_fact("t2")]

        report = rp.build_report([t1, t2], events, registry_facts)
        obs = report["observations"]

        expected_keys = {
            "uids_seen", "max_recordset_len", "max_create_vals_len",
            "companies_seen", "allowed_company_sets", "raised_exceptions",
        }
        self.assertEqual(set(obs.keys()), expected_keys)

        self.assertEqual(obs["uids_seen"], [2, 5])
        self.assertEqual(obs["max_recordset_len"], 7)
        self.assertEqual(obs["max_create_vals_len"], 0)
        self.assertEqual(obs["companies_seen"], [1, 2])
        self.assertEqual(obs["allowed_company_sets"], [[1], [1, 2]])
        self.assertEqual(obs["raised_exceptions"], ["UserError", "ValidationError"])


class BuildReportNoneInputTests(unittest.TestCase):
    def test_all_none_inputs_do_not_raise(self):
        report = rp.build_report(None, None, None)
        self.assertEqual(report["summary"], {"targets": 0, "bound": 0, "unbound": 0})
        self.assertEqual(report["targets"], [])
        self.assertEqual(report["unbound_targets"], [])
        self.assertEqual(
            set(report["observations"].keys()),
            {"uids_seen", "max_recordset_len", "max_create_vals_len",
             "companies_seen", "allowed_company_sets", "raised_exceptions"},
        )
        self.assertEqual(report["_warnings"], [])

    def test_empty_lists_do_not_raise(self):
        report = rp.build_report([], [], [])
        self.assertEqual(report["summary"], {"targets": 0, "bound": 0, "unbound": 0})


class BuildReportMalformedInputTests(unittest.TestCase):
    def test_non_dict_entries_are_skipped_not_fatal(self):
        report = rp.build_report(["not-a-dict", None], [123, "x"], [None, 5])
        self.assertEqual(report["summary"], {"targets": 0, "bound": 0, "unbound": 0})


if __name__ == "__main__":
    unittest.main()
