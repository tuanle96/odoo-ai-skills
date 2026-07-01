"""
Unit tests for deploy_gate.py Layer L policy v2 (strict) — the un-fakeable gate.

These assert (a) the strict path BLOCKS the fake-test / forged-evidence patterns
Oracle enumerated, (b) a genuine CI bundle APPROVES, and (c) the legacy default
(strict=False) is completely unchanged by the new artifacts.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import deploy_gate as dg      # noqa: E402
import provenance as prov     # noqa: E402


# ---------------------------------------------------------------------------
# Bundle helpers
# ---------------------------------------------------------------------------

def _write(p, name, obj):
    (p / name).write_text(json.dumps(obj))


def _clean_core_v2(p, **overrides):
    """Write a genuine, all-green Layer L bundle into dir *p*. Overrides replace
    individual artifacts (pass name=obj)."""
    arts = {
        "native_check.json": {"confirmed_candidates": []},
        "scenarios.json": {"risk": {"tier": "normal"}, "model": "res.partner"},
        "validate.json": {"summary": {"blocking": 0, "warning": 0}},
        "scan_secrets.json": {"count": 0},
        "diff_targets.json": {"targets": [{"id": "res.partner:write:x.py:1",
                                           "model": "res.partner", "method": "write",
                                           "changed_exec_lines": [10]}]},
        "changed_coverage.json": {"ok": True, "targets": [{"target_id": "res.partner:write:x.py:1"}],
                                  "summary": {"targets": 1, "fully_covered": 1, "gate": "pass"}},
        "runtime_path.json": {"targets": [{"target_id": "res.partner:write:x.py:1"}],
                              "summary": {"targets": 1, "bound": 1, "unbound": 0},
                              "producer": {"tool": "runtime_observer",
                                           "tool_digest": "sha256:TESTPROBE",
                                           "trace_integrity": "sealed", "sealed": True}},
        "test_quality.json": {"summary": {"files": 1, "blocking": 0, "warning": 0}},
        "scenario_satisfaction.json": {"ok": True, "unsatisfied": [], "summary": {"required": 2, "satisfied": 2}},
        "mutation_smoke.json": {"summary": {"targets": 1, "mutants": 3, "survived": 0}, "decision": "pass"},
    }
    for name, obj in overrides.items():
        arts[name.replace("__", ".") + ".json" if not name.endswith("json") else name] = obj
    for name, obj in arts.items():
        _write(p, name, obj)


class _KeyEnv:
    """Context manager setting the CI signing key (and, optionally, the CI-set
    expected head_sha) for provenance. ``head`` mirrors what CI would export in
    ODOO_AI_EXPECTED_HEAD_SHA — never sourced from the agent's bundle."""
    def __init__(self, head=None, probe=None):
        self._head = head
        self._probe = probe

    def __enter__(self):
        self._old = {v: os.environ.get(v) for v in
                     ("ODOO_AI_ATTEST_KEY", "ODOO_AI_EXPECTED_HEAD_SHA", "ODOO_AI_EXPECTED_PROBE_DIGEST")}
        os.environ["ODOO_AI_ATTEST_KEY"] = "test-ci-signing-key"
        for var, val in (("ODOO_AI_EXPECTED_HEAD_SHA", self._head),
                         ("ODOO_AI_EXPECTED_PROBE_DIGEST", self._probe)):
            if val is not None:
                os.environ[var] = val
            else:
                os.environ.pop(var, None)
        return self

    def __exit__(self, *a):
        for var, old in self._old.items():
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old


def _sign_all(p, head="b"):
    """Attest every *.json artifact in the bundle with the test key, writing
    <name>.provenance.json envelopes. Requires _KeyEnv active."""
    key = prov.load_key()
    for fp in sorted(p.glob("*.json")):
        if "provenance" in fp.name:
            continue
        env = prov.attest(fp.stem, fp.read_bytes(),
                          subject={"base_sha": "a", "head_sha": head, "tree_sha": "c"},
                          producer={"runner": "ci"}, environment={"odoo_version": "18.0"},
                          command={"argv": ["odoo-bin"], "exit_code": 0}, key=key)
        (p / f"{fp.stem}.provenance.json").write_text(json.dumps(env))


# ---------------------------------------------------------------------------
# Legacy unchanged
# ---------------------------------------------------------------------------

class TestLegacyUnchanged(unittest.TestCase):

    def test_new_artifacts_do_not_affect_legacy_approve(self):
        """A legacy (non-strict) bundle still approves even if a failing Layer L
        artifact is present — legacy must not read the new signals as blockers."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _write(p, "native_check.json", {"confirmed_candidates": []})
            _write(p, "scenarios.json", {"risk": {"tier": "normal"}})
            _write(p, "validate.json", {"summary": {"blocking": 0, "warning": 0}})
            _write(p, "scan_secrets.json", {"count": 0})
            # a failing runtime_path present — legacy must ignore it
            _write(p, "runtime_path.json", {"summary": {"targets": 1, "bound": 0, "unbound": 1}})
            r = dg.build_report(p)  # strict defaults False
        self.assertEqual(r["decision"]["decision"], "approve")
        self.assertEqual(r["policy"], "v1-legacy")


# ---------------------------------------------------------------------------
# Strict: the un-fakeable blocks
# ---------------------------------------------------------------------------

class TestStrictBlocks(unittest.TestCase):

    def test_runtime_path_unbound_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"runtime_path.json": {"targets": [{"target_id": "res.partner:write:x.py:1"}], "summary": {"targets": 1, "bound": 0, "unbound": 1}}})
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("runtime-path" in b for b in r["decision"]["blocking_findings"]))

    def test_test_quality_blocking_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"test_quality.json": {"summary": {"files": 1, "blocking": 2, "warning": 0}}})
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("test-quality" in b for b in r["decision"]["blocking_findings"]))

    def test_changed_coverage_fail_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"changed_coverage.json": {"ok": False, "targets": [{"target_id": "res.partner:write:x.py:1"}], "summary": {"targets": 1, "fully_covered": 0, "gate": "fail"}}})
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("changed-coverage" in b for b in r["decision"]["blocking_findings"]))

    def test_scenario_unsatisfied_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"scenario_satisfaction.json": {"ok": False, "unsatisfied": ["batch"], "summary": {"required": 2, "satisfied": 1}}})
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("scenario-satisfaction" in b and "batch" in b
                            for b in r["decision"]["blocking_findings"]))

    def test_forged_provenance_blocks(self):
        """A hand-authored (well-shaped) envelope must fail the HMAC check."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv():
                _sign_all(p)
                # forge: tamper the signed base_sha AFTER signing on one envelope
                env_fp = p / "validate.provenance.json"
                env = json.loads(env_fp.read_text())
                env["subject"]["base_sha"] = "FORGED"
                env_fp.write_text(json.dumps(env))
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("provenance" in b for b in r["decision"]["blocking_findings"]))

    def test_missing_provenance_needs_human_not_approve(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)   # no envelopes written
            with _KeyEnv():
                r = dg.build_report(p, strict=True)
        # provenance is core-required v2 → missing → needs_human (never approve)
        self.assertEqual(r["decision"]["decision"], "needs_human")
        self.assertIn("provenance", r["decision"]["missing_evidence"])

    def test_mutation_survived_high_risk_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{
                "scenarios.json": {"risk": {"tier": "high"}, "model": "sale.order"},
                "mutation_smoke.json": {"summary": {"targets": 1, "mutants": 3, "survived": 1}},
            })
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("mutation-smoke" in b for b in r["decision"]["blocking_findings"]))


# ---------------------------------------------------------------------------
# Strict: the genuine bundle approves
# ---------------------------------------------------------------------------

class TestStrictApprove(unittest.TestCase):

    def test_genuine_signed_bundle_approves(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)          # normal-risk, all green
            _write(p, "mutation_smoke.json", {"summary": {"targets": 1, "mutants": 3, "survived": 0}})
            with _KeyEnv(head="b", probe="sha256:TESTPROBE"):   # CI exports head + pinned probe
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "approve")
        self.assertEqual(r["policy"], "v2-strict")
        self.assertEqual(r["decision"]["blocking_findings"], [])

    def test_missing_mutation_smoke_needs_human(self):
        """mutation_smoke is core-required for strict (Oracle: the behavioral-proof
        backstop for normal-risk weak-assert tests). Its absence can't auto-approve."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            (p / "mutation_smoke.json").unlink()
            with _KeyEnv(head="b"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "needs_human")
        self.assertIn("mutation_smoke", r["decision"]["missing_evidence"])

    def test_normal_risk_surviving_mutant_blocks(self):
        """A surviving mutant blocks on ANY risk tier now, not just high/critical."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"mutation_smoke.json": {"summary": {"targets": 1, "mutants": 3, "survived": 1}}})
            with _KeyEnv(head="b"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("mutation-smoke" in b for b in r["decision"]["blocking_findings"]))

    def test_missing_core_v2_artifact_needs_human(self):
        """Genuine but incomplete: no runtime_path → cannot auto-approve."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            (p / "runtime_path.json").unlink()
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "needs_human")
        self.assertIn("runtime_path", r["decision"]["missing_evidence"])


# ---------------------------------------------------------------------------
# Regression: the bypasses the code review found (C1/C2/H1/H2) must BLOCK
# ---------------------------------------------------------------------------

def _sign_one(p, stem, head="b"):
    key = prov.load_key()
    fp = p / f"{stem}.json"
    env = prov.attest(stem, fp.read_bytes(),
                      subject={"base_sha": "a", "head_sha": head, "tree_sha": "c"},
                      producer={"runner": "ci"}, environment={"odoo_version": "18.0"},
                      command={"argv": ["odoo-bin"], "exit_code": 0}, key=key)
    (p / f"{stem}.provenance.json").write_text(json.dumps(env))


class TestReviewBypasses(unittest.TestCase):

    def test_C1_forged_green_plus_one_valid_unrelated_envelope_blocks(self):
        """Hand-forged green artifacts + a single valid envelope over an UNRELATED
        file must NOT approve: each consumed artifact must be content-bound."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)                       # all green, hand-written
            _write(p, "dummy.json", {"whatever": 1})
            with _KeyEnv():
                _sign_one(p, "dummy")               # one valid envelope, unrelated
                r = dg.build_report(p, strict=True)
        self.assertNotEqual(r["decision"]["decision"], "approve")
        self.assertTrue(any("not covered by a valid CI attestation" in b
                            for b in r["decision"]["blocking_findings"]))

    def test_C2_tamper_signed_artifact_bytes_blocks(self):
        """Sign validate.json, then rewrite its bytes after signing → block."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv():
                _sign_all(p)
                # tamper AFTER signing — hash no longer matches the signed one
                _write(p, "validate.json", {"summary": {"blocking": 0, "warning": 0, "x": 1}})
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("validate" in b and "attestation" in b
                            for b in r["decision"]["blocking_findings"]))

    def test_H1_empty_diff_targets_needs_human(self):
        """diff_targets claiming zero changed targets makes proofs vacuous →
        never a silent approve."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"diff_targets.json": {"targets": []}})
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "needs_human")

    def test_H1_coverage_target_mismatch_blocks(self):
        """coverage/runtime proofs that reference DIFFERENT targets than git found
        must block — they prove nothing about the actual change."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{
                "diff_targets.json": {"targets": [{"id": "sale.order:action_confirm:s.py:1",
                                                   "model": "sale.order", "method": "action_confirm",
                                                   "changed_exec_lines": [5]}]},
                "changed_coverage.json": {"ok": True, "targets": [{"target_id": "OTHER:x:y.py:9"}],
                                          "summary": {"targets": 1, "fully_covered": 1, "gate": "pass"}},
            })
            with _KeyEnv():
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("does not cover the change" in b
                            for b in r["decision"]["blocking_findings"]))

    def test_H2_replayed_bundle_wrong_head_blocks(self):
        """A fully-signed bundle from a DIFFERENT commit (envelopes signed head 'b',
        but CI says the real head is something else) must block as stale/replayed."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv(head="the-real-head-sha"):   # CI-set head ≠ envelopes' "b"
                _sign_all(p)                            # envelopes carry head_sha "b"
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("stale/replayed" in b for b in r["decision"]["blocking_findings"]))

    def test_H2_agent_manifest_head_cannot_authorize_replay(self):
        """The agent-authored manifest.json head_sha must NOT satisfy the freshness
        check — only the CI-set env var can. Manifest-only → needs_human, not approve."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            _write(p, "manifest.json", {"head_sha": "b"})   # agent picks the head
            with _KeyEnv():                                  # NO CI-set env head
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertNotEqual(r["decision"]["decision"], "approve")

    def test_H2_empty_head_sha_blocks(self):
        """CRITICAL regression (Oracle final review): envelopes with an EMPTY
        head_sha must NOT slip past the freshness check when CI set the real head."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv(head="REAL_HEAD"):     # CI knows the real head
                _sign_all(p, head="")           # but envelopes carry head_sha=""
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("stale/replayed or unbound" in b or "head_sha" in b
                            for b in r["decision"]["blocking_findings"]))

    def test_wrong_artifact_name_signature_blocks(self):
        """An envelope whose bytes hash-match but that NAMES a different artifact
        must not vouch for this one."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv(head="b"):
                _sign_all(p, head="b")
                # re-sign validate.json's bytes under the WRONG name
                key = prov.load_key()
                fp = p / "validate.json"
                env = prov.attest("scenarios", fp.read_bytes(),
                                  subject={"base_sha": "a", "head_sha": "b", "tree_sha": "c"},
                                  producer={"runner": "ci"}, environment={},
                                  command={"argv": []}, key=key)
                (p / "validate.provenance.json").write_text(json.dumps(env))
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")

    def test_H2_matching_head_approves(self):
        """Approves when the CI-set env head matches the envelopes."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv(head="b", probe="sha256:TESTPROBE"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "approve")


class TestTrustedObservationBoundary(unittest.TestCase):

    def test_unsealed_observer_blocks(self):
        """A runtime_path whose observer reports tampering (trace disabled) blocks."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"runtime_path.json": {
                "targets": [{"target_id": "res.partner:write:x.py:1"}],
                "summary": {"unbound": 0},
                "producer": {"tool": "runtime_observer", "tool_digest": "sha256:X",
                             "trace_integrity": "tampered", "sealed": False}}})
            with _KeyEnv(head="b"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("not sealed" in b for b in r["decision"]["blocking_findings"]))

    def test_missing_observer_seal_blocks(self):
        """A runtime_path with NO producer self-report can't be trusted → block."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p, **{"runtime_path.json": {
                "targets": [{"target_id": "res.partner:write:x.py:1"}],
                "summary": {"unbound": 0}}})   # no producer block
            with _KeyEnv(head="b"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("not sealed" in b for b in r["decision"]["blocking_findings"]))

    def test_pinned_probe_digest_mismatch_blocks(self):
        """When CI pins the observer digest, a different (swapped) observer blocks."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)   # sealed producer with tool_digest sha256:TESTPROBE
            with _KeyEnv(head="b", probe="sha256:DIFFERENT"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("pinned probe" in b for b in r["decision"]["blocking_findings"]))

    def test_pinned_probe_digest_match_approves(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv(head="b", probe="sha256:TESTPROBE"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "approve")

    def test_unpinned_probe_digest_needs_human(self):
        """Fail closed: a sealed observer with NO CI-pinned digest can't auto-approve
        (a modified/swapped observer would be undetectable) — Oracle observer review."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            with _KeyEnv(head="b"):   # head pinned, probe NOT pinned
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "needs_human")


class TestRedGreenReplay(unittest.TestCase):

    def test_bugfix_requires_red_green_replay(self):
        """A manifest flagged as a bug fix must carry a red/green replay."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            _write(p, "manifest.json", {"is_bugfix": True})
            with _KeyEnv(head="b"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "needs_human")
        self.assertIn("red_green_replay", r["decision"]["missing_evidence"])

    def test_bugfix_failed_replay_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            _write(p, "manifest.json", {"is_bugfix": True})
            _write(p, "red_green_replay.json", {"ok": False, "base_failed": False,
                                                "head_passed": True, "reasons": ["no red"]})
            with _KeyEnv(head="b"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "block")
        self.assertTrue(any("red-green-replay" in b for b in r["decision"]["blocking_findings"]))

    def test_bugfix_passing_replay_approves(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            _clean_core_v2(p)
            _write(p, "manifest.json", {"is_bugfix": True})
            _write(p, "red_green_replay.json", {"ok": True, "base_failed": True,
                                                "head_passed": True, "same_identity": True,
                                                "red_is_legit": True})
            with _KeyEnv(head="b", probe="sha256:TESTPROBE"):
                _sign_all(p)
                r = dg.build_report(p, strict=True)
        self.assertEqual(r["decision"]["decision"], "approve")


if __name__ == "__main__":
    unittest.main()
