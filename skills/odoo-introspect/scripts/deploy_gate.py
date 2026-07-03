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
import os
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
    # --- Layer L: un-fakeable proof artifacts (all CI-produced) --------------
    "diff_targets": ["diff_targets", "diff-targets"],
    "changed_coverage": ["changed_coverage", "changed-coverage"],
    "runtime_path": ["runtime_path", "runtime-path"],
    "test_quality": ["test_quality", "test-quality"],
    "scenario_satisfaction": ["scenario_satisfaction", "scenario-satisfaction"],
    "mutation_smoke": ["mutation_smoke", "mutation-smoke"],
    "red_green_replay": ["red_green_replay", "red-green-replay"],
    # NB: "provenance" is deliberately NOT in this generic merge loop — a bundle
    # carries one *.provenance.json envelope PER artifact, so merging them would
    # trip the ambiguity guard. Provenance is verified separately in
    # _verify_provenance and injected as a synthetic presence marker under strict.
}

# Core evidence required for ANY automatic 'approve' (absence → needs_human).
# scan_secrets is core: a missing secret scan must NOT silently approve.
_CORE_REQUIRED = frozenset({"native_check", "scenarios", "validate", "scan_secrets"})

# Layer L policy v2 ("strict") — the hardened, hostile-agent-resistant core CI
# must satisfy. OPT-IN (deploy_gate.py --strict): the legacy default above is
# unchanged so existing callers/tests keep their behaviour. Under strict, the
# agent can no longer pass with a ritual: the changed method must be bound to a
# real runtime path, its changed lines covered, its scenarios satisfied, its
# tests free of the known fakes, and every artifact CI-signed (provenance).
_CORE_REQUIRED_V2 = frozenset({
    "native_check", "scenarios", "validate", "scan_secrets",
    "diff_targets", "changed_coverage", "runtime_path",
    "test_quality", "scenario_satisfaction", "provenance",
    # mutation is the ONLY behavioral-proof backstop that catches a test which
    # runs the changed line but asserts nothing meaningful (Oracle's normal-risk
    # weak-assert gap). Required for EVERY strict auto-approve, not just high risk.
    "mutation_smoke",
})

# The pre-Layer-L artifact names. In legacy (non-strict) mode the gate reads ONLY
# these, so a bundle carrying Layer-L-named files can't perturb a legacy decision
# or the legacy present/missing output shape.
_LEGACY_ARTIFACT_NAMES = frozenset({
    "native_check", "env_diff", "scenarios", "validate",
    "security", "trace", "upgrade", "scan_secrets",
})

# Model-name fragments that flag an accounting/stock/payment/hr domain
_SENSITIVE_KEYWORDS = ("account", "stock", "payment", "hr", "payroll")

# ---------------------------------------------------------------------------
# Severity classification + remediation hints (additive, v0.14 Layer M)
# ---------------------------------------------------------------------------
# A gate finding/check "category" (the token in front of a blocking finding, a
# missing-evidence artifact name, or a required-approval label) maps to a
# severity class S0..S4 by WHAT the finding protects:
#   S4  security / record-rule / access-control / secrets / evidence-trust
#   S3  migration / upgrade / data-integrity / critical-change sign-off
#   S2  install / validation / view breakage / test-quality / coverage gaps
#   S1  documentation / advisory
#   S0  informational (reserved; nothing maps here by default)
# Unknown categories fall back to S2 (the conservative default).
SEVERITY_BY_CATEGORY = {
    # --- S4 ---------------------------------------------------------------
    "security": "S4",
    "access_control": "S4",
    "scan_secrets": "S4",
    "provenance": "S4",
    # --- S3 ---------------------------------------------------------------
    "upgrade": "S3",
    "migration": "S3",
    "data_integrity": "S3",
    "critical_signoff": "S3",
    # --- S2 ---------------------------------------------------------------
    "validate": "S2",
    "native_check": "S2",
    "env_diff": "S2",
    "scenarios": "S2",
    "diff_targets": "S2",
    "trace": "S2",
    "test_quality": "S2",
    "changed_coverage": "S2",
    "runtime_path": "S2",
    "scenario_satisfaction": "S2",
    "mutation_smoke": "S2",
    "red_green_replay": "S2",
    # --- S1 ---------------------------------------------------------------
    "doc": "S1",
    "advisory": "S1",
}

# The conservative fallbacks used when a category is not in the maps above.
_DEFAULT_SEVERITY = "S2"
_DEFAULT_REMEDIATION = (
    "Review this finding and attach the corresponding odoo-ai evidence artifact "
    "before deploy.")

# One short, actionable hint per category. Included per finding in findings_detail.
REMEDIATION_BY_CATEGORY = {
    "security": "Run odoo-ai security review and attach the security artifact; obtain an "
                "access-control reviewer sign-off for the ACL/record-rule change.",
    "access_control": "Have an access-control owner review the ACL/ir.rule change and record "
                      "it in human_signoff.json.",
    "scan_secrets": "Remove the secret from the diff, rotate it, and re-run odoo-ai "
                    "scan-secrets — a non-zero secret count must never ship.",
    "provenance": "Re-run the evidence build in CI so every artifact is HMAC-attested; a "
                  "hand-authored or tampered bundle is rejected.",
    "upgrade": "Run odoo-ai upgrade-check on the migration and resolve every blocking issue "
               "before deploy.",
    "migration": "Provide the migration scripts and an upgrade-check artifact; dry-run the "
                 "data migration on a copy first.",
    "data_integrity": "Obtain a finance/ops owner sign-off and record it in human_signoff.json "
                      "for the sensitive-model change.",
    "critical_signoff": "Obtain a senior Odoo developer sign-off for this critical-risk change.",
    "validate": "Run odoo-ai validate and fix every blocking finding (bad view/field/xml-id) "
                "before re-gating.",
    "native_check": "Run odoo-ai native-check cold to confirm no native capability was "
                    "reinvented — warm-cache evidence is rejected.",
    "env_diff": "Run odoo-ai env-diff and reconcile the high-severity environment drift.",
    "scenarios": "Run odoo-ai scenario to record the risk tier and required behaviours.",
    "diff_targets": "Run odoo-ai diff-targets so the gate knows exactly which methods changed.",
    "trace": "Re-run odoo-ai trace cold and fix the recorded runtime error — a trace error is "
             "an executable break.",
    "test_quality": "Fix the flagged tests (vacuous assert / mocked model / swallowed "
                    "exception) and re-run test-quality.",
    "changed_coverage": "Add a real test that executes the changed lines and re-run "
                        "changed-coverage — warm-cache evidence is rejected.",
    "runtime_path": "Bind the changed method through the live registry (no mock/stub) and "
                    "re-run runtime-path with a sealed observer.",
    "scenario_satisfaction": "Exercise every required scenario at runtime and attach the "
                             "scenario_satisfaction artifact.",
    "mutation_smoke": "Strengthen the assertions until no mutant survives, then re-run "
                      "mutation-smoke.",
    "red_green_replay": "Provide a red/green replay proving the test fails on base and passes "
                        "on head.",
    "doc": "Update the referenced documentation.",
    "advisory": "Review the advisory note; there is no automated blocker, but confirm intent.",
}


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
        # a bug fix must carry a red/green replay (a failing-then-passing test).
        "is_bugfix": bool(mf.get("is_bugfix")) or mf.get("change_type") == "bugfix",
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


def required_evidence(flags, strict=False):
    """The evidence set required for an 'approve', given the manifest flags.

    ``strict`` selects Layer L policy v2 (the hardened core that CI must satisfy);
    the default is the legacy core so existing callers are unaffected. Risk-tier
    dynamic requirements (mutation_smoke, trace) are added by ``build_report``
    once risk is known.
    """
    req = set(_CORE_REQUIRED_V2) if strict else set(_CORE_REQUIRED)
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
    # --- Layer L proof artifacts ---
    tq   = artifacts.get("test_quality") or {}
    rtp  = artifacts.get("runtime_path") or {}
    ccov = artifacts.get("changed_coverage") or {}
    ssat = artifacts.get("scenario_satisfaction") or {}
    dtg  = artifacts.get("diff_targets") or {}
    mut  = artifacts.get("mutation_smoke") or {}
    rgr  = artifacts.get("red_green_replay") or {}

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
        # --- Layer L: un-fakeable proof signals (only act under strict) ---
        "test_quality_blocking": _dig(tq, "summary", "blocking"),
        "runtime_path_unbound": _dig(rtp, "summary", "unbound"),
        "changed_coverage_ok": ccov.get("ok") if isinstance(ccov.get("ok"), bool) else None,
        "scenario_satisfaction_ok": ssat.get("ok") if isinstance(ssat.get("ok"), bool) else None,
        "scenario_unsatisfied": ssat.get("unsatisfied") if isinstance(ssat.get("unsatisfied"), list) else None,
        "diff_targets_count": len(dtg.get("targets")) if isinstance(dtg.get("targets"), list) else None,
        "mutation_survived": _dig(mut, "summary", "survived"),
        "red_green_ok": rgr.get("ok") if isinstance(rgr.get("ok"), bool) else None,
        # Trusted-observation boundary: the runtime_path artifact must carry the
        # CI observer's self-report proving its tracer was not disabled/incomplete.
        "observer_sealed": _dig(rtp, "producer", "sealed") if isinstance(_dig(rtp, "producer", "sealed"), bool) else None,
        "observer_integrity": _dig(rtp, "producer", "trace_integrity"),
        "observer_digest": _dig(rtp, "producer", "tool_digest"),
        "runtime_path_present": bool(rtp),
    }

    # Cross-artifact target-set consistency (H1): the changed targets that git
    # found must ALSO be the ones coverage and runtime-path actually bound — else
    # an agent submits empty/mismatched proofs that are vacuously green.
    def _ids(doc, key):
        ts = doc.get("targets")
        if not isinstance(ts, list):
            return None
        return {t.get(key) for t in ts if isinstance(t, dict) and t.get(key)}
    diff_ids = _ids(dtg, "id")
    cov_ids = _ids(ccov, "target_id")
    rt_ids = _ids(rtp, "target_id")
    signals["diff_targets_empty"] = (diff_ids is not None and len(diff_ids) == 0)
    signals["coverage_missing_targets"] = (
        sorted(diff_ids - cov_ids) if diff_ids and cov_ids is not None else None)
    signals["runtime_missing_targets"] = (
        sorted(diff_ids - rt_ids) if diff_ids and rt_ids is not None else None)

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


def gate_decision(evidence, risk, req_evidence=None, has_parse_errors=False,
                  strict=False, provenance_failures=None):
    """Determine the deployment gate outcome.

    Returns ``{"decision","required_approvals","blocking_findings","missing_evidence"}``.

    block        — any blocking finding (validate/upgrade blocking > 0, a trace
                   error, or a secret found). Under ``strict`` (Layer L policy v2)
                   also: a swallowed/mocked test (test_quality blocking), a changed
                   method not bound to a real runtime path (runtime_path unbound),
                   changed lines not covered, an unsatisfied required scenario, a
                   surviving mutant on a sensitive/high-risk change, or a forged/
                   unverifiable provenance envelope.
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

    # --- Layer L policy v2 (strict): the un-fakeable proof checks. These make
    # "high coverage but runtime breaks" a BLOCK, not a green tick. ---
    if strict:
        tqb = (s.get("test_quality_blocking") or 0)
        if tqb > 0:
            blocking_findings.append(
                f"test-quality: {tqb} fake-test finding(s) (vacuous assert / mocked "
                "model / swallowed exception) — the tests don't prove behaviour")
        rpu = (s.get("runtime_path_unbound") or 0)
        if rpu > 0:
            blocking_findings.append(
                f"runtime-path: {rpu} changed target(s) never executed through the live "
                "registry (mock / stub / wrong MRO layer / helper-only test)")
        if s.get("changed_coverage_ok") is False:
            blocking_findings.append(
                "changed-coverage: changed executable lines of a target are not covered "
                "by a real test")
        if s.get("scenario_satisfaction_ok") is False:
            unsat = s.get("scenario_unsatisfied") or []
            detail = f" ({', '.join(unsat)})" if unsat else ""
            blocking_findings.append(
                f"scenario-satisfaction: required scenario(s) not exercised at runtime{detail}")
        msv = (s.get("mutation_survived") or 0)
        if msv > 0:
            # A surviving mutant on ANY changed line means a test executed that line
            # but asserts nothing the mutation breaks — the exact "high coverage,
            # broken runtime" fake. Block regardless of risk tier.
            blocking_findings.append(
                f"mutation-smoke: {msv} mutant(s) survived — a test runs the changed "
                "code but asserts nothing the mutation breaks (vacuous behavioral proof)")
        # Cross-artifact consistency: the proofs must cover the SAME targets git
        # found, or they prove nothing about the actual change (H1).
        cov_miss = s.get("coverage_missing_targets")
        if cov_miss:
            blocking_findings.append(
                f"changed-coverage: {len(cov_miss)} changed target(s) not present in the "
                f"coverage proof ({', '.join(cov_miss[:3])}…) — proof does not cover the change")
        rt_miss = s.get("runtime_missing_targets")
        if rt_miss:
            blocking_findings.append(
                f"runtime-path: {len(rt_miss)} changed target(s) never runtime-bound "
                f"({', '.join(rt_miss[:3])}…) — proof does not cover the change")
        # Trusted-observation boundary: a runtime_path proof is only credible if the
        # CI observer SEALED it (its tracer stayed installed, hit the targets, and
        # its output wasn't test-writable). An unsealed/tampered/absent seal blocks.
        if s.get("runtime_path_present"):
            if s.get("observer_sealed") is not True or s.get("observer_integrity") != "sealed":
                integ = s.get("observer_integrity") or "absent"
                blocking_findings.append(
                    f"runtime-path: observer self-report not sealed (trace_integrity={integ}) "
                    "— the tracer was disabled/incomplete or the evidence was not produced by "
                    "the CI observer; runtime facts cannot be trusted")
            else:
                expected_probe = os.environ.get("ODOO_AI_EXPECTED_PROBE_DIGEST")
                if expected_probe and s.get("observer_digest") != expected_probe:
                    blocking_findings.append(
                        f"runtime-path: observer tool_digest {s.get('observer_digest')!r} != "
                        f"the CI-pinned probe {expected_probe!r} — untrusted/swapped observer")
        if s.get("red_green_ok") is False:
            blocking_findings.append(
                "red-green-replay: the test did not fail on the base and pass on head "
                "(faked or absent test-first proof)")
        for pf in (provenance_failures or []):
            blocking_findings.append(f"provenance: {pf}")

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

    # Escalate (never silently approve) a diff_targets that claims zero changed
    # targets — every proof would be vacuously green; a human confirms nothing
    # behavioural changed. (Surviving mutants are a hard block above.)
    soft_review = bool(strict and not blocking_findings and s.get("diff_targets_empty"))

    if blocking_findings:
        decision = "block"
    elif (risk.get("tier") in ("critical", "high") or missing_evidence
          or has_parse_errors or required_approvals or soft_review):
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
    # --- Layer L artifacts: the gate reads their summaries by truthiness / int,
    # so a present-but-mis-typed field must be rejected (treated as absent) so it
    # can't sneak a pass. ---
    if name == "test_quality":
        s = d.get("summary")
        if not isinstance(s, dict):
            return False
        b, w = s.get("blocking"), s.get("warning")
        return _int(b) and _int(w) and b >= 0 and w >= 0
    if name == "runtime_path":
        s = d.get("summary")
        if not isinstance(s, dict):
            return False
        u = s.get("unbound")
        # a real runtime-path proof carries the per-target list it bound; without
        # it the H1 target-consistency check would silently no-op → require it.
        return _int(u) and u >= 0 and isinstance(d.get("targets"), list)
    if name == "changed_coverage":
        return isinstance(d.get("ok"), bool) and isinstance(d.get("targets"), list)
    if name == "scenario_satisfaction":
        return isinstance(d.get("ok"), bool)
    if name == "diff_targets":
        # a real diff-target report carries a list of targets (may be empty)
        return isinstance(d.get("targets"), list)
    if name == "mutation_smoke":
        s = d.get("summary")
        if not isinstance(s, dict):
            return False
        sv = s.get("survived")
        return _int(sv) and sv >= 0
    if name == "red_green_replay":
        # the gate reads ok by truthiness; a mis-typed ok would hide a failed
        # replay → require a real bool.
        return isinstance(d.get("ok"), bool)
    if name == "provenance":
        # a signed envelope (or a list of them); the real signature check happens
        # in _verify_provenance — here we only assert the minimum shape.
        env = d if isinstance(d, dict) else None
        if env is None:
            return False
        return env.get("schema") == "odoo-ai-evidence/v1" and isinstance(env.get("signature"), str)
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


def _load_provenance_module():
    """Import the sibling ``provenance`` module, tolerating a direct-CWD run by
    adding this script's dir to sys.path first (L2)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import provenance as _prov  # noqa: E402
    return _prov


def _verify_provenance(bundle_path, consumed_files, required_names, expected_head=None):
    """Bind the CI attestations to the evidence the gate ACTUALLY consumes.

    The trust boundary: the signing key (``ODOO_AI_ATTEST_KEY``) lives only on the
    CI host, never in the agent's environment. For strict approval EVERY required
    artifact's *consumed file bytes* must be covered by a valid HMAC envelope —
    a hand-authored green ``validate.json`` has a sha256 CI never signed, so it is
    rejected even if some unrelated valid envelope is also present (C1), and the
    binding hashes the real file the gate read, not a path field (C2).

    ``consumed_files``  : {canonical_name: Path} the gate actually read.
    ``required_names``  : the set that must each be attested.
    ``expected_head``   : if given, every envelope's subject.head_sha must equal it
                          (defeats whole-bundle replay across commits, H2).

    Returns ``(failures, could_not_verify, checked)``. Never raises.
    """
    env_files = sorted(bundle_path.glob("*provenance*.json"))
    if not env_files:
        return [], False, 0
    try:
        _prov = _load_provenance_module()
    except Exception:  # noqa: BLE001
        return [], True, len(env_files)
    key = None
    try:
        key = _prov.load_key()
    except Exception:  # noqa: BLE001
        key = None
    if not key:
        return [], True, len(env_files)

    failures = []
    # Collect every VALID envelope as (sha256, artifact_name, head_sha). The
    # binding below is PER ARTIFACT — an envelope only vouches for a file when its
    # hash, its declared name, AND a non-empty head that matches the change all
    # line up. (A global hash set let an empty/mismatched head slip — Oracle H2.)
    valid_envs = []
    for fp in env_files:
        try:
            env = json.loads(fp.read_text())
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{fp.name}: unreadable envelope ({type(exc).__name__})")
            continue
        res = _prov.verify_envelope(env, key)
        if not res.get("ok"):
            reasons = "; ".join(res.get("reasons", []) or ["signature mismatch"])
            failures.append(f"{fp.name}: invalid attestation — {reasons}")
            continue
        valid_envs.append({
            "sha": _dig(env, "artifact", "sha256"),
            "name": env.get("artifact_name"),
            "head": _dig(env, "subject", "head_sha"),
        })

    def _name_ok(env_name, canonical):
        return env_name == canonical or env_name in _ARTIFACT_PATTERNS.get(canonical, [canonical])

    # Per-artifact binding: each required, consumed artifact must be covered by an
    # envelope that (a) hashes its real bytes, (b) names it, and (c) carries a
    # NON-EMPTY head equal to the expected head (when the CI head is known).
    for name in sorted(set(required_names)):
        if name == "provenance":
            continue
        fp = consumed_files.get(name)
        if fp is None:
            continue  # absence is handled as missing_evidence elsewhere
        try:
            h = _prov.sha256_hex(fp.read_bytes())
        except Exception:  # noqa: BLE001
            failures.append(f"{name}: could not hash consumed evidence for attestation check")
            continue
        by_hash = [e for e in valid_envs if e["sha"] == h]
        if not by_hash:
            failures.append(
                f"{name}: consumed evidence is not covered by a valid CI attestation "
                "(forged, unsigned, or tampered after signing)")
            continue
        named = [e for e in by_hash if _name_ok(e["name"], name)]
        if not named:
            failures.append(
                f"{name}: attestation does not name this artifact "
                f"(envelope names {[e['name'] for e in by_hash]}) — wrong-artifact signature")
            continue
        # head must be a non-empty string, and match the CI head when known.
        fresh = [e for e in named
                 if isinstance(e["head"], str) and e["head"]
                 and (expected_head is None or e["head"] == expected_head)]
        if not fresh:
            heads = [e["head"] for e in named]
            failures.append(
                f"{name}: attestation head_sha {heads} is empty or != the change under "
                f"gate {expected_head!r} — stale/replayed or unbound attestation")

    return failures, False, len(env_files)


def _category_from_prefix(finding):
    """Category for a blocking-finding string — the token before the first ':',
    hyphen/space-normalised (``scan-secrets: …`` → ``scan_secrets``). Unknown → the
    conservative default so a novel finding never reads as low-severity."""
    head = str(finding).split(":", 1)[0].strip().lower().replace("-", "_").replace(" ", "_")
    return head if head in SEVERITY_BY_CATEGORY else "default"


def _category_from_approval(label):
    """Category for a required-approval label (free-text, not ``prefix:``)."""
    t = str(label).lower()
    if ("controller" in t or "acl" in t or "record-rule" in t or "record_rule" in t
            or "access-control" in t or "access control" in t or "security" in t):
        return "security"          # S4
    if "finance" in t or "ops owner" in t or "payroll" in t:
        return "data_integrity"    # S3
    if "senior" in t:
        return "critical_signoff"  # S3
    return "default"


def _finding_entry(text, category):
    """Build one findings_detail element: {finding, severity, remediation}."""
    return {
        "finding":     text,
        "severity":    SEVERITY_BY_CATEGORY.get(category, _DEFAULT_SEVERITY),
        "remediation": REMEDIATION_BY_CATEGORY.get(category, _DEFAULT_REMEDIATION),
    }


def build_findings_detail(decision):
    """Structured, severity-classified view of the gate's surfaced findings.

    Parallel to (never replaces) the flat ``blocking_findings`` /
    ``missing_evidence`` / ``required_approvals`` lists — each becomes one
    ``{finding, severity, remediation}`` entry. De-duplicated on the finding text.
    """
    detail, seen = [], set()

    def add(text, category):
        if text in seen:
            return
        seen.add(text)
        detail.append(_finding_entry(text, category))

    for f in decision.get("blocking_findings", []) or []:
        add(f, _category_from_prefix(f))
    for name in decision.get("missing_evidence", []) or []:
        cat = name if name in SEVERITY_BY_CATEGORY else "default"
        add(f"missing required evidence: {name}", cat)
    for a in decision.get("required_approvals", []) or []:
        add(a, _category_from_approval(a))
    return detail


def severity_summary(findings_detail):
    """Count findings per severity class. Always returns all of S0..S4."""
    summary = {f"S{i}": 0 for i in range(5)}
    for fd in findings_detail:
        sev = fd.get("severity")
        if sev in summary:
            summary[sev] += 1
    return summary


def _load_json_file(path):
    """Strict-load a JSON file, returning None on absence/parse error (opt-in
    policy + sign-off files fail closed: an unreadable file grants nothing)."""
    try:
        return _loads_strict(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return None


def resolve_fail_closed_policy(bundle_path, policy_path=None):
    """Resolve the opt-in fail-closed policy from the first available source.

    Precedence (first present wins):
      (a) ``--policy <path>``  → {"fail_closed_severities": [...],
                                  "required_signoff_roles": [...]}
      (b) ``bundle_dir/gate_policy.json`` → same shape
      (c) env ``ODOO_AI_FAIL_CLOSED="S3,S4"`` → severities only, no roles

    Returns ``(active, severities:set, required_roles:list, source:str|None,
    warning:str|None)``. When NO source is present ``active`` is False and the
    caller's decision logic is completely unchanged.
    """
    cfg, source, warning = None, None, None

    if policy_path is not None:                          # (a)
        cfg = _load_json_file(policy_path)
        if cfg is None:
            warning = (f"gate-policy: --policy {policy_path!r} is missing or unparseable "
                       "— ignored; falling back to other policy sources")
        elif not isinstance(cfg, dict):
            warning = f"gate-policy: --policy {policy_path!r} is not a JSON object — ignored"
            cfg = None
        else:
            source = f"--policy {policy_path}"

    if cfg is None:                                      # (b)
        gp = bundle_path / "gate_policy.json"
        if gp.exists():
            loaded = _load_json_file(gp)
            if isinstance(loaded, dict):
                cfg, source = loaded, "gate_policy.json"
            else:
                warning = (warning or
                           "gate-policy: gate_policy.json is unparseable or not an object — ignored")

    if cfg is None:                                      # (c)
        env_fc = os.environ.get("ODOO_AI_FAIL_CLOSED")
        if env_fc and env_fc.strip():
            sevs = [s.strip() for s in env_fc.split(",") if s.strip()]
            cfg, source = {"fail_closed_severities": sevs, "required_signoff_roles": []}, \
                "env:ODOO_AI_FAIL_CLOSED"

    if cfg is None:
        return False, set(), [], None, warning

    sev = cfg.get("fail_closed_severities")
    sev = {s for s in sev if isinstance(s, str)} if isinstance(sev, list) else set()
    roles = cfg.get("required_signoff_roles")
    roles = [r for r in roles if isinstance(r, str)] if isinstance(roles, list) else []
    return True, sev, roles, source, warning


def _signoff_covers(bundle_path, required_roles):
    """True when ``human_signoff.json`` exists, is well-shaped, and its signoffs
    cover every required role. Fails closed: a missing/unparseable/mis-shaped file
    grants nothing. An empty ``required_roles`` is satisfied by any valid file."""
    fp = bundle_path / "human_signoff.json"
    if not fp.exists():
        return False
    doc = _load_json_file(fp)
    if not isinstance(doc, dict):
        return False
    signoffs = doc.get("signoffs")
    if not isinstance(signoffs, list):
        return False
    covered = {
        s.get("role") for s in signoffs
        if isinstance(s, dict) and isinstance(s.get("role"), str)
        and isinstance(s.get("name"), str) and s.get("name").strip()
    }
    return all(r in covered for r in required_roles)


def build_report(bundle_dir, strict=False, policy_path=None):
    """Read artifact files from *bundle_dir*, classify risk, return a gate report.

    ``strict`` selects Layer L policy v2: the hardened core (`_CORE_REQUIRED_V2`),
    the un-fakeable runtime/coverage/scenario/test-quality blocking checks, and
    provenance (HMAC-attestation) verification of the evidence bundle. The legacy
    default (``strict=False``) is unchanged.

    ``policy_path`` is the optional ``--policy`` fail-closed policy file (opt-in).
    When neither it, ``bundle_dir/gate_policy.json`` nor ``ODOO_AI_FAIL_CLOSED`` is
    present, the decision is identical to legacy behaviour.

    Returns ``{"bundle_dir","evidence","risk","decision","findings_detail",
    "severity_summary","fail_closed","required_evidence","policy","_warnings",
    "_caveat"}``. ``findings_detail``/``severity_summary``/``fail_closed`` are
    always present and purely additive; existing keys are unchanged.
    """
    bundle_path = Path(bundle_dir)
    warnings = []
    artifacts = {}
    artifact_files = {}   # canonical name -> Path of the file the gate consumed

    # Legacy reads only the pre-Layer-L names (so a Layer-L-named file can't
    # perturb a legacy decision); strict reads the full set.
    names = list(_ARTIFACT_PATTERNS) if strict else \
        [n for n in _ARTIFACT_PATTERNS if n in _LEGACY_ARTIFACT_NAMES]

    for name in names:
        valid = []   # list of (data, Path)
        for fp in _find_artifacts(bundle_path, name):
            try:
                data = _loads_strict(fp.read_text())
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{fp.name}: parse error — {type(exc).__name__}: {exc}")
                continue
            if not _artifact_valid(name, data):
                warnings.append(f"{fp.name}: invalid {name} schema — treated as missing")
                continue
            valid.append((data, fp))
        if not valid:
            artifacts[name] = None
        elif len(valid) == 1:
            artifacts[name], artifact_files[name] = valid[0]
        else:
            # Ambiguity is a parse-level warning → blocks 'approve' (needs_human),
            # and we still surface the WORST so a clean file can't hide a blocking
            # one. All entries are schema-valid here, so _worst_artifact can't crash.
            warnings.append(f"{name}: {len(valid)} files matched — using the worst-case; "
                            "give the bundle one canonical artifact per gate")
            worst = _worst_artifact(name, [d for d, _ in valid])
            artifacts[name] = worst
            artifact_files[name] = next((fp for d, fp in valid if d is worst), valid[0][1])

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
    req = required_evidence(flags, strict=strict)

    evidence = assemble_evidence(artifacts, manifest)
    risk     = classify_change_risk(evidence)

    prov_failures = []
    if strict:
        # A bug fix must prove the failing-then-passing replay (test-first).
        if flags.get("is_bugfix"):
            req.add("red_green_replay")

        # Freshness binding (H2): the head_sha of the change under gate. Sourced
        # ONLY from ODOO_AI_EXPECTED_HEAD_SHA — a CI-set channel — never from the
        # agent-authored, unsigned manifest.json (which the agent could set to a
        # replayed commit to satisfy its own check). Absent → fail-closed below.
        expected_head = os.environ.get("ODOO_AI_EXPECTED_HEAD_SHA") or None

        # Content-bind every required, consumed artifact to a valid CI attestation.
        prov_failures, prov_unverifiable, prov_checked = _verify_provenance(
            bundle_path, artifact_files, req, expected_head=expected_head)
        # Fail closed: envelopes present but no CI-set head to bind them to → we
        # cannot rule out a wholesale replay of a past signed bundle → needs_human
        # (never a silent approve).
        if prov_checked > 0 and expected_head is None and not prov_unverifiable:
            warnings.append(
                "provenance: ODOO_AI_EXPECTED_HEAD_SHA is not set — cannot confirm the "
                "bundle is for THIS commit (replay of a past signed bundle not ruled "
                "out). Set it from CI to enable strict auto-approve.")
        # provenance is "present" only when envelopes exist, verify, and every
        # required consumed artifact is content-bound (no failures).
        if prov_checked > 0 and not prov_unverifiable and not prov_failures:
            artifacts["provenance"] = {"schema": "odoo-ai-evidence/v1", "verified": True,
                                       "envelopes": prov_checked}
            evidence["present"].append("provenance")
        else:
            artifacts["provenance"] = None  # missing / unverifiable / forged
            evidence["missing"].append("provenance")
        if prov_unverifiable:
            warnings.append(
                f"provenance: {prov_checked} envelope(s) present but could not be verified "
                "(provenance.py or ODOO_AI_ATTEST_KEY unavailable) — cannot trust the bundle")
        # Fail closed on the observer identity: without a CI-pinned probe digest we
        # cannot tell a modified/swapped observer from the real one, so a sealed
        # self-report is not enough to auto-approve (Oracle observer review).
        if evidence.get("signals", {}).get("runtime_path_present") \
                and not os.environ.get("ODOO_AI_EXPECTED_PROBE_DIGEST"):
            warnings.append(
                "runtime-path: ODOO_AI_EXPECTED_PROBE_DIGEST is not set — the observer's "
                "identity is unpinned, so a modified/swapped observer cannot be detected. "
                "Pin it from CI to enable strict auto-approve.")

    decision = gate_decision(evidence, risk, req_evidence=req,
                             has_parse_errors=bool(warnings), strict=strict,
                             provenance_failures=prov_failures)

    # --- Severity-classified findings + summary (always additive) ----------
    findings_detail = build_findings_detail(decision)

    # --- Fail-closed policy (opt-in only) ----------------------------------
    # When NO policy source is present, fc_active is False and the decision is
    # left exactly as gate_decision produced it (byte-for-byte legacy behaviour).
    fc_active, fc_sev, req_roles, fc_source, fc_warning = \
        resolve_fail_closed_policy(bundle_path, policy_path)
    if fc_warning:
        warnings.append(fc_warning)
    fail_closed = {
        "active":         fc_active,
        "severities":     sorted(fc_sev),
        "signoff_present": False,
        "escalated":      False,
    }
    if fc_source:
        fail_closed["source"] = fc_source
    if req_roles:
        fail_closed["required_signoff_roles"] = list(req_roles)

    if fc_active and fc_sev:
        triggering = [fd for fd in findings_detail if fd["severity"] in fc_sev]
        if triggering:
            signoff_present = _signoff_covers(bundle_path, req_roles)
            fail_closed["signoff_present"] = signoff_present
            trig_sev = sorted({t["severity"] for t in triggering})
            if not signoff_present:
                fail_closed["escalated"] = True
                reason = (
                    f"fail-closed policy: {len(triggering)} finding(s) at severity "
                    f"{'/'.join(trig_sev)} require a human sign-off"
                    + (f" from role(s) {', '.join(req_roles)}" if req_roles else "")
                    + " that is absent — see human_signoff.json")
                decision["decision"] = "block"
                decision["blocking_findings"].append(reason)
                findings_detail.append({
                    "finding":     reason,
                    "severity":    max(trig_sev),   # S4 > S3 > … lexicographically
                    "remediation": REMEDIATION_BY_CATEGORY["critical_signoff"],
                })
            elif decision["decision"] == "approve":
                # A covering sign-off downgrades to at most needs_human — never a
                # silent approve while a fail-closed-severity finding is present.
                decision["decision"] = "needs_human"

    return {
        "bundle_dir": str(bundle_dir),
        "evidence":   evidence,
        "risk":       risk,
        "decision":   decision,
        "findings_detail": findings_detail,
        "severity_summary": severity_summary(findings_detail),
        "fail_closed": fail_closed,
        "required_evidence": sorted(req),
        "policy":     "v2-strict" if strict else "v1-legacy",
        "_warnings":  warnings,
        "_caveat":    _CAVEAT,
    }


def main(argv=None):
    """Entry point: ``deploy_gate.py [--strict] [--policy <path>] <bundle_dir>``."""
    args = (argv if argv is not None else sys.argv)[1:]
    strict = False
    policy_path = None
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--strict", "-s"):
            strict = True
        elif a == "--policy":
            i += 1
            if i < len(args):
                policy_path = args[i]
        elif a.startswith("--policy="):
            policy_path = a.split("=", 1)[1]
        else:
            positional.append(a)
        i += 1
    if not positional:
        print(json.dumps({
            "error":    "No bundle_dir supplied.",
            "usage":    "deploy_gate.py [--strict] [--policy <path>] <bundle_dir>",
            "_caveat":  _CAVEAT,
        }, indent=2))
        return
    report = build_report(positional[0], strict=strict, policy_path=policy_path)
    # allow_nan=False: never emit NaN/Infinity (not valid JSON) into the decision.
    print(json.dumps(report, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
