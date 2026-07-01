"""
Unit tests for provenance.py — pure-function tests (in-memory envelopes, no
filesystem I/O except CLI-round-trip tests which use tempfile).
"""
import hashlib
import hmac
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import provenance as prov  # noqa: E402

KEY = b"trusted-ci-secret"
OTHER_KEY = b"a-different-secret"


def _subject(**overrides):
    base = {"base_sha": "aaa111", "head_sha": "bbb222", "tree_sha": "ccc333"}
    base.update(overrides)
    return base


def _envelope(artifact_bytes=b'{"lines_covered": 42}', key=KEY, **subject_overrides):
    return prov.attest(
        "changed_coverage",
        artifact_bytes,
        _subject(**subject_overrides),
        {"runner": "github-actions", "runner_image_digest": "sha256:deadbeef",
         "tool_digest": {"deploy_gate.py": "sha256:cafebabe"}},
        {"odoo_version": "18.0", "database_uuid": "db-uuid", "db_template_digest": "sha256:feedface"},
        {"argv": ["deploy_gate.py", "bundle"], "exit_code": 0,
         "stdout_sha256": "sha256:00", "stderr_sha256": "sha256:00"},
        key,
    )


# ---------------------------------------------------------------------------
# sign / verify round trip
# ---------------------------------------------------------------------------

class TestSignVerifyRoundTrip(unittest.TestCase):

    def test_sign_then_verify_ok(self):
        env = _envelope()
        result = prov.verify_envelope(env, KEY)
        self.assertEqual(result, {"ok": True, "reasons": []})

    def test_signature_field_is_hex_string(self):
        env = _envelope()
        self.assertTrue(prov._is_hex(env["signature"]))

    def test_envelope_has_expected_schema(self):
        env = _envelope()
        self.assertEqual(env["schema"], "odoo-ai-evidence/v1")


# ---------------------------------------------------------------------------
# Tampering must invalidate the signature
# ---------------------------------------------------------------------------

class TestTampering(unittest.TestCase):

    def test_tamper_base_sha(self):
        env = _envelope()
        env["subject"]["base_sha"] = "forged"
        self.assertFalse(prov.verify_envelope(env, KEY)["ok"])

    def test_tamper_artifact_sha256(self):
        env = _envelope()
        env["artifact"]["sha256"] = "sha256:" + "0" * 64
        self.assertFalse(prov.verify_envelope(env, KEY)["ok"])

    def test_tamper_artifact_name(self):
        env = _envelope()
        env["artifact_name"] = "not_the_real_artifact"
        self.assertFalse(prov.verify_envelope(env, KEY)["ok"])

    def test_wrong_key_fails(self):
        env = _envelope(key=KEY)
        result = prov.verify_envelope(env, OTHER_KEY)
        self.assertFalse(result["ok"])
        self.assertIn("signature does not match recomputed HMAC", result["reasons"])

    def test_no_key_fails(self):
        env = _envelope(key=KEY)
        result = prov.verify_envelope(env, None)
        self.assertFalse(result["ok"])
        self.assertTrue(any("no signing key" in r for r in result["reasons"]))


# ---------------------------------------------------------------------------
# canonical_bytes: key-order independence
# ---------------------------------------------------------------------------

class TestCanonicalBytes(unittest.TestCase):

    def test_key_order_independent(self):
        env_a = {"schema": "odoo-ai-evidence/v1", "artifact_name": "x",
                 "subject": {"base_sha": "a", "head_sha": "b"}, "signature": "ignored"}
        env_b = {"signature": "ignored", "subject": {"head_sha": "b", "base_sha": "a"},
                 "artifact_name": "x", "schema": "odoo-ai-evidence/v1"}
        self.assertEqual(prov.canonical_bytes(env_a), prov.canonical_bytes(env_b))

    def test_signature_excluded_from_canonical_bytes(self):
        env_a = {"schema": "odoo-ai-evidence/v1", "signature": "aaa"}
        env_b = {"schema": "odoo-ai-evidence/v1", "signature": "zzz"}
        self.assertEqual(prov.canonical_bytes(env_a), prov.canonical_bytes(env_b))


# ---------------------------------------------------------------------------
# verify_artifact_bytes
# ---------------------------------------------------------------------------

class TestVerifyArtifactBytes(unittest.TestCase):

    def test_exact_bytes_match(self):
        artifact_bytes = b'{"lines_covered": 42}'
        env = _envelope(artifact_bytes=artifact_bytes)
        self.assertTrue(prov.verify_artifact_bytes(env, artifact_bytes))

    def test_mutated_bytes_do_not_match(self):
        env = _envelope(artifact_bytes=b'{"lines_covered": 42}')
        self.assertFalse(prov.verify_artifact_bytes(env, b'{"lines_covered": 43}'))

    def test_verify_artifact_bytes_malformed_envelope(self):
        self.assertFalse(prov.verify_artifact_bytes({"no": "artifact"}, b"whatever"))
        self.assertFalse(prov.verify_artifact_bytes("not a dict", b"whatever"))


# ---------------------------------------------------------------------------
# Malformed envelopes must never raise
# ---------------------------------------------------------------------------

class TestMalformedEnvelopes(unittest.TestCase):

    def test_missing_signature(self):
        env = _envelope()
        del env["signature"]
        result = prov.verify_envelope(env, KEY)
        self.assertFalse(result["ok"])
        self.assertIn("signature missing or not a hex string", result["reasons"])

    def test_not_a_dict(self):
        for bad in (None, "a string", 123, [1, 2, 3]):
            result = prov.verify_envelope(bad, KEY)
            self.assertFalse(result["ok"])
            self.assertEqual(result["reasons"], ["envelope is not a JSON object"])

    def test_wrong_schema(self):
        env = _envelope()
        env["schema"] = "some-other-schema/v9"
        result = prov.verify_envelope(env, KEY)
        self.assertFalse(result["ok"])
        self.assertTrue(any("schema mismatch" in r for r in result["reasons"]))

    def test_malformed_artifact_sha(self):
        env = _envelope()
        env["artifact"]["sha256"] = "not-a-real-hash"
        result = prov.verify_envelope(env, KEY)
        self.assertFalse(result["ok"])
        self.assertTrue(any("artifact.sha256" in r for r in result["reasons"]))

    def test_empty_dict_does_not_raise(self):
        result = prov.verify_envelope({}, KEY)
        self.assertFalse(result["ok"])
        self.assertTrue(len(result["reasons"]) > 0)


# ---------------------------------------------------------------------------
# HMAC is actually used (known-vector check)
# ---------------------------------------------------------------------------

class TestKnownVector(unittest.TestCase):

    def test_signature_matches_manual_hmac_computation(self):
        env = _envelope(key=KEY)
        env_wo_sig = {k: v for k, v in env.items() if k != "signature"}
        subject_bytes = json.dumps(env_wo_sig, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")
        expected = hmac.new(KEY, subject_bytes, hashlib.sha256).hexdigest()
        self.assertEqual(env["signature"], expected)

    def test_sha256_hex_format(self):
        digest = prov.sha256_hex(b"hello")
        self.assertEqual(digest, "sha256:" + hashlib.sha256(b"hello").hexdigest())


# ---------------------------------------------------------------------------
# load_key
# ---------------------------------------------------------------------------

class TestLoadKey(unittest.TestCase):

    def test_load_key_unset_returns_none(self, ):
        import os
        old = os.environ.pop("ODOO_AI_ATTEST_KEY", None)
        try:
            self.assertIsNone(prov.load_key())
        finally:
            if old is not None:
                os.environ["ODOO_AI_ATTEST_KEY"] = old

    def test_load_key_set_returns_bytes(self):
        import os
        old = os.environ.get("ODOO_AI_ATTEST_KEY")
        os.environ["ODOO_AI_ATTEST_KEY"] = "my-secret"
        try:
            self.assertEqual(prov.load_key(), b"my-secret")
        finally:
            if old is None:
                del os.environ["ODOO_AI_ATTEST_KEY"]
            else:
                os.environ["ODOO_AI_ATTEST_KEY"] = old


# ---------------------------------------------------------------------------
# CLI round trip (attest -> verify via main())
# ---------------------------------------------------------------------------

class TestCliRoundTrip(unittest.TestCase):

    def setUp(self):
        import os
        self._old_key = os.environ.get("ODOO_AI_ATTEST_KEY")
        os.environ["ODOO_AI_ATTEST_KEY"] = "cli-secret"
        self._tmpdir = tempfile.TemporaryDirectory()
        self.artifact_path = Path(self._tmpdir.name) / "changed_coverage.json"
        self.artifact_path.write_text('{"lines_covered": 10}')
        self.envelope_path = Path(self._tmpdir.name) / "envelope.json"

    def tearDown(self):
        import os
        if self._old_key is None:
            os.environ.pop("ODOO_AI_ATTEST_KEY", None)
        else:
            os.environ["ODOO_AI_ATTEST_KEY"] = self._old_key
        self._tmpdir.cleanup()

    def _run_main(self, argv):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prov.main(argv)
        return json.loads(buf.getvalue())

    def test_attest_then_verify_via_cli(self):
        envelope = self._run_main(["attest", str(self.artifact_path), "--name", "changed_coverage"])
        self.envelope_path.write_text(json.dumps(envelope))

        result = self._run_main(["verify", str(self.envelope_path), "--artifact", str(self.artifact_path)])
        self.assertTrue(result["ok"])
        self.assertTrue(result["artifact_match"])

    def test_attest_without_key_reports_error(self):
        import os
        os.environ.pop("ODOO_AI_ATTEST_KEY", None)
        result = self._run_main(["attest", str(self.artifact_path), "--name", "changed_coverage"])
        self.assertEqual(result, {"error": "ODOO_AI_ATTEST_KEY not set", "ok": False})


if __name__ == "__main__":
    unittest.main()
