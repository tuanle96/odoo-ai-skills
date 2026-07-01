"""
Unit tests for scenario_satisfaction.py — pure-function tests (in-memory
observations, plus tempfile-backed tests for main()'s file I/O).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import scenario_satisfaction as ss  # noqa: E402

ALL_KEYS = [
    "non_admin", "at_install_vs_post_install", "multi_company", "batch",
    "upgrade_i_and_u", "locked_period", "record_rules",
]

_FULLY_SATISFIED_OBS = {
    "uids_seen": [1, 17],
    "max_recordset_len": 2,
    "max_create_vals_len": 1,
    "companies_seen": [1, 2],
    "allowed_company_sets": [[1], [2]],
    "phases_covered": ["at_install", "post_install"],
    "install_modes": ["-i", "-u"],
    "raised_exceptions": ["UserError"],
    "access_errors_seen": True,
    "locked_period_usererror": True,
}


# ---------------------------------------------------------------------------
# Predicate tests
# ---------------------------------------------------------------------------

class NonAdminTests(unittest.TestCase):
    def test_ok_when_non_admin_uid_seen(self):
        ok, evidence = ss.satisfied_predicate("non_admin", {"uids_seen": [1, 17]})
        self.assertTrue(ok)
        self.assertIn("17", evidence)

    def test_not_ok_when_only_superuser_seen(self):
        ok, evidence = ss.satisfied_predicate("non_admin", {"uids_seen": [1]})
        self.assertFalse(ok)
        self.assertTrue(evidence)

    def test_not_ok_when_missing(self):
        ok, evidence = ss.satisfied_predicate("non_admin", {})
        self.assertFalse(ok)


class AtInstallVsPostInstallTests(unittest.TestCase):
    def test_ok_when_both_phases_covered(self):
        ok, _ = ss.satisfied_predicate(
            "at_install_vs_post_install",
            {"phases_covered": ["at_install", "post_install"]},
        )
        self.assertTrue(ok)

    def test_not_ok_when_only_one_phase(self):
        ok, evidence = ss.satisfied_predicate(
            "at_install_vs_post_install", {"phases_covered": ["post_install"]}
        )
        self.assertFalse(ok)
        self.assertIn("at_install", evidence)

    def test_not_ok_when_missing(self):
        ok, _ = ss.satisfied_predicate("at_install_vs_post_install", {})
        self.assertFalse(ok)


class MultiCompanyTests(unittest.TestCase):
    def test_ok_via_companies_seen(self):
        ok, evidence = ss.satisfied_predicate("multi_company", {"companies_seen": [1, 2]})
        self.assertTrue(ok)
        self.assertIn("1", evidence)

    def test_ok_via_allowed_company_sets_variety(self):
        ok, _ = ss.satisfied_predicate(
            "multi_company", {"allowed_company_sets": [[1], [2]]}
        )
        self.assertTrue(ok)

    def test_not_ok_single_company(self):
        ok, _ = ss.satisfied_predicate("multi_company", {"companies_seen": [1]})
        self.assertFalse(ok)

    def test_not_ok_when_missing(self):
        ok, _ = ss.satisfied_predicate("multi_company", {})
        self.assertFalse(ok)


class BatchTests(unittest.TestCase):
    def test_ok_via_recordset_len(self):
        ok, evidence = ss.satisfied_predicate("batch", {"max_recordset_len": 2})
        self.assertTrue(ok)
        self.assertIn("2", evidence)

    def test_ok_via_create_vals_len(self):
        ok, _ = ss.satisfied_predicate("batch", {"max_create_vals_len": 3})
        self.assertTrue(ok)

    def test_not_ok_when_both_singleton(self):
        ok, _ = ss.satisfied_predicate(
            "batch", {"max_recordset_len": 1, "max_create_vals_len": 1}
        )
        self.assertFalse(ok)

    def test_not_ok_when_missing(self):
        ok, _ = ss.satisfied_predicate("batch", {})
        self.assertFalse(ok)


class UpgradeIAndUTests(unittest.TestCase):
    def test_ok_when_both_modes(self):
        ok, _ = ss.satisfied_predicate("upgrade_i_and_u", {"install_modes": ["-i", "-u"]})
        self.assertTrue(ok)

    def test_not_ok_when_only_i(self):
        ok, evidence = ss.satisfied_predicate("upgrade_i_and_u", {"install_modes": ["-i"]})
        self.assertFalse(ok)
        self.assertIn("-u", evidence)

    def test_not_ok_when_missing(self):
        ok, _ = ss.satisfied_predicate("upgrade_i_and_u", {})
        self.assertFalse(ok)


class LockedPeriodTests(unittest.TestCase):
    def test_ok_via_locked_period_usererror(self):
        ok, evidence = ss.satisfied_predicate("locked_period", {"locked_period_usererror": True})
        self.assertTrue(ok)
        self.assertIn("locked_period_usererror", evidence)

    def test_generic_usererror_does_not_satisfy(self):
        # Oracle final review: a bare UserError anywhere must NOT satisfy the
        # locked-period scenario — only the specific flag does.
        ok, _ = ss.satisfied_predicate("locked_period", {"raised_exceptions": ["UserError"]})
        self.assertFalse(ok)

    def test_not_ok_when_neither(self):
        ok, _ = ss.satisfied_predicate("locked_period", {"raised_exceptions": ["ValidationError"]})
        self.assertFalse(ok)

    def test_not_ok_when_missing(self):
        ok, _ = ss.satisfied_predicate("locked_period", {})
        self.assertFalse(ok)


class RecordRulesTests(unittest.TestCase):
    def test_ok_via_access_errors_seen(self):
        ok, evidence = ss.satisfied_predicate("record_rules", {"access_errors_seen": True})
        self.assertTrue(ok)
        self.assertIn("access_errors_seen", evidence)

    def test_ok_via_multi_company_and_non_admin(self):
        ok, _ = ss.satisfied_predicate(
            "record_rules", {"companies_seen": [1, 2], "uids_seen": [1, 17]}
        )
        self.assertTrue(ok)

    def test_not_ok_multi_company_without_non_admin(self):
        ok, _ = ss.satisfied_predicate(
            "record_rules", {"companies_seen": [1, 2], "uids_seen": [1]}
        )
        self.assertFalse(ok)

    def test_not_ok_when_missing(self):
        ok, _ = ss.satisfied_predicate("record_rules", {})
        self.assertFalse(ok)


class UnknownKeyTests(unittest.TestCase):
    def test_unknown_key_not_ok(self):
        ok, evidence = ss.satisfied_predicate("some_unknown_key", {})
        self.assertFalse(ok)
        self.assertIn("some_unknown_key", evidence)


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------

class EvaluateTests(unittest.TestCase):
    def test_all_satisfied(self):
        result = ss.evaluate(ALL_KEYS, _FULLY_SATISFIED_OBS)
        self.assertTrue(result["ok"])
        self.assertEqual(result["unsatisfied"], [])
        self.assertEqual(result["summary"], {"required": len(ALL_KEYS), "satisfied": len(ALL_KEYS)})

    def test_one_missing_batch(self):
        obs = dict(_FULLY_SATISFIED_OBS)
        obs["max_recordset_len"] = 1
        obs["max_create_vals_len"] = 1
        result = ss.evaluate(ALL_KEYS, obs)
        self.assertFalse(result["ok"])
        self.assertIn("batch", result["unsatisfied"])
        self.assertFalse(result["satisfied"]["batch"]["ok"])
        # everything else still satisfied
        self.assertEqual(result["unsatisfied"], ["batch"])

    def test_observations_none(self):
        result = ss.evaluate(ALL_KEYS, None)
        self.assertFalse(result["ok"])
        self.assertEqual(sorted(result["unsatisfied"]), sorted(ALL_KEYS))

    def test_observations_empty_dict(self):
        result = ss.evaluate(ALL_KEYS, {})
        self.assertFalse(result["ok"])
        self.assertEqual(sorted(result["unsatisfied"]), sorted(ALL_KEYS))

    def test_no_exception_raised_on_bad_input(self):
        try:
            ss.evaluate(ALL_KEYS, {"uids_seen": "not-a-list", "companies_seen": 42})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"evaluate() raised {exc!r} on malformed observations")

    def test_summary_counts(self):
        result = ss.evaluate(["non_admin", "batch"], {"uids_seen": [1, 17], "max_recordset_len": 1})
        self.assertEqual(result["summary"], {"required": 2, "satisfied": 1})

    def test_empty_required(self):
        result = ss.evaluate([], _FULLY_SATISFIED_OBS)
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"], {"required": 0, "satisfied": 0})


# ---------------------------------------------------------------------------
# required_from_scenarios()
# ---------------------------------------------------------------------------

class RequiredFromScenariosTests(unittest.TestCase):
    def test_scenario_gen_shape(self):
        doc = {"scenarios": [{"key": "non_admin", "why": "..."}, {"key": "batch", "why": "..."}]}
        self.assertEqual(ss.required_from_scenarios(doc), ["non_admin", "batch"])

    def test_plain_required_shape(self):
        doc = {"required": ["non_admin", "multi_company"]}
        self.assertEqual(ss.required_from_scenarios(doc), ["non_admin", "multi_company"])

    def test_missing_shape_returns_empty(self):
        self.assertEqual(ss.required_from_scenarios({}), [])

    def test_non_dict_returns_empty(self):
        self.assertEqual(ss.required_from_scenarios(None), [])
        self.assertEqual(ss.required_from_scenarios("nope"), [])

    def test_malformed_scenario_entries_skipped(self):
        doc = {"scenarios": [{"key": "non_admin"}, {"no_key": True}, "not-a-dict"]}
        self.assertEqual(ss.required_from_scenarios(doc), ["non_admin"])


# ---------------------------------------------------------------------------
# main() — file I/O
# ---------------------------------------------------------------------------

class MainTests(unittest.TestCase):
    def _write(self, tmpdir, name, obj):
        p = Path(tmpdir) / name
        p.write_text(json.dumps(obj))
        return str(p)

    def test_main_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            scen_path = self._write(tmp, "scenarios.json", {"scenarios": [{"key": "non_admin"}]})
            obs_path = self._write(tmp, "observations.json", {"uids_seen": [1, 17]})
            argv = ["--scenarios", scen_path, "--observations", obs_path]

            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ss.main(argv)
            self.assertEqual(rc, 0)
            report = json.loads(buf.getvalue())
            self.assertTrue(report["ok"])
            self.assertEqual(report["required"], ["non_admin"])

    def test_main_missing_scenarios_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs_path = self._write(tmp, "observations.json", {"uids_seen": [1, 17]})
            argv = ["--scenarios", str(Path(tmp) / "nope.json"), "--observations", obs_path]

            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ss.main(argv)
            self.assertEqual(rc, 0)
            report = json.loads(buf.getvalue())
            self.assertFalse(report["ok"])
            self.assertTrue(report["_warnings"])

    def test_main_unparseable_observations_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            scen_path = self._write(tmp, "scenarios.json", {"scenarios": [{"key": "non_admin"}]})
            obs_path = Path(tmp) / "observations.json"
            obs_path.write_text("{not valid json")
            argv = ["--scenarios", scen_path, "--observations", str(obs_path)]

            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = ss.main(argv)
            self.assertEqual(rc, 0)
            report = json.loads(buf.getvalue())
            self.assertFalse(report["ok"])
            self.assertTrue(report["_warnings"])


if __name__ == "__main__":
    unittest.main()
