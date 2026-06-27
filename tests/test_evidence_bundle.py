"""
Unit tests for evidence_bundle (pure render tests + one integration test).

Run with:
    python3 -m unittest tests.test_evidence_bundle -v

Render tests build hand-made report dicts and never require deploy_gate.
The integration test writes a tiny bundle dir and calls build_evidence end-to-end.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import evidence_bundle  # noqa: E402

_ALL_ARTIFACTS = [
    "native_check", "env_diff", "scenarios", "validate",
    "security", "trace", "upgrade",
]


def _make_report(decision="approve", tier="normal", reasons=None,
                 present=None, missing=None, signals=None,
                 blocking_findings=None, required_approvals=None,
                 missing_evidence=None, caveat="", warnings=None):
    """Build a minimal hand-made report dict mirroring deploy_gate.build_report shape."""
    present_list = present or []
    missing_list = missing if missing is not None else [
        a for a in _ALL_ARTIFACTS if a not in present_list
    ]
    return {
        "bundle_dir": "/tmp/fake",
        "evidence": {
            "present": present_list,
            "missing": missing_list,
            "signals": signals or {},
        },
        "risk": {
            "tier": tier,
            "reasons": reasons or ["no elevated signals detected"],
        },
        "decision": {
            "decision": decision,
            "blocking_findings": blocking_findings or [],
            "required_approvals": required_approvals or [],
            "missing_evidence": missing_evidence or [],
        },
        "_warnings": warnings or [],
        "_caveat": caveat,
    }


# ---------------------------------------------------------------------------
# Verdict badge
# ---------------------------------------------------------------------------

class VerdictBadgeTests(unittest.TestCase):
    def test_approve_badge(self):
        md = evidence_bundle.render_markdown(_make_report(decision="approve"))
        self.assertIn("✅ APPROVE", md)

    def test_needs_human_badge(self):
        md = evidence_bundle.render_markdown(_make_report(decision="needs_human"))
        self.assertIn("🟡 NEEDS HUMAN", md)

    def test_block_badge(self):
        md = evidence_bundle.render_markdown(_make_report(decision="block"))
        self.assertIn("⛔ BLOCK", md)


# ---------------------------------------------------------------------------
# Evidence table
# ---------------------------------------------------------------------------

class EvidenceTableTests(unittest.TestCase):
    def test_present_artifact_row(self):
        report = _make_report(
            present=["validate"],
            signals={"validate_blocking": 0, "validate_warning": 3},
        )
        md = evidence_bundle.render_markdown(report)
        self.assertIn("| `validate`", md)
        self.assertIn("✅ present", md)
        # signal column contains "blocking"
        self.assertIn("blocking:", md)

    def test_missing_artifact_row(self):
        report = _make_report(present=[], signals={})
        md = evidence_bundle.render_markdown(report)
        self.assertIn("| `native_check`", md)
        self.assertIn("⬜ absent", md)
        self.assertIn("not checked", md)

    def test_all_known_artifacts_appear(self):
        report = _make_report(present=[], signals={})
        md = evidence_bundle.render_markdown(report)
        for art in _ALL_ARTIFACTS:
            self.assertIn(f"| `{art}`", md, f"artifact '{art}' missing from table")

    def test_signal_for_env_diff(self):
        report = _make_report(
            present=["env_diff"],
            signals={"env_diff_severity": "high"},
        )
        md = evidence_bundle.render_markdown(report)
        self.assertIn("severity:", md)
        self.assertIn("high", md)

    def test_signal_for_upgrade_blocking(self):
        report = _make_report(
            present=["upgrade"],
            signals={"upgrade_blocking": 2},
        )
        md = evidence_bundle.render_markdown(report)
        self.assertIn("blocking:", md)
        self.assertIn("`2`", md)


# ---------------------------------------------------------------------------
# Blocking findings / required approvals / missing evidence
# ---------------------------------------------------------------------------

class FindingsAndApprovalsTests(unittest.TestCase):
    def test_blocking_findings_listed(self):
        report = _make_report(
            decision="block",
            blocking_findings=["validate: 2 blocking finding(s) must be fixed"],
        )
        md = evidence_bundle.render_markdown(report)
        self.assertIn("Blocking findings", md)
        self.assertIn("validate: 2 blocking", md)

    def test_required_approvals_listed_as_checkboxes(self):
        report = _make_report(
            decision="needs_human",
            tier="critical",
            required_approvals=["senior Odoo dev sign-off", "finance/ops owner sign-off"],
        )
        md = evidence_bundle.render_markdown(report)
        self.assertIn("Required approvals", md)
        self.assertIn("- [ ] senior Odoo dev sign-off", md)
        self.assertIn("- [ ] finance/ops owner sign-off", md)

    def test_missing_evidence_section(self):
        report = _make_report(
            decision="needs_human",
            missing_evidence=["native_check", "scenarios"],
        )
        md = evidence_bundle.render_markdown(report)
        self.assertIn("Missing evidence", md)
        self.assertIn("`native_check`", md)
        self.assertIn("`scenarios`", md)

    def test_no_blocking_section_when_empty(self):
        report = _make_report(decision="approve", blocking_findings=[])
        md = evidence_bundle.render_markdown(report)
        self.assertNotIn("Blocking findings", md)


# ---------------------------------------------------------------------------
# Defensive / footer
# ---------------------------------------------------------------------------

class DefensiveAndFooterTests(unittest.TestCase):
    def test_empty_report_does_not_crash(self):
        """render_markdown must not raise on a completely empty dict."""
        md = evidence_bundle.render_markdown({})
        self.assertIsInstance(md, str)
        self.assertIn("Odoo Deployment Gate", md)

    def test_footer_always_present(self):
        md = evidence_bundle.render_markdown(_make_report())
        self.assertIn("odoo-ai", md)
        self.assertIn("agent-written, tool-verified, human-approved", md)

    def test_caveat_in_footer(self):
        report = _make_report(caveat="Always have a human review.")
        md = evidence_bundle.render_markdown(report)
        self.assertIn("Always have a human review.", md)

    def test_warnings_section(self):
        report = _make_report(warnings=["validate.json: parse error — JSONDecodeError"])
        md = evidence_bundle.render_markdown(report)
        self.assertIn("Warnings", md)
        self.assertIn("parse error", md)


# ---------------------------------------------------------------------------
# Integration (uses deploy_gate.build_report end-to-end)
# ---------------------------------------------------------------------------

class IntegrationTest(unittest.TestCase):
    def test_build_evidence_blocks_on_validate_blocking(self):
        """validate.json with blocking>0 must yield decision=block + BLOCK in markdown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "validate.json").write_text(
                json.dumps({"summary": {"blocking": 1, "warning": 0}})
            )
            (Path(tmpdir) / "upgrade.json").write_text(
                json.dumps({"summary": {"blocking": 0}})
            )

            result = evidence_bundle.build_evidence(tmpdir)

        self.assertEqual(result["decision"], "block")
        self.assertIn("⛔ BLOCK", result["markdown"])
        self.assertIn("report", result)
        self.assertIsInstance(result["markdown"], str)
        # validate must appear as present in the table
        self.assertIn("✅ present", result["markdown"])


if __name__ == "__main__":
    unittest.main()
