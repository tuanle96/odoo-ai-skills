"""
Unit tests for pr_comment.py — the sticky PR-comment renderer. Pure-function
tests of build_comment / resolve_decision (no network, no gh, no filesystem).
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pr_comment as pc  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic reports mirroring deploy_gate.build_report's shape.
# ---------------------------------------------------------------------------

def _block_report():
    return {
        "bundle_dir": "evidence_bundle",
        "policy": "v2-strict",
        "risk": {"tier": "high"},
        "decision": {
            "decision": "block",
            "required_approvals": [],
            "blocking_findings": [
                "validate: 3 blocking finding(s) must be fixed",
                "scan-secrets: 1 potential secret(s) — must not ship",
            ],
            "missing_evidence": ["runtime_path"],
        },
    }


def _approve_report():
    return {
        "bundle_dir": "evidence_bundle",
        "policy": "v1-legacy",
        "risk": {"tier": "normal"},
        "decision": {
            "decision": "approve",
            "required_approvals": [],
            "blocking_findings": [],
            "missing_evidence": [],
        },
    }


class TestBuildComment(unittest.TestCase):
    def test_block_report_renders_marker_badge_findings_and_repro(self):
        md = pc.build_comment(_block_report())
        # Sticky marker must be the FIRST line (post step keys off it).
        self.assertTrue(md.startswith(pc.MARKER))
        # Block badge.
        self.assertIn("⛔", md)
        self.assertIn("block", md)
        # Findings table rendered with both findings.
        self.assertIn("| Finding | Severity | Remediation |", md)
        self.assertIn("validate: 3 blocking finding(s) must be fixed", md)
        self.assertIn("scan-secrets: 1 potential secret(s) — must not ship", md)
        # Missing-evidence list.
        self.assertIn("`runtime_path`", md)
        # Reproduce-locally block: both the CLI and the direct-script forms.
        self.assertIn("### Reproduce locally", md)
        self.assertIn("deploy-gate --strict evidence_bundle", md)
        self.assertIn("deploy_gate.py --strict evidence_bundle", md)
        self.assertIn("CLAUDE_PLUGIN_ROOT", md)
        # Footer / trust-boundary line.
        self.assertIn("human review stays mandatory", md)

    def test_approve_report_renders_check_and_no_findings(self):
        md = pc.build_comment(_approve_report())
        self.assertTrue(md.startswith(pc.MARKER))
        self.assertIn("✅", md)
        self.assertIn("approve", md)
        self.assertIn("No blocking findings.", md)

    def test_flat_decision_shape(self):
        # A flat report: decision is a bare string, findings at top level.
        flat = {
            "decision": "block",
            "blocking_findings": ["upgrade: 1 blocking issue(s) must be resolved"],
            "bundle_dir": "bundle",
        }
        md = pc.build_comment(flat)
        self.assertEqual(pc.resolve_decision(flat), "block")
        self.assertIn("⛔", md)
        self.assertIn("upgrade: 1 blocking issue(s) must be resolved", md)

    def test_nested_decision_shape(self):
        self.assertEqual(pc.resolve_decision(_block_report()), "block")
        self.assertEqual(pc.resolve_decision(_approve_report()), "approve")

    def test_severity_column_renders_when_findings_carry_severity(self):
        report = {
            "decision": {
                "decision": "needs_human",
                "blocking_findings": [
                    {"finding": "account.move write bypasses posted lock",
                     "severity": "S4", "remediation": "guard on state != 'posted'"},
                ],
                "missing_evidence": [],
            },
            "severity_summary": {"S4": 1, "S2": 3},
        }
        md = pc.build_comment(report)
        self.assertIn("🟡", md)
        self.assertIn("S4", md)
        self.assertIn("guard on state != 'posted'", md)
        # Severity summary table present when the report carries one.
        self.assertIn("### Severity summary", md)
        self.assertIn("| S4 | 1 |", md)

    def test_needs_human_renders_required_signoffs(self):
        report = {
            "decision": {
                "decision": "needs_human",
                "required_approvals": ["senior Odoo dev sign-off"],
                "blocking_findings": [],
                "missing_evidence": ["scenarios"],
            },
        }
        md = pc.build_comment(report)
        self.assertIn("### Required sign-offs", md)
        self.assertIn("senior Odoo dev sign-off", md)

    def test_missing_keys_do_not_crash(self):
        # Empty / malformed reports must render the unknown badge, never raise.
        for bad in ({}, {"decision": None}, {"decision": {}},
                    {"decision": 123}, {"decision": {"decision": None}},
                    {"risk": "not-a-dict", "decision": "approve"},
                    None, "not-a-dict", []):
            md = pc.build_comment(bad)
            self.assertTrue(md.startswith(pc.MARKER))
            self.assertIn("### Reproduce locally", md)

    def test_unknown_decision_badge(self):
        md = pc.build_comment({"decision": "weird-value"})
        self.assertIn("❓", md)
        self.assertIn("unknown", md)

    def test_pipe_in_finding_is_escaped(self):
        # A finding containing '|' must not break the Markdown table.
        report = {"decision": {"decision": "block",
                               "blocking_findings": ["a | b | c pipeline broke"]}}
        md = pc.build_comment(report)
        self.assertIn("\\|", md)

    def test_missing_evidence_falls_back_to_evidence_block(self):
        # When decision has no missing_evidence, fall back to evidence.missing.
        report = {"decision": {"decision": "needs_human"},
                  "evidence": {"present": [], "missing": ["native_check"]}}
        md = pc.build_comment(report)
        self.assertIn("`native_check`", md)

    def test_bundle_dir_override(self):
        md = pc.build_comment(_approve_report(), bundle_dir="/tmp/mybundle")
        self.assertIn("deploy-gate --strict /tmp/mybundle", md)


if __name__ == "__main__":
    unittest.main()
