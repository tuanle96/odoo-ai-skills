"""
Evidence bundle — consolidates gate outputs from a deploy bundle directory into
(a) a structured verdict dict and (b) a human-readable, PR-ready Markdown comment.

Reuses deploy_gate.build_report for all evidence assembly and risk/decision logic.
No Odoo connection required; reads only from the filesystem.

Usage
-----
    python3 evidence_bundle.py <bundle_dir> [--md-out <path>]

Output: JSON to stdout; Markdown written to <bundle_dir>/evidence.md (or --md-out).
Exit code is always 0 so a non-zero return never suppresses the JSON output.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import deploy_gate  # noqa: E402

_BADGES = {
    "approve":     "✅ APPROVE",
    "needs_human": "🟡 NEEDS HUMAN",
    "block":       "⛔ BLOCK",
}

# Same order as deploy_gate._KNOWN_ARTIFACTS so the table is stable
_ARTIFACTS_ORDER = [
    "native_check", "env_diff", "scenarios", "validate",
    "security", "trace", "upgrade",
]


def _artifact_signal(art: str, signals: dict) -> str:
    """Return a concise signal string for a single artifact table row."""
    if art == "env_diff":
        sev = signals.get("env_diff_severity")
        return f"severity: `{sev}`" if sev is not None else "—"
    if art == "scenarios":
        tier = signals.get("scenarios_risk_tier")
        return f"risk tier: `{tier}`" if tier is not None else "—"
    if art == "validate":
        blocking = signals.get("validate_blocking")
        warnings = signals.get("validate_warning")
        parts = []
        if blocking is not None:
            parts.append(f"blocking: `{blocking}`")
        if warnings is not None:
            parts.append(f"warnings: `{warnings}`")
        return ", ".join(parts) or "—"
    if art == "security":
        parts = []
        if signals.get("security_superuser"):
            parts.append("superuser detected")
        if signals.get("security_has_warnings"):
            parts.append("has warnings")
        return ", ".join(parts) or "—"
    if art == "trace":
        err = signals.get("trace_error")
        return f"error: `{err}`" if err else "—"
    if art == "upgrade":
        blocking = signals.get("upgrade_blocking")
        return f"blocking: `{blocking}`" if blocking is not None else "—"
    return "—"  # native_check and any unknown artifact


def render_markdown(report: dict) -> str:
    """Turn a deploy_gate report dict into a PR-ready Markdown comment string.

    Pure function — no I/O. Defensive: tolerates missing or None keys at every
    level so callers can pass a partial / hand-made report in tests.

    Parameters
    ----------
    report : dict
        Shape: ``{bundle_dir, evidence, risk, decision, _warnings, _caveat}``
        as returned by ``deploy_gate.build_report``.

    Returns
    -------
    str
        Standard Markdown (headings, tables, lists, checkboxes).
    """
    decision_block = report.get("decision") or {}
    risk_block = report.get("risk") or {}
    evidence_block = report.get("evidence") or {}
    caveat = report.get("_caveat") or ""
    warnings = report.get("_warnings") or []

    verdict_key = (decision_block.get("decision") or "").lower()
    badge = _BADGES.get(verdict_key, f"❓ {verdict_key.upper() or 'UNKNOWN'}")

    lines: list[str] = []
    lines.append(f"## {badge} — Odoo Deployment Gate")
    lines.append("")

    # --- Risk summary ---
    tier = risk_block.get("tier") or "unknown"
    reasons = risk_block.get("reasons") or []
    lines.append(f"**Risk tier:** `{tier.upper()}`")
    for r in reasons:
        lines.append(f"- {r}")
    lines.append("")

    # --- Gate evidence table ---
    present_set = set(evidence_block.get("present") or [])
    signals = evidence_block.get("signals") or {}

    lines.append("### Gate evidence")
    lines.append("")
    lines.append("| Artifact | Status | Signal |")
    lines.append("|----------|--------|--------|")
    for art in _ARTIFACTS_ORDER:
        if art in present_set:
            status = "✅ present"
            sig_str = _artifact_signal(art, signals)
        else:
            status = "⬜ absent"
            sig_str = "not checked"
        lines.append(f"| `{art}` | {status} | {sig_str} |")
    lines.append("")

    # --- Blocking findings ---
    blocking = decision_block.get("blocking_findings") or []
    if blocking:
        lines.append("### ⛔ Blocking findings")
        for f in blocking:
            lines.append(f"- {f}")
        lines.append("")

    # --- Required approvals (checkbox list for PR reviewers) ---
    required_approvals = decision_block.get("required_approvals") or []
    if required_approvals:
        lines.append("### 👤 Required approvals")
        for a in required_approvals:
            lines.append(f"- [ ] {a}")
        lines.append("")

    # --- Missing evidence ---
    missing_evidence = decision_block.get("missing_evidence") or []
    if missing_evidence:
        lines.append("### ⚠️ Missing evidence")
        for m in missing_evidence:
            lines.append(f"- `{m}` was not run — treat as unknown, not safe")
        lines.append("")

    # --- Tool warnings (parse errors, etc.) ---
    if warnings:
        lines.append("### ℹ️ Warnings")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    # --- Footer ---
    lines.append("---")
    lines.append("")
    lines.append(
        "_Generated by odoo-ai · agent-written, tool-verified, human-approved_"
    )
    if caveat:
        lines.append("")
        lines.append(f"> {caveat}")

    return "\n".join(lines)


def build_evidence(bundle_dir: str) -> dict:
    """Build the full evidence bundle: gate report + rendered markdown.

    Parameters
    ----------
    bundle_dir : str | Path
        Directory holding the JSON gate artifacts.

    Returns
    -------
    dict
        ``{"report": <deploy_gate report>, "markdown": str, "decision": str}``
    """
    report = deploy_gate.build_report(bundle_dir)
    md = render_markdown(report)
    return {
        "report": report,
        "markdown": md,
        "decision": report["decision"]["decision"],
    }


def main(argv=None):
    """Entry point: evidence_bundle.py <bundle_dir> [--md-out <path>]"""
    args = list(argv if argv is not None else sys.argv)[1:]

    if not args:
        print(json.dumps({
            "error": "No bundle_dir supplied.",
            "usage": "evidence_bundle.py <bundle_dir> [--md-out <path>]",
        }, indent=2))
        return

    bundle_dir = args[0]
    md_out = None

    i = 1
    while i < len(args):
        if args[i] == "--md-out" and i + 1 < len(args):
            md_out = args[i + 1]
            i += 2
        else:
            i += 1

    if md_out is None:
        md_out = str(Path(bundle_dir) / "evidence.md")

    result = build_evidence(bundle_dir)
    Path(md_out).write_text(result["markdown"])

    print(json.dumps({
        "decision": result["decision"],
        "md_path": md_out,
        "markdown_present": True,
    }, indent=2))


if __name__ == "__main__":
    main()
