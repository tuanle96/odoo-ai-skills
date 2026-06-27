"""
Deployment approval gate (local, no Odoo) — reads a bundle directory of evidence
JSON artifacts produced by the odoo-ai tool-chain, classifies the change risk,
and emits a go/no-go decision that requires explicit human approval for high-risk
changes.

No Odoo connection required. All helpers are pure Python, unit-testable, and read
only from the filesystem. The caller/agent reads the ``decision`` field; exit code
is always 0 so a non-zero return never suppresses the JSON output.

Usage
-----
    python3 deploy_gate.py <bundle_dir>

Output: pure JSON to stdout.

Bundle artifacts recognised (all optional; absence = "not checked"). Files may be
named canonically (``validate.json``) OR with the CLI's ``<label>.<cmd>.json``
form (``patch.validate.json``, ``env.env-diff.json``) — both are resolved:
    native_check  env_diff  scenarios  validate  security  trace  upgrade  scan_secrets

An optional ``manifest.json`` describes the change so the gate can demand the
*right* evidence:
    {"touched_models": [...], "has_migration": bool, "touches_security": bool,
     "touches_controller": bool, "changed_files": [...]}
Without it the gate is conservative: it requires the core evidence and never
approves when an artifact is present-but-unparseable or a secret was found.
"""
import json
import math
import sys
from pathlib import Path

_CAVEAT = (
    "Risk classification is a first-pass gate, not a substitute for human "
    "judgement. Absent evidence means the check was not run — treat it as "
    "unknown, not safe. 'approve' only means no automated blocker was found AND "
    "the evidence required for this change is present and parseable; a senior "
    "developer MUST still review critical data-model, security, or controller "
    "changes regardless of the automated tier."
)

# Canonical artifact name -> accepted filename suffixes (the CLI writes
# <label>.<cmd>.json, so e.g. env-fingerprint -> env.env_fingerprint.json must
# still resolve as the canonical "env_diff" evidence).
_ARTIFACT_PATTERNS = {
    "native_check": ["native_check"],
    "env_diff": ["env_diff", "env-diff", "env_fingerprint"],
    "scenarios": ["scenarios"],
    "validate": ["validate"],
    "security": ["security"],
    "trace": ["trace"],
    "upgrade": ["upgrade", "upgrade_check", "upgrade-diff"],
    "scan_secrets": ["scan_secrets", "scan-secrets"],
}

# Core evidence required for ANY automatic 'approve' (absence → needs_human).
# scan_secrets is core: a missing secret scan must NOT silently approve.
_CORE_REQUIRED = frozenset({"native_check", "scenarios", "validate", "scan_secrets"})

# Model-name fragments that flag an accounting/stock/payment/hr domain
_SENSITIVE_KEYWORDS = ("account", "stock", "payment", "hr", "payroll")


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo, unit-testable)
# ---------------------------------------------------------------------------

def _reject_dups(pairs):
    """object_pairs_hook that raises on a duplicate key — a `{"blocking":1,
    "blocking":0}` payload must NOT silently keep the last value and clear a
    blocker. Recurses naturally (the hook runs per object)."""
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _reject_const(c):
    """parse_constant hook — reject NaN/Infinity/-Infinity (not standard JSON)."""
    raise ValueError(f"non-standard JSON constant: {c}")


def _reject_nonfinite(num_str):
    """parse_float hook — reject a number that overflows to inf (e.g. 1e10000).
    parse_constant only catches the literal NaN/Infinity tokens, not overflow."""
    f = float(num_str)
    if not math.isfinite(f):
        raise ValueError(f"non-finite number: {num_str}")
    return f


def _loads_strict(text):
    """json.loads that rejects duplicate object keys, NaN/Infinity constants, AND
    overflow-to-inf floats — all anti-spoofing guards for the gate."""
    return json.loads(text, object_pairs_hook=_reject_dups,
                      parse_constant=_reject_const, parse_float=_reject_nonfinite)


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


def manifest_warnings(manifest):
    """Shape problems that make a manifest untrusted (→ needs_human). [] = clean.

    A present-but-wrong-typed field must NOT silently skip required evidence: a
    non-bool ``has_migration`` (e.g. "") would coerce to False and drop the
    upgrade demand; a ``changed_files`` with non-string entries would skip path
    inference. Both now warn → needs_human."""
    if manifest is None:
        return []
    if not isinstance(manifest, dict):
        return ["manifest.json: not an object — ignored (forces needs_human)"]
    w = []
    for fld in ("changed_files", "touched_models"):
        if fld in manifest:
            v = manifest[fld]
            if not isinstance(v, list):
                w.append(f"manifest.json: {fld} must be a list of strings — manifest untrusted")
            elif not all(isinstance(x, str) for x in v):
                w.append(f"manifest.json: {fld} has non-string entries — manifest untrusted")
    for fld in ("has_migration", "touches_security", "touches_controller"):
        if fld in manifest and not isinstance(manifest[fld], bool):
            w.append(f"manifest.json: {fld} must be a boolean — manifest untrusted")
    return w


def manifest_flags(manifest):
    """Normalise the optional change manifest into the flags the gate uses.

    Reads explicit booleans and also infers them from ``changed_files`` paths
    (migrations/, controllers/, security/ or an ir.model.access csv).
    """
    mf = manifest if isinstance(manifest, dict) else {}
    tm = mf.get("touched_models")
    tm = [m for m in tm if isinstance(m, str)] if isinstance(tm, list) else []
    cf = mf.get("changed_files")
    cf = cf if isinstance(cf, list) else []   # a non-list (e.g. a string) is ignored, not char-iterated
    # normalise separators (a Windows `migrations\18.0\post.py` must still match)
    norm_files = [f.lower().replace("\\", "/") for f in cf if isinstance(f, str)]
    flags = {
        "has_migration": bool(mf.get("has_migration")),
        "touches_security": bool(mf.get("touches_security")),
        "touches_controller": bool(mf.get("touches_controller")),
        "touched_models": tm,
        "changed_files": norm_files,
        "present": bool(manifest),
    }
    for fl in norm_files:
        if "migrations/" in fl:
            flags["has_migration"] = True
        if "controllers/" in fl:
            flags["touches_controller"] = True
        # security: any security/ dir (module-relative or nested), an ACL csv, or a rule file
        if ("security/" in fl
                or (fl.endswith(".csv") and "access" in fl)
                or "ir.rule" in fl
                or "ir_rule" in fl):
            flags["touches_security"] = True
    return flags


def required_evidence(flags):
    """The evidence set required for an 'approve', given the manifest flags."""
    req = set(_CORE_REQUIRED)
    if flags.get("has_migration"):
        req.add("upgrade")
    if flags.get("touches_security") or flags.get("touches_controller"):
        req.add("security")
    return req


def assemble_evidence(artifacts, manifest=None):
    """Normalise raw artifact dict into a decision-relevant evidence summary.

    Parameters
    ----------
    artifacts : dict
        ``{name: parsed_json_or_None}`` keyed by canonical artifact name.
        ``None`` means absent or parse error.
    manifest : dict | None
        Optional change descriptor (see module docstring).

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
    scn  = artifacts.get("scan_secrets") or {}

    flags = manifest_flags(manifest)

    signals = {
        "validate_blocking":  _dig(val,  "summary", "blocking"),
        "validate_warning":   _dig(val,  "summary", "warning"),
        "env_diff_severity":  _dig(edif, "summary", "severity"),
        "upgrade_blocking":   _dig(upg,  "summary", "blocking"),
        "upgrade_warning":    _dig(upg,  "summary", "warning"),
        "scenarios_risk_tier": _dig(scen, "risk", "tier"),
        "security_superuser":   bool(sec.get("is_superuser")),
        "security_has_warnings": bool(sec.get("_warnings")),
        "trace_error": trc.get("error"),
        "scan_secrets_count": scn.get("count") if isinstance(scn.get("count"), int) else None,
        "has_migration": flags["has_migration"],
        "touches_security": flags["touches_security"],
        "touches_controller": flags["touches_controller"],
        "manifest_present": flags["present"],
    }

    # Sensitive-model detection: scenarios/security model hints + manifest models
    # + changed-file PATHS (a change under account/ hr/ stock/ payment/ is
    # sensitive even when touched_models wasn't declared).
    candidates = [
        _dig(scen, "risk", "model"),
        scen.get("model"),
        sec.get("model"),
        sec.get("model_name"),
    ] + flags["touched_models"] + flags.get("changed_files", [])
    signals["sensitive_model"] = any(_is_sensitive_model(m) for m in candidates if m)

    return {"present": present, "missing": missing, "signals": signals}


def classify_change_risk(evidence):
    """Classify overall change risk from assembled evidence signals.

    Returns ``{"tier": "critical"|"high"|"normal", "reasons": [str]}``.

    critical — scenarios tier critical, OR upgrade/validate blocking > 0,
               OR env_diff severity high, OR a secret was found.
    high     — scenarios tier high, OR validate warnings > 0,
               OR security superuser/warnings, OR the manifest says the change
               touches security / a public controller.
    normal   — no elevated signals.
    """
    s = evidence.get("signals", {})
    reasons = []
    tier = "normal"

    # --- critical (any one is sufficient) ---
    if s.get("scenarios_risk_tier") == "critical":
        reasons.append("scenarios risk tier: critical"); tier = "critical"
    upg = (s.get("upgrade_blocking") or 0)
    if upg > 0:
        reasons.append(f"upgrade: {upg} blocking issue(s)"); tier = "critical"
    val_b = (s.get("validate_blocking") or 0)
    if val_b > 0:
        reasons.append(f"validate: {val_b} blocking issue(s)"); tier = "critical"
    if s.get("env_diff_severity") == "high":
        reasons.append("env_diff severity: high"); tier = "critical"
    if (s.get("scan_secrets_count") or 0) > 0:
        reasons.append(f"scan-secrets: {s['scan_secrets_count']} potential secret(s)"); tier = "critical"

    # --- high (only when not already critical) ---
    if tier == "normal":
        if s.get("scenarios_risk_tier") == "high":
            reasons.append("scenarios risk tier: high"); tier = "high"
        val_w = (s.get("validate_warning") or 0)
        if val_w > 0:
            reasons.append(f"validate: {val_w} warning(s)"); tier = "high"
        upg_w = (s.get("upgrade_warning") or 0)
        if upg_w > 0:
            reasons.append(f"upgrade: {upg_w} warning(s)"); tier = "high"
        if s.get("security_superuser"):
            reasons.append("security: is_superuser detected"); tier = "high"
        if s.get("security_has_warnings"):
            reasons.append("security: _warnings present"); tier = "high"
        if s.get("touches_controller"):
            reasons.append("manifest: change touches a public controller"); tier = "high"
        if s.get("touches_security"):
            reasons.append("manifest: change touches security (ACL/rules)"); tier = "high"

    if not reasons:
        reasons.append("no elevated signals detected")

    return {"tier": tier, "reasons": reasons}


def gate_decision(evidence, risk, req_evidence=None, has_parse_errors=False):
    """Determine the deployment gate outcome.

    Returns ``{"decision","required_approvals","blocking_findings","missing_evidence"}``.

    block        — any blocking finding (validate/upgrade blocking > 0, a trace
                   error, or a secret found).
    needs_human  — risk tier critical/high, OR required evidence absent, OR an
                   artifact was present-but-unparseable (can't trust it), OR a
                   human sign-off is required (non-empty required_approvals).
    approve      — normal risk + all required evidence present + parseable + clean.

    ``req_evidence`` is the dynamic required set (defaults to the core set when a
    manifest is not available).
    """
    s = evidence.get("signals", {})
    missing_set = set(evidence.get("missing", []))
    req = set(req_evidence) if req_evidence is not None else set(_CORE_REQUIRED)

    blocking_findings = []
    val_b = (s.get("validate_blocking") or 0)
    if val_b > 0:
        blocking_findings.append(f"validate: {val_b} blocking finding(s) must be fixed")
    upg_b = (s.get("upgrade_blocking") or 0)
    if upg_b > 0:
        blocking_findings.append(f"upgrade: {upg_b} blocking issue(s) must be resolved")
    # trace_error: defense-in-depth — don't rely solely on _artifact_valid upstream.
    # None → no error; a real string → block; any present-but-empty/odd value is an
    # anomaly we can't trust → block (never silently approve on a falsy non-None).
    te = s.get("trace_error")
    if isinstance(te, str) and te.strip():
        blocking_findings.append(f"trace: error recorded → {te}")
    elif te is not None:
        blocking_findings.append("trace: malformed/empty error field — cannot trust the trace")
    if (s.get("scan_secrets_count") or 0) > 0:
        blocking_findings.append(
            f"scan-secrets: {s['scan_secrets_count']} potential secret(s) — must not ship")

    missing_evidence = sorted(req & missing_set)

    # Compute required approvals FIRST: a change needing a human sign-off can
    # never be 'approve' (a consumer reading `decision` would skip the sign-off).
    required_approvals = []
    if risk.get("tier") == "critical":
        required_approvals.append("senior Odoo dev sign-off")
    if s.get("sensitive_model"):
        required_approvals.append("finance/ops owner sign-off")
    if s.get("touches_controller"):
        required_approvals.append("security review — public controller change")
    if s.get("touches_security"):
        required_approvals.append("access-control review — ACL/record-rule change")
    # de-dup, preserve order
    required_approvals = list(dict.fromkeys(required_approvals))

    if blocking_findings:
        decision = "block"
    elif (risk.get("tier") in ("critical", "high") or missing_evidence
          or has_parse_errors or required_approvals):
        decision = "needs_human"
    else:
        decision = "approve"

    return {
        "decision": decision,
        "required_approvals": required_approvals,
        "blocking_findings": blocking_findings,
        "missing_evidence": missing_evidence,
    }


def _find_artifacts(bundle_path, name):
    """Resolve ALL files for a canonical artifact: exact <name>.json plus every
    *.<alias>.json. Returns a de-duplicated list of Paths (possibly empty).
    Multiple matches are NOT collapsed silently — build_report merges the worst
    and flags the ambiguity, so one clean file can't hide a blocking one."""
    paths, seen = [], set()
    exact = bundle_path / f"{name}.json"
    if exact.exists():
        paths.append(exact)
        seen.add(exact)
    for alias in _ARTIFACT_PATTERNS.get(name, [name]):
        for p in sorted(bundle_path.glob(f"*.{alias}.json")):
            if p not in seen:
                seen.add(p)
                paths.append(p)
    return paths


def _artifact_valid(name, d):
    """True if *d* has the minimum well-typed shape for its kind. A parseable but
    empty/mis-typed required artifact must NOT satisfy evidence (it would let a
    blank {} approve) or crash the int comparisons — invalid → treated as absent.
    `bool` is rejected for int fields (bool is an int subclass in Python)."""
    def _int(x):
        return isinstance(x, int) and not isinstance(x, bool)
    if not isinstance(d, dict):
        return False
    if name in ("validate", "upgrade"):
        s = d.get("summary")
        if not isinstance(s, dict):
            return False
        b, w = s.get("blocking"), s.get("warning")
        # both counts explicit, non-bool ints, and non-negative (a -1 blocking is
        # attacker-shaped: it would clear the >0 blocking signal and approve)
        return _int(b) and _int(w) and b >= 0 and w >= 0
    if name == "scan_secrets":
        return _int(d.get("count")) and d["count"] >= 0
    if name == "scenarios":
        risk = d.get("risk")
        if not (isinstance(risk, dict) and risk.get("tier") in ("critical", "high", "normal")):
            return False
        # model hints feed sensitive-model detection — a present non-string (model:[])
        # would silently read as not-sensitive → must be a string when present
        return all(m is None or isinstance(m, str) for m in (risk.get("model"), d.get("model")))
    if name == "env_diff":
        s = d.get("summary")
        if not isinstance(s, dict):
            return False
        sev = s.get("severity")
        return sev is None or sev in ("none", "low", "high")
    if name == "native_check":
        # at least one known key present, and EVERY present known key is a list —
        # a wrong-typed sibling (e.g. considered:true) must not pass on the other's
        # back. (the real signal is a list; a string/bool is not evidence)
        present_keys = [k for k in ("confirmed_candidates", "considered") if k in d]
        return bool(present_keys) and all(isinstance(d.get(k), list) for k in present_keys)
    if name == "security":
        # the gate reads these by truthiness, so a wrong-typed value (is_superuser:[]
        # → falsey) would hide a real superuser/warnings → must be the right type
        if "is_superuser" in d and not isinstance(d["is_superuser"], bool):
            return False
        if "_warnings" in d and not isinstance(d["_warnings"], list):
            return False
        # model hints feed sensitive detection — string when present
        return all(d[k] is None or isinstance(d[k], str)
                   for k in ("model", "model_name") if k in d)
    if name == "trace":
        # error is read by truthiness → null or a NON-empty string ("" would read
        # falsey and approve despite a present error field)
        err = d.get("error")
        return err is None or (isinstance(err, str) and err.strip() != "")
    return True


def _worst_artifact(name, dicts):
    """Merge multiple matching artifacts into the WORST case so a clean file can
    never hide a blocking one. Blocking-bearing artifacts → max blocking; the
    secret scan → max count; otherwise the first."""
    if len(dicts) == 1:
        return dicts[0]
    if name in ("validate", "upgrade"):
        return max(dicts, key=lambda d: _dig(d, "summary", "blocking") or 0)
    if name == "scan_secrets":
        return max(dicts, key=lambda d: d.get("count") or 0)
    return dicts[0]


def build_report(bundle_dir):
    """Read artifact files from *bundle_dir*, classify risk, return a gate report.

    Returns ``{"bundle_dir","evidence","risk","decision","required_evidence",
    "_warnings","_caveat"}``.
    """
    bundle_path = Path(bundle_dir)
    warnings = []
    artifacts = {}

    for name in _ARTIFACT_PATTERNS:
        valid = []
        for fp in _find_artifacts(bundle_path, name):
            try:
                data = _loads_strict(fp.read_text())
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{fp.name}: parse error — {type(exc).__name__}: {exc}")
                continue
            if not _artifact_valid(name, data):
                warnings.append(f"{fp.name}: invalid {name} schema — treated as missing")
                continue
            valid.append(data)
        if not valid:
            artifacts[name] = None
        elif len(valid) == 1:
            artifacts[name] = valid[0]
        else:
            # Ambiguity is a parse-level warning → blocks 'approve' (needs_human),
            # and we still surface the WORST so a blocker can't be hidden. All
            # entries are schema-valid here, so _worst_artifact can't crash.
            warnings.append(f"{name}: {len(valid)} files matched — using the worst-case; "
                            "give the bundle one canonical artifact per gate")
            artifacts[name] = _worst_artifact(name, valid)

    manifest = None
    mfp = bundle_path / "manifest.json"
    if mfp.exists():
        try:
            manifest = _loads_strict(mfp.read_text())
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"manifest.json: parse error — {type(exc).__name__}: {exc}")
    # A malformed manifest must not silently under-require evidence (a string
    # changed_files char-iterated, a non-bool has_migration coerced to False).
    # Any shape problem warns (→ needs_human) rather than being trusted.
    warnings.extend(manifest_warnings(manifest))
    if manifest is not None and not isinstance(manifest, dict):
        manifest = None

    flags = manifest_flags(manifest)
    req = required_evidence(flags)
    evidence = assemble_evidence(artifacts, manifest)
    risk     = classify_change_risk(evidence)
    decision = gate_decision(evidence, risk, req_evidence=req, has_parse_errors=bool(warnings))

    return {
        "bundle_dir": str(bundle_dir),
        "evidence":   evidence,
        "risk":       risk,
        "decision":   decision,
        "required_evidence": sorted(req),
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
    # allow_nan=False: never emit NaN/Infinity (not valid JSON) into the decision.
    print(json.dumps(report, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
