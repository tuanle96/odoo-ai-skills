"""
Unit tests for evidence_schema (Evidence Artifact v1).

Run with:
    python3 -m unittest tests.test_evidence_schema -v

Validation tests build a full valid artifact then mutate one field at a time.
The build tests synthesize a minimal bundle dir (same approach as
tests/test_evidence_bundle.py) and assert build_artifact(...) round-trips through
validate_artifact(...).
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import evidence_schema  # noqa: E402


def _valid_artifact():
    """A full, valid Evidence Artifact v1 (every optional block populated)."""
    return {
        "schema_version": "1.0",
        "generated_at": "2026-07-03T12:00:00+00:00",
        "git": {"commit_sha": "abc123", "diff_hash": "def456", "branch": "main"},
        "instance": {"db_fingerprint": "fp-1", "odoo_version": "18.0",
                     "module_graph_hash": "mgh-1"},
        "addons": {"addon_hash": "ah-1"},
        "checks": [
            {"id": "validate", "layer": "gate", "status": "pass", "severity": "S2",
             "cache_provenance": "cold", "summary": "no anti-patterns"},
            {"id": "native_check", "layer": "inspect", "status": "skip", "severity": "S0",
             "cache_provenance": "cold", "summary": "not checked", "logs_path": "logs/nc.txt"},
        ],
        "decision": {"decision": "approve", "blocking_findings": [],
                     "required_approvals": []},
        "human_signoffs": [{"role": "senior-dev", "name": "Ada", "at": "2026-07-03T13:00:00+00:00"}],
        "redaction": {"mode": "external", "scanned": True},
    }


def _write_minimal_bundle(tmpdir):
    """A minimal deploy-gate bundle: the four core artifacts, all clean."""
    d = Path(tmpdir)
    (d / "validate.json").write_text(json.dumps({"summary": {"blocking": 0, "warning": 0}}))
    (d / "native_check.json").write_text(json.dumps({"confirmed_candidates": []}))
    (d / "scenarios.json").write_text(json.dumps({"risk": {"tier": "normal"}}))
    (d / "scan_secrets.json").write_text(json.dumps({"count": 0}))
    return str(d)


class HappyPathTests(unittest.TestCase):
    def test_full_valid_artifact_ok(self):
        res = evidence_schema.validate_artifact(_valid_artifact())
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(res["errors"], [])

    def test_optional_blocks_may_be_absent(self):
        art = _valid_artifact()
        del art["instance"]
        del art["addons"]
        self.assertTrue(evidence_schema.validate_artifact(art)["ok"])

    def test_not_a_dict_rejected(self):
        res = evidence_schema.validate_artifact(["nope"])
        self.assertFalse(res["ok"])
        self.assertIn("must be a JSON object", res["errors"][0])


class RequiredKeyTests(unittest.TestCase):
    def test_each_required_key_caught(self):
        for key in ("schema_version", "generated_at", "git", "checks",
                    "decision", "human_signoffs", "redaction"):
            art = _valid_artifact()
            del art[key]
            res = evidence_schema.validate_artifact(art)
            self.assertFalse(res["ok"], f"{key} removal should fail")
            self.assertIn(f"missing required key: {key}", res["errors"])

    def test_bad_schema_version(self):
        art = _valid_artifact()
        art["schema_version"] = "2.0"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any("schema_version" in e for e in res["errors"]))

    def test_git_commit_sha_may_be_null(self):
        art = _valid_artifact()
        art["git"] = {"commit_sha": None}
        self.assertTrue(evidence_schema.validate_artifact(art)["ok"])

    def test_git_missing_commit_sha_caught(self):
        art = _valid_artifact()
        art["git"] = {"branch": "main"}
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any("git.commit_sha" in e for e in res["errors"]))


class EnumViolationTests(unittest.TestCase):
    def test_bad_layer(self):
        art = _valid_artifact()
        art["checks"][0]["layer"] = "wat"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any(".layer must be one of" in e for e in res["errors"]))

    def test_bad_status(self):
        art = _valid_artifact()
        art["checks"][0]["status"] = "maybe"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any(".status must be one of" in e for e in res["errors"]))

    def test_bad_severity(self):
        art = _valid_artifact()
        art["checks"][0]["severity"] = "S9"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any(".severity must be one of" in e for e in res["errors"]))

    def test_bad_cache_provenance(self):
        art = _valid_artifact()
        art["checks"][0]["cache_provenance"] = "lukewarm"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any(".cache_provenance must be one of" in e for e in res["errors"]))

    def test_missing_check_id(self):
        art = _valid_artifact()
        del art["checks"][0]["id"]
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any(".id is required" in e for e in res["errors"]))

    def test_bad_decision_enum(self):
        art = _valid_artifact()
        art["decision"]["decision"] = "ship-it"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any("decision.decision must be one of" in e for e in res["errors"]))

    def test_bad_blocking_findings_type(self):
        art = _valid_artifact()
        art["decision"]["blocking_findings"] = [1, 2]
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any("blocking_findings must be a list of strings" in e for e in res["errors"]))

    def test_bad_redaction_mode(self):
        art = _valid_artifact()
        art["redaction"]["mode"] = "public"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any("redaction.mode must be one of" in e for e in res["errors"]))

    def test_bad_redaction_scanned_type(self):
        art = _valid_artifact()
        art["redaction"]["scanned"] = "yes"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any("redaction.scanned must be a boolean" in e for e in res["errors"]))

    def test_bad_signoff_role(self):
        art = _valid_artifact()
        art["human_signoffs"][0]["role"] = 42
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertTrue(any(".role must be a string" in e for e in res["errors"]))


class InvariantTests(unittest.TestCase):
    def test_warm_gate_check_blocks_approve(self):
        art = _valid_artifact()
        art["checks"][0]["cache_provenance"] = "warm"  # layer=gate, decision=approve
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertIn("warm-cache evidence cannot support an approve decision", res["errors"])

    def test_warm_verify_check_blocks_approve(self):
        art = _valid_artifact()
        art["checks"][0]["layer"] = "verify"
        art["checks"][0]["cache_provenance"] = "warm"
        res = evidence_schema.validate_artifact(art)
        self.assertFalse(res["ok"])
        self.assertIn("warm-cache evidence cannot support an approve decision", res["errors"])

    def test_warm_inspect_check_ok_under_approve(self):
        # inspect/report warm reads are advisory — they do NOT gate a merge.
        art = _valid_artifact()
        art["checks"][1]["cache_provenance"] = "warm"  # layer=inspect
        self.assertTrue(evidence_schema.validate_artifact(art)["ok"])

    def test_warm_gate_check_ok_when_not_approving(self):
        art = _valid_artifact()
        art["checks"][0]["cache_provenance"] = "warm"
        art["decision"]["decision"] = "needs_human"
        res = evidence_schema.validate_artifact(art)
        self.assertNotIn("warm-cache evidence cannot support an approve decision", res["errors"])
        self.assertTrue(res["ok"], res["errors"])


class BuildArtifactTests(unittest.TestCase):
    def test_build_from_minimal_bundle_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_minimal_bundle(tmp)
            art = evidence_schema.build_artifact(bundle)
        res = evidence_schema.validate_artifact(art)
        self.assertTrue(res["ok"], res["errors"])
        self.assertIn(art["decision"]["decision"], ("approve", "needs_human", "block"))
        # every synthesized check is a cold read → invariant can never trip on build.
        self.assertTrue(all(c["cache_provenance"] == "cold" for c in art["checks"]))
        # the four core artifacts show up as present (pass) checks.
        pass_ids = {c["id"] for c in art["checks"] if c["status"] == "pass"}
        self.assertLessEqual({"validate", "native_check", "scenarios", "scan_secrets"}, pass_ids)

    def test_build_gitless_tempdir_null_git_but_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_minimal_bundle(tmp)
            art = evidence_schema.build_artifact(bundle)
        self.assertIsNone(art["git"]["commit_sha"])
        self.assertIsNone(art["git"]["branch"])
        self.assertTrue(evidence_schema.validate_artifact(art)["ok"])

    def test_build_copies_human_signoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_minimal_bundle(tmp)
            (Path(tmp) / "human_signoff.json").write_text(
                json.dumps([{"role": "senior-dev", "name": "Grace", "at": "2026-07-03T09:00:00+00:00"}]))
            art = evidence_schema.build_artifact(bundle)
        self.assertEqual(art["human_signoffs"][0]["role"], "senior-dev")
        self.assertEqual(art["human_signoffs"][0]["name"], "Grace")
        self.assertTrue(evidence_schema.validate_artifact(art)["ok"])

    def test_build_marks_redaction_scanned_when_artifact_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_minimal_bundle(tmp)
            (Path(tmp) / "state.redact.json").write_text(json.dumps({"mode": "local"}))
            art = evidence_schema.build_artifact(bundle)
        self.assertTrue(art["redaction"]["scanned"])
        self.assertEqual(art["redaction"]["mode"], "local")

    def test_build_extra_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_minimal_bundle(tmp)
            art = evidence_schema.build_artifact(
                bundle, extra={"instance": {"odoo_version": "19.0"}})
        self.assertEqual(art["instance"]["odoo_version"], "19.0")
        self.assertTrue(evidence_schema.validate_artifact(art)["ok"])


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            evidence_schema.main(argv)
        return json.loads(buf.getvalue())

    def test_cli_validate_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "artifact.json"
            p.write_text(json.dumps(_valid_artifact()))
            out = self._run(["validate", str(p)])
        self.assertTrue(out["ok"], out["errors"])

    def test_cli_validate_missing_file(self):
        out = self._run(["validate", "/no/such/file.json"])
        self.assertFalse(out["ok"])
        self.assertTrue(any("file not found" in e for e in out["errors"]))

    def test_cli_build_writes_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_minimal_bundle(tmp)
            out_file = Path(tmp) / "out.json"
            out = self._run(["build", bundle, "--out", str(out_file)])
            self.assertEqual(out["schema_version"], "1.0")
            self.assertTrue(out_file.exists())
            written = json.loads(out_file.read_text())
        self.assertTrue(evidence_schema.validate_artifact(written)["ok"])

    def test_cli_unknown_command(self):
        out = self._run(["frobnicate"])
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
