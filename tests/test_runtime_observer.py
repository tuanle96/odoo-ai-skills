"""
Unit tests for runtime_observer.py — pure-function tests of the trace-integrity
decision layer (PART A). PART B (install/finalize) is env-gated/CI-only and not
exercised here, per the module's own docstring.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import runtime_observer as obs  # noqa: E402


def _good_state(**overrides):
    state = {
        "trace_installed": True,
        "trace_still_installed": True,
        "heartbeats": 5,
        "min_heartbeats": 1,
        "events_recorded": 3,
        "targets_expected": 2,
        "targets_hit": 2,
        "output_test_writable": False,
    }
    state.update(overrides)
    return state


class TestComputeToolDigest(unittest.TestCase):
    def test_format(self):
        digest = obs.compute_tool_digest(b"hello world")
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(digest, "sha256:" + hashlib.sha256(b"hello world").hexdigest())

    def test_deterministic(self):
        d1 = obs.compute_tool_digest(b"same bytes")
        d2 = obs.compute_tool_digest(b"same bytes")
        self.assertEqual(d1, d2)

    def test_differs_on_different_input(self):
        d1 = obs.compute_tool_digest(b"a")
        d2 = obs.compute_tool_digest(b"b")
        self.assertNotEqual(d1, d2)

    def test_self_digest_matches_own_file(self):
        expected = obs.compute_tool_digest(Path(obs.__file__).read_bytes())
        self.assertEqual(obs.self_digest(), expected)


class TestEvaluateIntegritySealed(unittest.TestCase):
    def test_fully_good_state_is_sealed(self):
        result = obs.evaluate_integrity(_good_state())
        self.assertEqual(result["trace_integrity"], "sealed")
        self.assertTrue(result["sealed"])

    def test_sealed_with_no_targets_expected(self):
        # targets_expected == 0 means the target-hit check doesn't apply.
        result = obs.evaluate_integrity(_good_state(targets_expected=0, targets_hit=0))
        self.assertEqual(result["trace_integrity"], "sealed")
        self.assertTrue(result["sealed"])

    def test_unknown_output_writable_fails_closed(self):
        # Oracle observer review: unknown output ownership must NOT seal — we can't
        # prove the evidence path wasn't overwritten by the test process.
        result = obs.evaluate_integrity(_good_state(output_test_writable=None))
        self.assertEqual(result["trace_integrity"], "incomplete")
        self.assertFalse(result["sealed"])
        self.assertTrue(any("output_test_writable" in r for r in result["reasons"]))

    def test_disable_restore_detected_via_audit_counter(self):
        # The disable/restore attack (settrace(None) then restore) is caught by the
        # permanent audit counter even though trace_still_installed reads True again.
        result = obs.evaluate_integrity(_good_state(trace_changed_externally=True))
        self.assertEqual(result["trace_integrity"], "tampered")
        self.assertFalse(result["sealed"])


class TestEvaluateIntegrityAbsent(unittest.TestCase):
    def test_trace_never_installed(self):
        result = obs.evaluate_integrity(_good_state(trace_installed=False))
        self.assertEqual(result["trace_integrity"], "absent")
        self.assertFalse(result["sealed"])
        self.assertIn("tracer was never installed", result["reasons"])

    def test_trace_installed_missing_key(self):
        state = _good_state()
        del state["trace_installed"]
        result = obs.evaluate_integrity(state)
        self.assertEqual(result["trace_integrity"], "absent")
        self.assertFalse(result["sealed"])


class TestEvaluateIntegrityTampered(unittest.TestCase):
    def test_trace_disabled_mid_run(self):
        """The sys.settrace(None) attack: installed, but not still installed."""
        result = obs.evaluate_integrity(_good_state(trace_still_installed=False))
        self.assertEqual(result["trace_integrity"], "tampered")
        self.assertFalse(result["sealed"])
        self.assertTrue(any("no longer our tracer" in r for r in result["reasons"]))

    def test_trace_still_installed_missing_key(self):
        state = _good_state()
        del state["trace_still_installed"]
        result = obs.evaluate_integrity(state)
        self.assertEqual(result["trace_integrity"], "tampered")
        self.assertFalse(result["sealed"])

    def test_output_writable_by_test_process(self):
        result = obs.evaluate_integrity(_good_state(output_test_writable=True))
        self.assertEqual(result["trace_integrity"], "tampered")
        self.assertFalse(result["sealed"])
        self.assertTrue(any("writable by the test process" in r for r in result["reasons"]))


class TestEvaluateIntegrityIncomplete(unittest.TestCase):
    def test_zero_heartbeats(self):
        result = obs.evaluate_integrity(_good_state(heartbeats=0))
        self.assertEqual(result["trace_integrity"], "incomplete")
        self.assertFalse(result["sealed"])
        self.assertTrue(any("heartbeats" in r for r in result["reasons"]))

    def test_zero_events_recorded(self):
        result = obs.evaluate_integrity(_good_state(events_recorded=0))
        self.assertEqual(result["trace_integrity"], "incomplete")
        self.assertFalse(result["sealed"])
        self.assertTrue(any("events_recorded" in r for r in result["reasons"]))

    def test_targets_hit_less_than_expected(self):
        result = obs.evaluate_integrity(_good_state(targets_expected=3, targets_hit=1))
        self.assertEqual(result["trace_integrity"], "incomplete")
        self.assertFalse(result["sealed"])
        self.assertTrue(any("targets_hit" in r for r in result["reasons"]))

    def test_negative_events_recorded_coerced_to_default(self):
        result = obs.evaluate_integrity(_good_state(events_recorded=-5))
        # Non-int-ish weirdness aside, this is a real negative int; treat via
        # the same <=0 check as zero.
        self.assertEqual(result["trace_integrity"], "incomplete")
        self.assertFalse(result["sealed"])


class TestEvaluateIntegrityDefensive(unittest.TestCase):
    def test_empty_dict_fails_closed(self):
        result = obs.evaluate_integrity({})
        self.assertFalse(result["sealed"])
        self.assertEqual(result["trace_integrity"], "absent")

    def test_none_fails_closed_no_exception(self):
        result = obs.evaluate_integrity(None)
        self.assertFalse(result["sealed"])
        self.assertEqual(result["trace_integrity"], "absent")

    def test_wrong_typed_state_no_exception(self):
        result = obs.evaluate_integrity("not a dict")
        self.assertFalse(result["sealed"])
        self.assertEqual(result["trace_integrity"], "absent")

    def test_wrong_typed_numeric_fields_no_exception(self):
        state = _good_state(heartbeats="five", events_recorded=None, targets_hit=[])
        result = obs.evaluate_integrity(state)
        self.assertFalse(result["sealed"])
        self.assertEqual(result["trace_integrity"], "incomplete")

    def test_bool_heartbeats_not_treated_as_int(self):
        # bool is an int subclass in Python — True must not silently pass as
        # heartbeats=1.
        state = _good_state(heartbeats=True)
        result = obs.evaluate_integrity(state)
        self.assertEqual(result["trace_integrity"], "incomplete")
        self.assertFalse(result["sealed"])


class TestBuildSelfReport(unittest.TestCase):
    def test_shape_has_all_frozen_keys(self):
        report = obs.build_self_report(_good_state())
        expected_keys = {
            "tool", "tool_digest", "trace_integrity", "sealed",
            "heartbeats", "events_recorded", "targets_expected",
            "targets_hit", "reasons",
        }
        self.assertEqual(set(report.keys()), expected_keys)
        self.assertEqual(report["tool"], "runtime_observer")
        self.assertTrue(report["tool_digest"].startswith("sha256:"))

    def test_sealed_matches_trace_integrity_good(self):
        report = obs.build_self_report(_good_state())
        self.assertEqual(report["sealed"], report["trace_integrity"] == "sealed")
        self.assertTrue(report["sealed"])

    def test_sealed_matches_trace_integrity_tampered(self):
        report = obs.build_self_report(_good_state(trace_still_installed=False))
        self.assertEqual(report["sealed"], report["trace_integrity"] == "sealed")
        self.assertFalse(report["sealed"])
        self.assertEqual(report["trace_integrity"], "tampered")

    def test_custom_tool_digest_used_when_provided(self):
        report = obs.build_self_report(_good_state(), tool_digest="sha256:deadbeef")
        self.assertEqual(report["tool_digest"], "sha256:deadbeef")

    def test_defaults_tool_digest_to_self_digest(self):
        report = obs.build_self_report(_good_state())
        self.assertEqual(report["tool_digest"], obs.self_digest())

    def test_types_are_correct(self):
        report = obs.build_self_report(_good_state())
        self.assertIsInstance(report["sealed"], bool)
        self.assertIsInstance(report["trace_integrity"], str)
        self.assertIsInstance(report["tool_digest"], str)
        self.assertIsInstance(report["heartbeats"], int)
        self.assertIsInstance(report["events_recorded"], int)
        self.assertIsInstance(report["targets_expected"], int)
        self.assertIsInstance(report["targets_hit"], int)
        self.assertIsInstance(report["reasons"], list)


class TestCLI(unittest.TestCase):
    def _run_cli(self, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "runtime_observer.py"), *args],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return json.loads(result.stdout)

    def test_self_digest_cli(self):
        out = self._run_cli("--self-digest")
        self.assertEqual(out["tool_digest"], obs.self_digest())

    def test_digest_cli_on_this_file(self):
        this_file = str(SCRIPTS_DIR / "runtime_observer.py")
        out = self._run_cli("--digest", this_file)
        expected = obs.compute_tool_digest(Path(this_file).read_bytes())
        self.assertEqual(out["tool_digest"], expected)

    def test_digest_cli_missing_file(self):
        out = self._run_cli("--digest", "/no/such/file.py")
        self.assertFalse(out["sealed"])
        self.assertEqual(out["trace_integrity"], "absent")

    def test_evaluate_cli_good_state(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(_good_state(), f)
            path = f.name
        try:
            out = self._run_cli("--evaluate", path)
            self.assertTrue(out["sealed"])
            self.assertEqual(out["trace_integrity"], "sealed")
            self.assertTrue(out["self_report"]["sealed"])
        finally:
            Path(path).unlink()

    def test_evaluate_cli_tampered_state(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(_good_state(trace_still_installed=False), f)
            path = f.name
        try:
            out = self._run_cli("--evaluate", path)
            self.assertFalse(out["sealed"])
            self.assertEqual(out["trace_integrity"], "tampered")
            self.assertFalse(out["self_report"]["sealed"])
        finally:
            Path(path).unlink()

    def test_evaluate_cli_missing_file(self):
        out = self._run_cli("--evaluate", "/no/such/state.json")
        self.assertFalse(out["sealed"])
        self.assertEqual(out["trace_integrity"], "absent")

    def test_evaluate_cli_via_main_call(self):
        """Also exercise main() directly (not just via subprocess) for coverage
        of the in-process path."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(_good_state(), f)
            path = f.name
        try:
            rc = obs.main(["--evaluate", path])
            self.assertEqual(rc, 0)
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()
