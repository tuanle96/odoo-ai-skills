"""
Deployment approval gate (local, no Odoo) — reads a bundle directory of
evidence JSON artifacts produced by the odoo-ai tool-chain, classifies the
change risk, and emits a go/no-go decision that requires explicit human
approval for high-risk models.

No Odoo connection required. All helpers are pure Python, unit-testable, and
read only from the filesystem. The caller/agent reads the ``decision`` field;
exit code is always 0 so a non-zero return never suppresses the JSON output.

Usage
-----
    python3 deploy_gate.py <bundle_dir>

Output: pure JSON to stdout.

Bundle artifacts recognised (all optional; absence = "not checked"):
    native_check.json   env_diff.json   scenarios.json   validate.json
    security.json       trace.json      upgrade.json

Each is the JSON output of the corresponding ``odoo-ai`` tool.
"""
import json
import sys
from pathlib import Path

_CAVEAT = (
    "Risk classification is a first-pass gate, not a substitute for human "
    "judgement. Absent evidence means the check was not run — treat it as "
    "unknown, not safe. 'approve' only means no automated blocker was found; "
    "a senior developer MUST review critical data-model or security changes "
    "regardless of the automated tier."
)

_KNOWN_ARTIFACTS = [
    "native_check", "env_diff", "scenarios", "validate",
    "security", "trace", "upgrade",
]

# Evidence that MUST be present for an automatic 'approve' (absence → needs_human)
_REQUIRED_EVIDENCE = frozenset({"native_check", "scenarios", "validate"})

# Model-name fragments that flag an accounting/stock/payment/hr domain
_SENSITIVE_KEYWORDS = ("account", "stock", "payment", "hr", "payroll")


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo, unit-testable)
# ---------------------------------------------------------------------------

def _dig(obj, *keys):
    """Safely navigate a nested dict/None chain; returns None on any missing step."""
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _is_sensitive_model(name):
    """Return True when a model name suggests an accounting/stock/payment/hr domain."""
    if not name:
        return False
    low = str(name).lower()
    return any(kw in low for kw in _SENSITIVE_KEYWORDS)


def assemble_evidence(artifacts):
    """Normalise raw artifact dict into a decision-relevant evidence summary.

    Parameters
    ----------
    artifacts : dict
        ``{name: parsed_json_or_None}`` keyed by artifact stem (e.g.
        ``"validate"``, ``"upgrade"``).  ``None`` means absent or parse error.

    Returns
    -------
    dict
        ``{"present": [...], "missing": [...], "signals": {...}}``
    """
    present = [n for n, v in artifacts.items() if v is not None]
    missing = [n for n, v in artifacts.items() if v is None]

    val  = artifacts.get("validate")  or {}
    edif = artifacts.get("env_diff")  or {}
    upg  = artifacts.get("upgrade")   or {}
    scen = artifacts.get("scenarios") or {}
    sec  = artifacts.get("security")  or {}
    trc  = artifacts.get("trace")     or {}

    signals = {
        "validate_blocking":  _dig(val,  "summary", "blocking"),
        "validate_warning":   _dig(val,  "summary", "warning"),
        "env_diff_severity":  _dig(edif, "summary", "severity"),
        "upgrade_blocking":   _dig(upg,  "summary", "blocking"),
        "scenarios_risk_tier": _dig(scen, "risk", "tier"),
        "security_superuser":   bool(sec.get("is_superuser")),
        "security_has_warnings": bool(sec.get("_warnings")),
        "trace_error": trc.get("error"),
    }

    # Sensitive-model detection: scan any model-name hint in scenarios / security
    candidates = [
        _dig(scen, "risk", "model"),
        scen.get("model"),
        sec.get("model"),
        sec.get("model_name"),
    ]
    signals["sensitive_model"] = any(_is_sensitive_model(m) for m in candidates if m)

    return {"present": present, "missing": missing, "signals": signals}


def classify_change_risk(evidence):
    """Classify overall change risk from assembled evidence signals.

    Returns
    -------
    dict
        ``{"tier": "critical"|"high"|"normal", "reasons": [str]}``

    Rules (first match wins at each level):
      critical — scenarios tier critical, OR upgrade/validate blocking > 0,
                 OR env_diff severity high.
      high     — scenarios tier high, OR validate warnings > 0,
                 OR security superuser / warnings present.
      normal   — no elevated signals.
    """
    s = evidence.get("signals", {})
    reasons = []
    tier = "normal"

    # --- critical (any one is sufficient) ---
    if s.get("scenarios_risk_tier") == "critical":
        reasons.append("scenarios risk tier: critical")
        tier = "critical"
    upg = (s.get("upgrade_blocking") or 0)
    if upg > 0:
        reasons.append(f"upgrade: {upg} blocking issue(s)")
        tier = "critical"
    val_b = (s.get("validate_blocking") or 0)
    if val_b > 0:
        reasons.append(f"validate: {val_b} blocking issue(s)")
        tier = "critical"
    if s.get("env_diff_severity") == "high":
        reasons.append("env_diff severity: high")
        tier = "critical"

    # --- high (only evaluated when not already critical) ---
    if tier == "normal":
        if s.get("scenarios_risk_tier") == "high":
            reasons.append("scenarios risk tier: high")
            tier = "high"
        val_w = (s.get("validate_warning") or 0)
        if val_w > 0:
            reasons.append(f"validate: {val_w} warning(s)")
            tier = "high"
        if s.get("security_superuser"):
            reasons.append("security: is_superuser detected")
            tier = "high"
        if s.get("security_has_warnings"):
            reasons.append("security: _warnings present")
            tier = "high"

    if not reasons:
        reasons.append("no elevated signals detected")

    return {"tier": tier, "reasons": reasons}


def gate_decision(evidence, risk):
    """Determine the deployment gate outcome from evidence and risk tier.

    Returns
    -------
    dict
        ``{"decision": "block"|"needs_human"|"approve",
           "required_approvals": [...],
           "blocking_findings": [...],
           "missing_evidence": [...]}``

    Decision rules (precedence order):
      block        — any blocking finding (validate/upgrade blocking > 0 or
                     trace error non-null).
      needs_human  — risk tier critical or high, OR required evidence absent.
      approve      — normal risk + all required evidence present + no blocking.

    required_approvals:
      critical tier → "senior Odoo dev sign-off"; additionally, when
      scenarios/security signal an accounting/stock/payment/hr model →
      "finance/ops owner sign-off".
    """
    s = evidence.get("signals", {})
    missing_set = set(evidence.get("missing", []))

    # Collect blocking findings
    blocking_findings = []
    val_b = (s.get("validate_blocking") or 0)
    if val_b > 0:
        blocking_findings.append(f"validate: {val_b} blocking finding(s) must be fixed")
    upg_b = (s.get("upgrade_blocking") or 0)
    if upg_b > 0:
        blocking_findings.append(f"upgrade: {upg_b} blocking issue(s) must be resolved")
    if s.get("trace_error"):
        blocking_findings.append(f"trace: error recorded → {s['trace_error']}")

    # Required evidence absent
    missing_evidence = sorted(_REQUIRED_EVIDENCE & missing_set)

    # Decision
    if blocking_findings:
        decision = "block"
    elif risk.get("tier") in ("critical", "high") or missing_evidence:
        decision = "needs_human"
    else:
        decision = "approve"

    # Required approvals (only meaningful for critical tier)
    required_approvals = []
    if risk.get("tier") == "critical":
        required_approvals.append("senior Odoo dev sign-off")
        if s.get("sensitive_model"):
            required_approvals.append("finance/ops owner sign-off")

    return {
        "decision": decision,
        "required_approvals": required_approvals,
        "blocking_findings": blocking_findings,
        "missing_evidence": missing_evidence,
    }


def build_report(bundle_dir):
    """Read artifact files from *bundle_dir*, classify risk, return a gate report.

    Parameters
    ----------
    bundle_dir : str | Path

    Returns
    -------
    dict
        ``{"bundle_dir", "evidence", "risk", "decision", "_warnings", "_caveat"}``
    """
    bundle_path = Path(bundle_dir)
    warnings = []
    artifacts = {}

    for name in _KNOWN_ARTIFACTS:
        fp = bundle_path / f"{name}.json"
        if not fp.exists():
            artifacts[name] = None
            continue
        try:
            artifacts[name] = json.loads(fp.read_text())
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name}.json: parse error — {type(exc).__name__}: {exc}")
            artifacts[name] = None

    evidence = assemble_evidence(artifacts)
    risk     = classify_change_risk(evidence)
    decision = gate_decision(evidence, risk)

    return {
        "bundle_dir": str(bundle_dir),
        "evidence":   evidence,
        "risk":       risk,
        "decision":   decision,
        "_warnings":  warnings,
        "_caveat":    _CAVEAT,
    }


def main(argv=None):
    """Entry point: ``deploy_gate.py <bundle_dir>``."""
    args = (argv if argv is not None else sys.argv)[1:]
    if not args:
        print(json.dumps({
            "error":    "No bundle_dir supplied.",
            "usage":    "deploy_gate.py <bundle_dir>",
            "_caveat":  _CAVEAT,
        }, indent=2))
        return
    report = build_report(args[0])
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
