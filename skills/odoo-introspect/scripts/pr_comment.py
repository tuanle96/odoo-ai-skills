"""
Sticky PR-comment renderer for the odoo-ai evidence gate (local, no Odoo).

Turns a deploy_gate.py report (approve / needs_human / block) into a STICKY
Markdown PR comment: a hidden marker line lets CI post-or-update exactly one
comment per PR instead of stacking a fresh one every run. The renderer
(build_comment) is pure Python and unit-tested; the ``post`` subcommand shells
out to ``gh`` to create or patch the comment.

Usage
-----
    pr_comment.py build <gate_report.json> [--bundle-dir DIR]
    pr_comment.py post  <gate_report.json> --repo owner/name --pr N [--bundle-dir DIR]

``build`` prints {"ok", "markdown"}. ``post`` finds the existing marker comment
via ``gh api`` and PATCHes it (or POSTs a new one), printing {"ok", ...}. It
needs ``gh`` on PATH and GITHUB_TOKEN/GH_TOKEN in the env; without them it
prints {"ok": false, "error": ..., "markdown": ...} and still exits 0.

Output: pure JSON to stdout. Exit code is ALWAYS 0 — a bad report / missing gh
is reported as JSON, never a crash; the CI action, not this script, enforces.
"""
import json
import os
import shutil
import subprocess
import sys

# Hidden HTML comment that makes the PR comment "sticky": the post step finds
# the one comment carrying this marker and updates it in place.
MARKER = "<!-- odoo-ai-gate -->"

_BADGES = {
    "approve":     "✅ **approve** — automated gate passed",
    "needs_human": "\U0001f7e1 **needs_human** — human review required before deploy",
    "block":       "⛔ **block** — blocking findings must be fixed",
}
_UNKNOWN_BADGE = "❓ **unknown** — could not read a decision from the gate report"

_FOOTER = ("CI-bound evidence gate with an explicit trust boundary — human "
           "review stays mandatory for sensitive domains.")


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo, no network — unit-tested)
# ---------------------------------------------------------------------------

def _cell(value):
    """Render *value* safe for a single-line Markdown table cell (collapse
    whitespace/newlines, escape the column separator). '-' for the empty case."""
    text = " ".join(str(value).split()).replace("|", "\\|")
    return text or "-"


def resolve_decision(report):
    """Return the verdict string, tolerating both the nested
    ``{"decision": {"decision": ...}}`` and the flat ``{"decision": "..."}``
    report shapes. None when neither is a readable string."""
    d = report.get("decision")
    if isinstance(d, dict):
        d = d.get("decision")
    return d if isinstance(d, str) else None


def _normalize_finding(f):
    """A finding is either a plain string (legacy deploy_gate) or a dict that may
    carry ``severity``/``remediation`` (severity-class reports). Normalize both to
    a ``(text, severity, remediation)`` tuple; severity/remediation are None when
    absent so the caller renders '-'."""
    if isinstance(f, dict):
        text = (f.get("finding") or f.get("message") or f.get("title")
                or f.get("summary") or f.get("rule") or json.dumps(f, default=str))
        return str(text), f.get("severity"), (f.get("remediation") or f.get("fix")
                                              or f.get("hint"))
    return str(f), None, None


def _collect_findings(report):
    """Blocking findings from the nested decision, falling back to a flat report."""
    d = report.get("decision")
    raw = d.get("blocking_findings") if isinstance(d, dict) else None
    raw = raw or report.get("blocking_findings") or report.get("findings") or []
    return [_normalize_finding(f) for f in raw] if isinstance(raw, list) else []


def _collect_missing(report):
    """Missing-evidence artifact names (nested decision → evidence → flat)."""
    d = report.get("decision")
    if isinstance(d, dict) and isinstance(d.get("missing_evidence"), list):
        return list(d["missing_evidence"])
    ev = report.get("evidence")
    if isinstance(ev, dict) and isinstance(ev.get("missing"), list):
        return list(ev["missing"])
    m = report.get("missing_evidence")
    return list(m) if isinstance(m, list) else []


def _collect_approvals(report):
    d = report.get("decision")
    if isinstance(d, dict) and isinstance(d.get("required_approvals"), list):
        return list(d["required_approvals"])
    return []


def build_comment(report, bundle_dir=None):
    """Render the sticky Markdown comment for a deploy-gate *report* dict."""
    if not isinstance(report, dict):
        report = {}
    verdict = resolve_decision(report)
    badge = _BADGES.get(verdict, _UNKNOWN_BADGE)

    lines = [MARKER, f"## Odoo AI evidence gate — {badge}", ""]

    # Context line: policy + risk tier when the report carries them.
    risk = report.get("risk")
    tier = risk.get("tier") if isinstance(risk, dict) else None
    ctx = []
    if report.get("policy"):
        ctx.append(f"**Policy:** `{_cell(report['policy'])}`")
    if tier:
        ctx.append(f"**Risk tier:** `{_cell(tier)}`")
    if ctx:
        lines += [" • ".join(ctx), ""]

    # Findings table (severity / remediation columns show '-' when absent).
    findings = _collect_findings(report)
    lines.append("### Findings")
    if findings:
        lines += ["| Finding | Severity | Remediation |", "| --- | --- | --- |"]
        for text, severity, remediation in findings:
            lines.append(
                f"| {_cell(text)} | {_cell(severity) if severity else '-'} "
                f"| {_cell(remediation) if remediation else '-'} |")
    else:
        lines.append("No blocking findings.")
    lines.append("")

    # Missing evidence — checks that were not run (unknown, not safe).
    missing = _collect_missing(report)
    if missing:
        lines.append("### Missing evidence")
        lines += [f"- `{_cell(m)}`" for m in missing]
        lines.append("")

    # Required human sign-offs — a needs_human verdict is only actionable with these.
    approvals = _collect_approvals(report)
    if approvals:
        lines.append("### Required sign-offs")
        lines += [f"- {_cell(a)}" for a in approvals]
        lines.append("")

    # Severity summary, only when the report carries one (severity-class reports).
    summary = report.get("severity_summary")
    if isinstance(summary, dict) and summary:
        lines += ["### Severity summary", "| Severity | Count |", "| --- | --- |"]
        for sev, count in summary.items():
            lines.append(f"| {_cell(sev)} | {_cell(count)} |")
        lines.append("")

    # Reproduce-locally: the plugin-relative CLI form + the direct script form.
    bd = bundle_dir or report.get("bundle_dir") or "<bundle_dir>"
    lines += [
        "### Reproduce locally",
        "```bash",
        f'"${{CLAUDE_PLUGIN_ROOT:-.}}"/skills/odoo-introspect/scripts/odoo-ai deploy-gate --strict {bd}',
        f"python skills/odoo-introspect/scripts/deploy_gate.py --strict {bd}",
        "```",
        "",
        "---",
        f"<sub>{_FOOTER}</sub>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# gh plumbing (post subcommand) — subprocess only, no PyGithub dependency
# ---------------------------------------------------------------------------

def _gh(args, input_text=None):
    """Run a ``gh`` command. Returns ``(ok, stdout, err)`` — never raises."""
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True,
                              input=input_text, timeout=60)
    except Exception as e:  # noqa: BLE001
        return False, "", f"{type(e).__name__}: {e}"
    if proc.returncode != 0:
        return False, proc.stdout or "", (proc.stderr or "").strip()
    return True, proc.stdout or "", ""


def _find_marker_comment(repo, pr):
    """Return ``(comment_id_or_None, error)`` for the existing sticky comment."""
    ok, out, err = _gh(["api", f"repos/{repo}/issues/{pr}/comments", "--paginate"])
    if not ok:
        return None, err
    try:
        comments = json.loads(out or "[]")
    except json.JSONDecodeError:
        # `gh api --paginate` can concatenate array pages as `][`; merge them.
        try:
            comments = json.loads("[" + (out or "").replace("][", ",") + "]")
        except Exception:  # noqa: BLE001
            return None, "could not parse gh comments response"
    for c in comments if isinstance(comments, list) else []:
        if isinstance(c, dict) and MARKER in (c.get("body") or ""):
            return c.get("id"), ""
    return None, ""


def post_comment(report, repo, pr, bundle_dir=None):
    """Post or update the sticky comment. Returns a JSON-able status dict; the
    rendered markdown is ALWAYS included so a token-less run still surfaces it."""
    md = build_comment(report, bundle_dir=bundle_dir)
    if shutil.which("gh") is None:
        return {"ok": False, "error": "gh CLI not found on PATH", "markdown": md}
    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        return {"ok": False, "error": "GITHUB_TOKEN/GH_TOKEN not set in env",
                "markdown": md}

    payload = json.dumps({"body": md})
    existing, err = _find_marker_comment(repo, pr)
    if err:
        return {"ok": False, "error": f"gh api (list comments) failed: {err}",
                "markdown": md}
    if existing is not None:
        ok, out, err = _gh(["api", "--method", "PATCH",
                            f"repos/{repo}/issues/comments/{existing}", "--input", "-"],
                           input_text=payload)
        action = "updated"
    else:
        ok, out, err = _gh(["api", "--method", "POST",
                            f"repos/{repo}/issues/{pr}/comments", "--input", "-"],
                           input_text=payload)
        action = "created"
    if not ok:
        return {"ok": False, "error": f"gh api ({action}) failed: {err}", "markdown": md}
    url = None
    try:
        url = json.loads(out).get("html_url")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "action": action, "comment_url": url, "markdown": md}


def _parse_argv(rest):
    """Positional <report_path> plus --repo/--pr/--bundle-dir flags."""
    report_path = repo = pr = bundle_dir = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--repo" and i + 1 < len(rest):
            repo, i = rest[i + 1], i + 2
        elif tok == "--pr" and i + 1 < len(rest):
            pr, i = rest[i + 1], i + 2
        elif tok == "--bundle-dir" and i + 1 < len(rest):
            bundle_dir, i = rest[i + 1], i + 2
        elif report_path is None and not tok.startswith("-"):
            report_path, i = tok, i + 1
        else:
            i += 1
    return report_path, repo, pr, bundle_dir


def _emit(obj):
    print(json.dumps(obj, indent=2, default=str, allow_nan=False))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("build", "post"):
        return _emit({"ok": False,
                      "error": "usage: pr_comment.py build|post <gate_report.json> "
                               "[--repo owner/name --pr N] [--bundle-dir DIR]"})
    sub = argv[0]
    report_path, repo, pr, bundle_dir = _parse_argv(argv[1:])
    if not report_path:
        return _emit({"ok": False, "error": "missing <gate_report.json>"})

    try:
        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)
    except Exception as e:  # noqa: BLE001
        return _emit({"ok": False,
                      "error": f"cannot read report: {type(e).__name__}: {e}"})

    if sub == "build":
        return _emit({"ok": True, "markdown": build_comment(report, bundle_dir=bundle_dir)})

    # sub == "post"
    if not repo or not pr:
        return _emit({"ok": False, "error": "post requires --repo owner/name and --pr N",
                      "markdown": build_comment(report, bundle_dir=bundle_dir)})
    return _emit(post_comment(report, repo, pr, bundle_dir=bundle_dir))


if __name__ == "__main__":
    sys.exit(main())
