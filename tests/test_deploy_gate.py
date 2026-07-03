"""
Unit tests for deploy_gate.py — pure-function tests (in-memory artifacts, no
filesystem I/O except TestBuildReport which uses tempfile).
"""
import importlib.util
import json
import os
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
            (p / "scan_secrets.json").write_text(json.dumps({"count": 0}))  # core-required v0.9.1
            report = dg.build_report(p)
        self.assertEqual(report["decision"]["decision"], "approve")
        self.assertEqual(report["risk"]["tier"], "normal")
        self.assertIn("_caveat", report)
        self.assertEqual(report["_warnings"], [])

    def test_block_when_upgrade_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "native_check.json").write_text(json.dumps({"confirmed_candidates": []}))
            (p / "scenarios.json").write_text(json.dumps({"risk": {"tier": "normal"}}))
            (p / "validate.json").write_text(
                json.dumps({"summary": {"blocking": 0, "warning": 0}}))
            (p / "upgrade.json").write_text(json.dumps({"summary": {"blocking": 1, "warning": 0}}))
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


class V091GateTests(unittest.TestCase):
    """v0.9.1 fixes: glob filenames, scan-secrets, parse-error guard, manifest."""

    def _bundle(self, files):
        d = tempfile.mkdtemp()
        for name, obj in files.items():
            with open(Path(d) / name, "w") as fh:
                fh.write(obj if isinstance(obj, str) else json.dumps(obj))
        return d

    def test_resolves_cli_style_filenames(self):
        d = self._bundle({
            "patch.validate.json": {"summary": {"blocking": 0, "warning": 0}},
            "env.env-diff.json": {"summary": {"severity": "low"}},
            "sale_order.scenarios.json": {"risk": {"tier": "normal"}},
        })
        r = dg.build_report(d)
        self.assertIn("validate", r["evidence"]["present"])
        self.assertIn("env_diff", r["evidence"]["present"])
        self.assertIn("scenarios", r["evidence"]["present"])

    def test_missing_scan_secrets_does_not_approve(self):
        d = self._bundle({
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
            "validate.json": {"summary": {"blocking": 0, "warning": 0}},
        })
        r = dg.build_report(d)
        self.assertEqual(r["decision"]["decision"], "needs_human")
        self.assertIn("scan_secrets", r["decision"]["missing_evidence"])

    def test_multiple_matching_artifacts_use_worst_and_warn(self):
        d = self._bundle({
            "a.validate.json": {"summary": {"blocking": 0, "warning": 0}},
            "b.validate.json": {"summary": {"blocking": 2, "warning": 0}},  # worst must win
        })
        r = dg.build_report(d)
        self.assertEqual(r["evidence"]["signals"]["validate_blocking"], 2)
        self.assertTrue(any("matched" in w for w in r["_warnings"]))
        self.assertEqual(r["decision"]["decision"], "block")

    def test_invalid_schema_required_artifact_does_not_approve(self):
        # empty {} validate has no real signal → must not satisfy required evidence
        d = self._bundle({
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
            "validate.json": {},                       # invalid schema
            "scan_secrets.json": {"count": 0},
        })
        r = dg.build_report(d)
        self.assertEqual(r["decision"]["decision"], "needs_human")
        self.assertIn("validate", r["decision"]["missing_evidence"])

    def test_wrong_typed_fields_do_not_crash_and_do_not_approve(self):
        d = self._bundle({
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
            "validate.json": {"summary": {"blocking": "2", "warning": 0}},  # str, invalid
            "scan_secrets.json": {"count": "2"},                            # str, invalid
        })
        r = dg.build_report(d)  # must not raise
        self.assertNotEqual(r["decision"]["decision"], "approve")

    def test_scan_secrets_blocks(self):
        d = self._bundle({"scan_secrets.json": {"count": 2, "hits": [{"kind": "aws_key"}]}})
        r = dg.build_report(d)
        self.assertEqual(r["decision"]["decision"], "block")

    def test_parse_error_never_approves(self):
        d = self._bundle({
            "validate.json": "{not valid json",
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
        })
        r = dg.build_report(d)
        self.assertNotEqual(r["decision"]["decision"], "approve")
        self.assertTrue(r["_warnings"])

    def _clean_core(self, extra):
        files = {
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
            "validate.json": {"summary": {"blocking": 0, "warning": 0}},
            "scan_secrets.json": {"count": 0},
        }
        files.update(extra)
        return dg.build_report(self._bundle(files))

    def test_string_changed_files_does_not_silently_approve(self):
        # round-4 #2: a string (not list) changed_files would be char-iterated and
        # miss `migrations/` — a malformed manifest must force needs_human
        r = self._clean_core({"manifest.json": {"changed_files": "migrations/17.0.1/post.py"}})
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_non_dict_manifest_does_not_crash(self):
        for bad in ("x", [1], 5):
            r = self._clean_core({"manifest.json": bad})  # must not raise
            self.assertNotEqual(r["decision"]["decision"], "approve")

    def test_list_manifest_with_migration_requires_upgrade(self):
        r = self._clean_core({"manifest.json": {"changed_files": ["migrations/17.0.1/post.py"]}})
        self.assertIn("upgrade", r["required_evidence"])

    def test_manifest_nonstring_or_nonbool_fields_not_trusted(self):
        # round-5: present-but-wrong-typed fields must force needs_human, never approve
        for bad in ({"changed_files": [123]}, {"touched_models": [1, 2]},
                    {"has_migration": ""}, {"touches_security": []},
                    {"touches_controller": 1}):
            r = self._clean_core({"manifest.json": bad})
            self.assertEqual(r["decision"]["decision"], "needs_human", bad)

    def test_valid_manifest_shapes_still_approve(self):
        for ok in ({}, {"changed_files": ["m/models/x.py"], "has_migration": False},
                   {"touched_models": ["sale.order"]}):
            r = self._clean_core({"manifest.json": ok})
            self.assertEqual(r["decision"]["decision"], "approve", ok)

    def test_required_approval_forces_needs_human_not_approve(self):
        # round-6 #1: decision must never say approve while a sign-off is required
        r = self._clean_core({"manifest.json": {"touched_models": ["account.move"]}})
        self.assertTrue(r["evidence"]["signals"].get("sensitive_model"))
        self.assertTrue(r["decision"]["required_approvals"])
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_negative_or_missing_counts_are_invalid(self):
        # round-6 #2: a -1 blocking (would clear the >0 signal) or a missing warning
        # must be treated as invalid evidence → needs_human, never approve
        r = self._clean_core({"manifest.json": {"has_migration": True},
                              "upgrade.json": {"summary": {"blocking": -1, "warning": 0}}})
        self.assertEqual(r["decision"]["decision"], "needs_human")
        r = self._clean_core({"validate.json": {"summary": {"blocking": 0}}})  # no warning
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_duplicate_json_keys_rejected(self):
        # round-7 #1: a duplicate key must not silently overwrite a blocker/tier
        # (_bundle writes str values raw, so these stay duplicate on disk)
        r = self._clean_core({"validate.json": '{"summary":{"blocking":1,"blocking":0,"warning":0}}'})
        self.assertEqual(r["decision"]["decision"], "needs_human")
        r = self._clean_core({"scenarios.json": '{"risk":{"tier":"critical","tier":"normal"}}'})
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_native_check_wrong_type_is_invalid(self):
        # round-7 #2: confirmed_candidates/considered must be real lists
        for bad in ({"confirmed_candidates": "not-a-list"}, {"considered": True}):
            r = self._clean_core({"native_check.json": bad})
            self.assertEqual(r["decision"]["decision"], "needs_human", bad)

    def test_backslash_migration_path_detected(self):
        # round-7 #3A: a Windows-separator migration path must still require upgrade
        r = self._clean_core({"manifest.json": {
            "changed_files": ["addons/m/migrations\\18.0.1\\post-migrate.py"]}})
        self.assertTrue(r["evidence"]["signals"]["has_migration"])
        self.assertIn("upgrade", r["required_evidence"])

    def test_sensitive_model_from_changed_file_path(self):
        # round-7 #3B: a change under account/ is sensitive even w/o touched_models
        r = self._clean_core({"manifest.json": {
            "changed_files": ["addons/account/models/account_move.py"]}})
        self.assertTrue(r["evidence"]["signals"]["sensitive_model"])
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_upgrade_warning_forces_needs_human(self):
        # round-8 #1: a required upgrade reporting warnings must not silently approve
        r = self._clean_core({"manifest.json": {"has_migration": True},
                              "upgrade.json": {"summary": {"blocking": 0, "warning": 1}}})
        self.assertEqual(r["risk"]["tier"], "high")
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_native_check_mixed_wrong_type_invalid(self):
        # round-8 #2: a present-but-non-list known key invalidates native_check
        for bad in ('{"confirmed_candidates": [], "considered": true}',
                    '{"confirmed_candidates": "not-a-list", "considered": []}'):
            r = self._clean_core({"native_check.json": bad})
            self.assertEqual(r["decision"]["decision"], "needs_human", bad)

    def test_nan_infinity_constants_rejected(self):
        # round-8 #3: NaN/Infinity are not valid JSON and must not parse into evidence
        for bad in ('{"confirmed_candidates": [NaN]}', '{"confirmed_candidates": [Infinity]}'):
            r = self._clean_core({"native_check.json": bad})
            self.assertEqual(r["decision"]["decision"], "needs_human", bad)

    def test_overflow_float_rejected_and_no_crash(self):
        # round-9 #1/#2: 1e10000 overflows to inf — must be rejected on load, and the
        # report must still serialise (allow_nan=False) without crashing
        r = self._clean_core({"native_check.json": '{"confirmed_candidates":[1e10000]}'})
        self.assertEqual(r["decision"]["decision"], "needs_human")
        r = self._clean_core({"trace.json": '{"error":1e10000}'})
        self.assertEqual(r["decision"]["decision"], "needs_human")
        json.dumps(r, default=str, allow_nan=False)  # output contract: must not raise

    def test_security_and_trace_wrong_types_invalid(self):
        # round-9 #3: known signal fields read by truthiness must be the right type
        for bad in ({"is_superuser": [], "_warnings": {}},):
            r = self._clean_core({"security.json": bad})
            self.assertEqual(r["decision"]["decision"], "needs_human", bad)
        r = self._clean_core({"trace.json": {"error": []}})
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_security_and_trace_valid_shapes_still_decide(self):
        r = self._clean_core({"security.json": {"is_superuser": False, "_warnings": []}})
        self.assertEqual(r["decision"]["decision"], "approve")
        r = self._clean_core({"trace.json": {"calls": 5}})           # no error → approve
        self.assertEqual(r["decision"]["decision"], "approve")
        r = self._clean_core({"trace.json": {"error": "boom"}})      # real error → block
        self.assertEqual(r["decision"]["decision"], "block")

    def test_malformed_model_hint_fields_invalid(self):
        # round-10 #1: model hints feed sensitive detection — a non-string is invalid
        for bad in ({"scenarios.json": {"risk": {"tier": "normal", "model": []}}},
                    {"scenarios.json": {"risk": {"tier": "normal"}, "model": []}},
                    {"security.json": {"model": 5}},
                    {"security.json": {"model_name": []}}):
            r = self._clean_core(bad)
            self.assertEqual(r["decision"]["decision"], "needs_human", bad)

    def test_string_model_hint_decides_by_sensitivity(self):
        r = self._clean_core({"scenarios.json": {"risk": {"tier": "normal", "model": "sale.order"}}})
        self.assertEqual(r["decision"]["decision"], "approve")        # non-sensitive
        r = self._clean_core({"scenarios.json": {"risk": {"tier": "normal", "model": "account.move"}}})
        self.assertEqual(r["decision"]["decision"], "needs_human")    # sensitive

    def test_empty_string_trace_error_is_invalid(self):
        # round-10 #2: "" reads falsey but is a present error field → not approve
        r = self._clean_core({"trace.json": {"error": ""}})
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_gate_decision_self_defends_on_odd_trace_error(self):
        # round-11: gate_decision must not approve on a present-but-falsy trace_error
        # even if called directly (defense-in-depth, not relying on _artifact_valid)
        for te, want in (("", "block"), ([], "block"), ("boom", "block"), (None, "approve")):
            d = dg.gate_decision({"signals": {"trace_error": te}, "missing": []}, {"tier": "normal"})
            self.assertEqual(d["decision"], want, te)

    def test_manifest_expands_required_evidence(self):
        d = self._bundle({
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
            "validate.json": {"summary": {"blocking": 0, "warning": 0}},
            "manifest.json": {"has_migration": True,
                              "changed_files": ["addons/m/controllers/main.py"]},
        })
        r = dg.build_report(d)
        self.assertIn("upgrade", r["required_evidence"])
        self.assertIn("security", r["required_evidence"])
        self.assertEqual(r["decision"]["decision"], "needs_human")  # upgrade+security missing
        self.assertTrue(any("controller" in a for a in r["decision"]["required_approvals"]))

    def test_clean_core_bundle_approves(self):
        d = self._bundle({
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
            "validate.json": {"summary": {"blocking": 0, "warning": 0}},
            "scan_secrets.json": {"count": 0},  # core-required as of v0.9.1
        })
        r = dg.build_report(d)
        self.assertEqual(r["decision"]["decision"], "approve")


# ---------------------------------------------------------------------------
# v0.14 Layer M: severity classes, findings_detail, fail-closed policy
# (APPENDED — additive, backward compatible; no existing test above modified)
# ---------------------------------------------------------------------------

class SeverityAndFailClosedTests(unittest.TestCase):
    """Severity mapping, findings_detail/severity_summary shape, and the opt-in
    fail-closed policy (gate_policy.json / env / --policy) + human sign-off."""

    def _bundle(self, files):
        d = tempfile.mkdtemp()
        for name, obj in files.items():
            with open(Path(d) / name, "w") as fh:
                fh.write(obj if isinstance(obj, str) else json.dumps(obj))
        return d

    def _clean_core(self, extra=None):
        """A clean, would-approve legacy core bundle (+ optional extra files)."""
        files = {
            "native_check.json": {"confirmed_candidates": []},
            "scenarios.json": {"risk": {"tier": "normal"}},
            "validate.json": {"summary": {"blocking": 0, "warning": 0}},
            "scan_secrets.json": {"count": 0},
        }
        files.update(extra or {})
        return self._bundle(files)

    def _security_bundle(self, extra=None):
        """A would-approve core + a manifest that touches security (present, clean
        security artifact). Without policy → needs_human with exactly one S4
        finding (the access-control approval). A clean, deterministic S4 source."""
        files = {
            "manifest.json": {"touches_security": True},
            "security.json": {"is_superuser": False, "_warnings": []},
        }
        files.update(extra or {})
        return self._clean_core(files)

    # --- SEVERITY mapping ---------------------------------------------------

    def test_severity_by_category_known_mappings(self):
        m = dg.SEVERITY_BY_CATEGORY
        self.assertEqual(m["security"], "S4")
        self.assertEqual(m["scan_secrets"], "S4")
        self.assertEqual(m["provenance"], "S4")
        self.assertEqual(m["upgrade"], "S3")
        self.assertEqual(m["data_integrity"], "S3")
        self.assertEqual(m["validate"], "S2")
        self.assertEqual(m["test_quality"], "S2")
        self.assertEqual(m["changed_coverage"], "S2")
        self.assertEqual(m["doc"], "S1")
        # every value is a well-formed S0..S4 class
        self.assertTrue(all(v in {"S0", "S1", "S2", "S3", "S4"} for v in m.values()))

    def test_unknown_category_defaults_to_s2(self):
        entry = dg._finding_entry("mystery: something new", "no_such_category")
        self.assertEqual(entry["severity"], "S2")
        self.assertEqual(entry["remediation"], dg._DEFAULT_REMEDIATION)

    def test_blocking_finding_prefix_classified(self):
        # upgrade blocking → S3, scan-secrets → S4, validate → S2
        r = dg.build_report(self._bundle({"upgrade.json": {"summary": {"blocking": 1, "warning": 0}}}))
        by_sev = {fd["severity"] for fd in r["findings_detail"]}
        self.assertIn("S3", by_sev)   # the upgrade blocking finding
        r = dg.build_report(self._bundle({"scan_secrets.json": {"count": 2}}))
        self.assertTrue(any(fd["severity"] == "S4" for fd in r["findings_detail"]))

    # --- findings_detail + severity_summary shape ---------------------------

    def test_findings_detail_shape(self):
        r = dg.build_report(self._bundle({"validate.json": {"summary": {"blocking": 1, "warning": 0}}}))
        fd = r["findings_detail"]
        self.assertIsInstance(fd, list)
        self.assertTrue(fd)
        for entry in fd:
            self.assertEqual(set(entry), {"finding", "severity", "remediation"})
            self.assertIsInstance(entry["finding"], str)
            self.assertIn(entry["severity"], {"S0", "S1", "S2", "S3", "S4"})
            self.assertIsInstance(entry["remediation"], str)
            self.assertTrue(entry["remediation"])

    def test_findings_detail_is_parallel_not_replacing(self):
        # existing flat keys keep their element type (list[str]); findings_detail
        # is a separate list[dict]
        r = dg.build_report(self._bundle({"validate.json": {"summary": {"blocking": 1, "warning": 0}}}))
        self.assertTrue(all(isinstance(x, str) for x in r["decision"]["blocking_findings"]))
        self.assertTrue(all(isinstance(x, dict) for x in r["findings_detail"]))

    def test_severity_summary_counts_match(self):
        r = dg.build_report(self._security_bundle())
        summary = r["severity_summary"]
        self.assertEqual(set(summary), {"S0", "S1", "S2", "S3", "S4"})
        # summary must equal the per-severity tally of findings_detail
        expect = {f"S{i}": 0 for i in range(5)}
        for fd in r["findings_detail"]:
            expect[fd["severity"]] += 1
        self.assertEqual(summary, expect)
        self.assertEqual(summary["S4"], 1)   # exactly the access-control approval

    def test_approve_has_empty_findings_and_zero_summary(self):
        r = dg.build_report(self._clean_core())
        self.assertEqual(r["decision"]["decision"], "approve")
        self.assertEqual(r["findings_detail"], [])
        self.assertEqual(r["severity_summary"], {f"S{i}": 0 for i in range(5)})

    def test_new_report_keys_always_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = dg.build_report(tmp)
        for key in ("findings_detail", "severity_summary", "fail_closed"):
            self.assertIn(key, r)
        self.assertFalse(r["fail_closed"]["active"])

    # --- FAIL-CLOSED policy: gate_policy.json -------------------------------

    def test_fail_closed_gate_policy_escalates_s4_to_block(self):
        d = self._security_bundle({"gate_policy.json": {
            "fail_closed_severities": ["S3", "S4"], "required_signoff_roles": ["reviewer"]}})
        r = dg.build_report(d)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(r["fail_closed"]["active"])
        self.assertTrue(r["fail_closed"]["escalated"])
        self.assertFalse(r["fail_closed"]["signoff_present"])
        self.assertEqual(r["fail_closed"]["source"], "gate_policy.json")
        self.assertTrue(any("fail-closed policy" in b for b in r["decision"]["blocking_findings"]))

    def test_fail_closed_signoff_downgrades_to_needs_human(self):
        d = self._security_bundle({
            "gate_policy.json": {"fail_closed_severities": ["S3", "S4"],
                                 "required_signoff_roles": ["reviewer"]},
            "human_signoff.json": {"signoffs": [
                {"role": "reviewer", "name": "Alice", "at": "2026-07-03T10:00:00Z"}]},
        })
        r = dg.build_report(d)
        self.assertEqual(r["decision"]["decision"], "needs_human")   # never approve
        self.assertTrue(r["fail_closed"]["signoff_present"])
        self.assertFalse(r["fail_closed"]["escalated"])

    def test_fail_closed_wrong_role_signoff_still_blocks(self):
        # a sign-off that does NOT cover the required role must not downgrade
        d = self._security_bundle({
            "gate_policy.json": {"fail_closed_severities": ["S4"],
                                 "required_signoff_roles": ["reviewer"]},
            "human_signoff.json": {"signoffs": [
                {"role": "intern", "name": "Bob", "at": "2026-07-03T10:00:00Z"}]},
        })
        r = dg.build_report(d)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertFalse(r["fail_closed"]["signoff_present"])
        self.assertTrue(r["fail_closed"]["escalated"])

    def test_fail_closed_only_lower_severity_does_not_escalate(self):
        # policy fails closed on S3 only; the bundle's finding is S4 → still triggers.
        # Use a bundle whose ONLY finding is S2 to prove a non-matching class is inert.
        d = self._clean_core({
            "manifest.json": {"has_migration": True},   # requires 'upgrade' (S3) evidence
            "gate_policy.json": {"fail_closed_severities": ["S0"]},   # nothing maps to S0
        })
        r = dg.build_report(d)
        # missing upgrade → S3 finding exists, but policy only fails closed on S0
        self.assertTrue(r["fail_closed"]["active"])
        self.assertFalse(r["fail_closed"]["escalated"])
        self.assertNotEqual(r["decision"]["decision"], "block")

    # --- FAIL-CLOSED policy: env var ---------------------------------------

    def test_fail_closed_env_var_source(self):
        d = self._security_bundle()
        os.environ["ODOO_AI_FAIL_CLOSED"] = "S3,S4"
        try:
            r = dg.build_report(d)
        finally:
            os.environ.pop("ODOO_AI_FAIL_CLOSED", None)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertEqual(r["fail_closed"]["source"], "env:ODOO_AI_FAIL_CLOSED")
        self.assertTrue(r["fail_closed"]["escalated"])

    def test_env_signoff_downgrades_when_no_roles_required(self):
        # env source declares no required roles → any valid sign-off file downgrades
        d = self._security_bundle({"human_signoff.json": {
            "signoffs": [{"role": "anyone", "name": "Carol", "at": "2026-07-03T10:00:00Z"}]}})
        os.environ["ODOO_AI_FAIL_CLOSED"] = "S4"
        try:
            r = dg.build_report(d)
        finally:
            os.environ.pop("ODOO_AI_FAIL_CLOSED", None)
        self.assertEqual(r["decision"]["decision"], "needs_human")
        self.assertTrue(r["fail_closed"]["signoff_present"])

    # --- FAIL-CLOSED policy: --policy flag ---------------------------------

    def test_policy_flag_source(self):
        d = self._security_bundle()
        pol = Path(d) / "external_policy.json"
        pol.write_text(json.dumps({"fail_closed_severities": ["S4"],
                                   "required_signoff_roles": ["reviewer"]}))
        r = dg.build_report(d, policy_path=str(pol))
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(r["fail_closed"]["source"].startswith("--policy"))

    def test_policy_flag_precedes_gate_policy_file(self):
        # explicit --policy (S4 fail-closed) wins over an inert gate_policy.json
        d = self._security_bundle({"gate_policy.json": {"fail_closed_severities": ["S0"]}})
        pol = Path(d) / "external_policy.json"
        pol.write_text(json.dumps({"fail_closed_severities": ["S4"]}))
        r = dg.build_report(d, policy_path=str(pol))
        self.assertTrue(r["fail_closed"]["source"].startswith("--policy"))
        self.assertEqual(r["decision"]["decision"], "block")

    def test_main_parses_policy_flag(self):
        import io
        from contextlib import redirect_stdout
        d = self._security_bundle()
        pol = Path(d) / "p.json"
        pol.write_text(json.dumps({"fail_closed_severities": ["S4"]}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            # argv[0] is the program name (main slices it off, like sys.argv)
            dg.main(["deploy_gate.py", "--policy", str(pol), d])
        out = json.loads(buf.getvalue())
        self.assertEqual(out["decision"]["decision"], "block")
        self.assertTrue(out["fail_closed"]["active"])

    def test_malformed_policy_flag_warns_and_falls_back(self):
        # a missing --policy path is ignored (warned) and other sources apply
        d = self._security_bundle()
        r = dg.build_report(d, policy_path=str(Path(d) / "does_not_exist.json"))
        self.assertFalse(r["fail_closed"]["active"])   # no other source present
        self.assertTrue(any("--policy" in w for w in r["_warnings"]))

    # --- NO-POLICY behaviour is byte-for-byte legacy ------------------------

    def test_no_policy_decision_identical_to_legacy(self):
        # For a matrix of bundles, the decision with no policy present must equal
        # the historically-expected decision, and fail_closed must be inactive.
        cases = [
            (self._clean_core(), "approve"),
            (self._security_bundle(), "needs_human"),
            (self._bundle({"validate.json": {"summary": {"blocking": 1, "warning": 0}}}), "block"),
            (self._bundle({}), "needs_human"),
        ]
        for d, expected in cases:
            r = dg.build_report(d)
            self.assertEqual(r["decision"]["decision"], expected, d)
            self.assertFalse(r["fail_closed"]["active"], d)
            self.assertNotIn("source", r["fail_closed"], d)

    def test_no_policy_report_keys_unchanged_types(self):
        # existing keys keep exact shape; new keys are purely additive
        r = dg.build_report(self._clean_core())
        self.assertEqual(r["policy"], "v1-legacy")
        self.assertIn("decision", r["decision"])
        self.assertIsInstance(r["decision"]["blocking_findings"], list)
        self.assertIsInstance(r["decision"]["missing_evidence"], list)
        self.assertIsInstance(r["decision"]["required_approvals"], list)


if __name__ == "__main__":
    unittest.main()
