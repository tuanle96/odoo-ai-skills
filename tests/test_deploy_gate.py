"""
Unit tests for deploy_gate.py — pure-function tests (in-memory artifacts, no
filesystem I/O except TestBuildReport which uses tempfile).
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

import deploy_gate as dg  # noqa: E402

_ALL_NAMES = ["native_check", "env_diff", "scenarios",
              "validate", "security", "trace", "upgrade"]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _blank_artifacts():
    return {n: None for n in _ALL_NAMES}


def _evidence_with_signals(present=None, missing=None, **signals):
    """Build an evidence dict directly (bypasses assemble_evidence)."""
    if missing is None:
        missing = []
    if present is None:
        present = [n for n in _ALL_NAMES if n not in missing]
    base = {
        "validate_blocking": 0,
        "validate_warning": 0,
        "env_diff_severity": None,
        "upgrade_blocking": 0,
        "scenarios_risk_tier": None,
        "security_superuser": False,
        "security_has_warnings": False,
        "trace_error": None,
        "sensitive_model": False,
    }
    base.update(signals)
    return {"present": present, "missing": missing, "signals": base}


def _risk(tier="normal"):
    return {"tier": tier, "reasons": [f"test: {tier}"]}


# ---------------------------------------------------------------------------
# assemble_evidence
# ---------------------------------------------------------------------------

class TestAssembleEvidence(unittest.TestCase):

    def _ev(self, **overrides):
        arts = _blank_artifacts()
        arts.update(overrides)
        return dg.assemble_evidence(arts)

    def test_all_absent_fills_missing(self):
        ev = self._ev()
        self.assertEqual(ev["present"], [])
        self.assertEqual(set(ev["missing"]), set(_ALL_NAMES))

    def test_present_and_missing_split(self):
        ev = self._ev(validate={"summary": {"blocking": 0}})
        self.assertIn("validate", ev["present"])
        self.assertNotIn("validate", ev["missing"])
        self.assertIn("upgrade", ev["missing"])

    def test_validate_blocking_and_warning(self):
        ev = self._ev(validate={"summary": {"blocking": 3, "warning": 1}})
        self.assertEqual(ev["signals"]["validate_blocking"], 3)
        self.assertEqual(ev["signals"]["validate_warning"], 1)

    def test_validate_missing_summary_is_none(self):
        ev = self._ev(validate={"other": "data"})
        self.assertIsNone(ev["signals"]["validate_blocking"])
        self.assertIsNone(ev["signals"]["validate_warning"])

    def test_env_diff_severity(self):
        ev = self._ev(env_diff={"summary": {"severity": "high"}})
        self.assertEqual(ev["signals"]["env_diff_severity"], "high")

    def test_upgrade_blocking(self):
        ev = self._ev(upgrade={"summary": {"blocking": 2}})
        self.assertEqual(ev["signals"]["upgrade_blocking"], 2)

    def test_scenarios_risk_tier(self):
        ev = self._ev(scenarios={"risk": {"tier": "critical"}})
        self.assertEqual(ev["signals"]["scenarios_risk_tier"], "critical")

    def test_security_superuser_true(self):
        ev = self._ev(security={"is_superuser": True})
        self.assertTrue(ev["signals"]["security_superuser"])

    def test_security_superuser_false(self):
        ev = self._ev(security={"is_superuser": False})
        self.assertFalse(ev["signals"]["security_superuser"])

    def test_security_warnings_flag(self):
        ev = self._ev(security={"_warnings": ["w1", "w2"]})
        self.assertTrue(ev["signals"]["security_has_warnings"])

    def test_security_no_warnings(self):
        ev = self._ev(security={"_warnings": []})
        self.assertFalse(ev["signals"]["security_has_warnings"])

    def test_trace_error_captured(self):
        ev = self._ev(trace={"error": "TypeError: bad"})
        self.assertEqual(ev["signals"]["trace_error"], "TypeError: bad")

    def test_trace_no_error(self):
        ev = self._ev(trace={"result": "ok"})
        self.assertIsNone(ev["signals"]["trace_error"])

    def test_sensitive_model_from_security(self):
        ev = self._ev(security={"model": "account.move"})
        self.assertTrue(ev["signals"]["sensitive_model"])

    def test_sensitive_model_from_scenarios(self):
        ev = self._ev(scenarios={"model": "stock.picking"})
        self.assertTrue(ev["signals"]["sensitive_model"])

    def test_sensitive_model_payment(self):
        ev = self._ev(security={"model": "payment.transaction"})
        self.assertTrue(ev["signals"]["sensitive_model"])

    def test_sensitive_model_hr(self):
        ev = self._ev(security={"model": "hr.employee"})
        self.assertTrue(ev["signals"]["sensitive_model"])

    def test_non_sensitive_model(self):
        ev = self._ev(security={"model": "sale.order"})
        self.assertFalse(ev["signals"]["sensitive_model"])

    def test_tolerates_all_none(self):
        ev = dg.assemble_evidence(_blank_artifacts())
        self.assertIn("signals", ev)


# ---------------------------------------------------------------------------
# classify_change_risk
# ---------------------------------------------------------------------------

class TestClassifyChangeRisk(unittest.TestCase):

    def test_critical_upgrade_blocking(self):
        ev = _evidence_with_signals(upgrade_blocking=1)
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "critical")
        self.assertTrue(any("upgrade" in reason for reason in r["reasons"]))

    def test_critical_validate_blocking(self):
        ev = _evidence_with_signals(validate_blocking=2)
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "critical")
        self.assertTrue(any("validate" in reason for reason in r["reasons"]))

    def test_critical_env_diff_high(self):
        ev = _evidence_with_signals(env_diff_severity="high")
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "critical")
        self.assertTrue(any("env_diff" in reason for reason in r["reasons"]))

    def test_critical_scenarios_tier(self):
        ev = _evidence_with_signals(scenarios_risk_tier="critical")
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "critical")

    def test_high_scenarios_tier(self):
        ev = _evidence_with_signals(scenarios_risk_tier="high")
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "high")

    def test_high_validate_warning(self):
        ev = _evidence_with_signals(validate_warning=3)
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "high")

    def test_high_security_superuser(self):
        ev = _evidence_with_signals(security_superuser=True)
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "high")

    def test_high_security_warnings(self):
        ev = _evidence_with_signals(security_has_warnings=True)
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "high")

    def test_normal_no_signals(self):
        ev = _evidence_with_signals()
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "normal")
        self.assertTrue(r["reasons"])

    def test_critical_dominates_high_signals(self):
        ev = _evidence_with_signals(upgrade_blocking=1, security_superuser=True)
        r = dg.classify_change_risk(ev)
        self.assertEqual(r["tier"], "critical")

    def test_reasons_always_non_empty(self):
        for kwargs in ({}, {"validate_blocking": 1}, {"scenarios_risk_tier": "high"}):
            ev = _evidence_with_signals(**kwargs)
            r = dg.classify_change_risk(ev)
            self.assertTrue(r["reasons"], "reasons must never be empty")


# ---------------------------------------------------------------------------
# gate_decision
# ---------------------------------------------------------------------------

class TestGateDecision(unittest.TestCase):

    def test_block_on_validate_blocking(self):
        ev = _evidence_with_signals(validate_blocking=1)
        d = dg.gate_decision(ev, _risk("normal"))
        self.assertEqual(d["decision"], "block")
        self.assertTrue(d["blocking_findings"])

    def test_block_on_upgrade_blocking(self):
        ev = _evidence_with_signals(upgrade_blocking=2)
        d = dg.gate_decision(ev, _risk("normal"))
        self.assertEqual(d["decision"], "block")
        self.assertTrue(any("upgrade" in f for f in d["blocking_findings"]))

    def test_block_on_trace_error(self):
        ev = _evidence_with_signals(trace_error="RuntimeError: exploded")
        d = dg.gate_decision(ev, _risk("normal"))
        self.assertEqual(d["decision"], "block")
        self.assertTrue(any("trace" in f for f in d["blocking_findings"]))

    def test_needs_human_critical_tier(self):
        ev = _evidence_with_signals()
        d = dg.gate_decision(ev, _risk("critical"))
        self.assertEqual(d["decision"], "needs_human")

    def test_needs_human_high_tier(self):
        ev = _evidence_with_signals()
        d = dg.gate_decision(ev, _risk("high"))
        self.assertEqual(d["decision"], "needs_human")

    def test_needs_human_missing_all_required(self):
        ev = _evidence_with_signals(missing=["native_check", "scenarios", "validate"])
        d = dg.gate_decision(ev, _risk("normal"))
        self.assertEqual(d["decision"], "needs_human")
        self.assertIn("native_check", d["missing_evidence"])
        self.assertIn("scenarios",    d["missing_evidence"])
        self.assertIn("validate",     d["missing_evidence"])

    def test_needs_human_partial_required_missing(self):
        ev = _evidence_with_signals(missing=["validate"])
        d = dg.gate_decision(ev, _risk("normal"))
        self.assertEqual(d["decision"], "needs_human")
        self.assertEqual(d["missing_evidence"], ["validate"])

    def test_approve_normal_risk_all_present_no_blocking(self):
        ev = _evidence_with_signals(missing=[])
        d = dg.gate_decision(ev, _risk("normal"))
        self.assertEqual(d["decision"], "approve")
        self.assertEqual(d["blocking_findings"], [])
        self.assertEqual(d["missing_evidence"],  [])

    def test_block_takes_precedence_over_critical(self):
        ev = _evidence_with_signals(validate_blocking=1)
        d = dg.gate_decision(ev, _risk("critical"))
        self.assertEqual(d["decision"], "block")

    def test_critical_adds_senior_signoff(self):
        ev = _evidence_with_signals()
        d = dg.gate_decision(ev, _risk("critical"))
        self.assertIn("senior Odoo dev sign-off", d["required_approvals"])

    def test_critical_sensitive_model_adds_finance_signoff(self):
        ev = _evidence_with_signals(sensitive_model=True)
        d = dg.gate_decision(ev, _risk("critical"))
        self.assertIn("finance/ops owner sign-off", d["required_approvals"])

    def test_critical_non_sensitive_no_finance_signoff(self):
        ev = _evidence_with_signals(sensitive_model=False)
        d = dg.gate_decision(ev, _risk("critical"))
        self.assertNotIn("finance/ops owner sign-off", d["required_approvals"])

    def test_high_tier_no_required_approvals(self):
        ev = _evidence_with_signals()
        d = dg.gate_decision(ev, _risk("high"))
        self.assertNotIn("senior Odoo dev sign-off", d["required_approvals"])

    def test_approve_all_collections_empty(self):
        ev = _evidence_with_signals(missing=[])
        d = dg.gate_decision(ev, _risk("normal"))
        self.assertEqual(d["required_approvals"], [])
        self.assertEqual(d["blocking_findings"],  [])
        self.assertEqual(d["missing_evidence"],   [])


# ---------------------------------------------------------------------------
# build_report — integration tests using tempfile
# ---------------------------------------------------------------------------

class TestBuildReport(unittest.TestCase):

    def test_approve_key_evidence_present_and_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "native_check.json").write_text(json.dumps({"confirmed_candidates": []}))
            (p / "scenarios.json").write_text(json.dumps({"risk": {"tier": "normal"}}))
            (p / "validate.json").write_text(
                json.dumps({"summary": {"blocking": 0, "warning": 0}}))
            report = dg.build_report(p)
        self.assertEqual(report["decision"]["decision"], "approve")
        self.assertEqual(report["risk"]["tier"], "normal")
        self.assertIn("_caveat", report)
        self.assertEqual(report["_warnings"], [])

    def test_block_when_upgrade_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "native_check.json").write_text(json.dumps({}))
            (p / "scenarios.json").write_text(json.dumps({"risk": {"tier": "normal"}}))
            (p / "validate.json").write_text(
                json.dumps({"summary": {"blocking": 0, "warning": 0}}))
            (p / "upgrade.json").write_text(json.dumps({"summary": {"blocking": 1}}))
            report = dg.build_report(p)
        self.assertEqual(report["decision"]["decision"], "block")
        self.assertEqual(report["risk"]["tier"], "critical")

    def test_needs_human_when_required_artifacts_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = dg.build_report(tmp)
        self.assertEqual(report["decision"]["decision"], "needs_human")
        missing = set(report["decision"]["missing_evidence"])
        self.assertTrue({"native_check", "scenarios", "validate"} <= missing)

    def test_parse_error_logs_warning_treats_as_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "validate.json").write_text("not { valid json }")
            report = dg.build_report(p)
        self.assertTrue(any("validate.json" in w for w in report["_warnings"]))
        self.assertIn("validate", report["evidence"]["missing"])

    def test_bundle_dir_in_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = dg.build_report(tmp)
        self.assertIn(str(tmp), report["bundle_dir"])

    def test_report_top_level_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = dg.build_report(tmp)
        for key in ("bundle_dir", "evidence", "risk", "decision", "_warnings", "_caveat"):
            self.assertIn(key, report)

    def test_evidence_structure_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = dg.build_report(tmp)
        ev = report["evidence"]
        self.assertIn("present",  ev)
        self.assertIn("missing",  ev)
        self.assertIn("signals",  ev)

    def test_needs_human_via_env_diff_high(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "native_check.json").write_text(json.dumps({}))
            (p / "scenarios.json").write_text(json.dumps({"risk": {"tier": "normal"}}))
            (p / "validate.json").write_text(
                json.dumps({"summary": {"blocking": 0, "warning": 0}}))
            (p / "env_diff.json").write_text(json.dumps({"summary": {"severity": "high"}}))
            report = dg.build_report(p)
        self.assertEqual(report["risk"]["tier"], "critical")
        self.assertEqual(report["decision"]["decision"], "needs_human")


if __name__ == "__main__":
    unittest.main()
