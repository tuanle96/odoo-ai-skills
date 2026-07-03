"""
Evidence Artifact v1 — the stable, public, machine-validatable schema for an
AI-written Odoo change (local, no Odoo).

This is the thing a partner shows a client: one JSON document that says *what the
tool-chain checked, how sure it is, and whether a human still has to sign*. It is
deliberately small and versioned so downstream consumers (dashboards, PR bots,
auditors) can validate it without knowing anything about the internals.

Two entry points:
  * ``validate_artifact(obj)``  — hand-rolled (stdlib-only) schema + invariant check.
  * ``build_artifact(bundle_dir)`` — assemble a v1 artifact from a deploy-gate bundle
    (git binding + ``deploy_gate.build_report`` for checks/decision).

The strategic invariants encoded here:
  * severity classes S0..S4 (cosmetic → silent/security), each with a risk weight;
  * cache provenance cold|warm|stale-rejected, with the hard rule that a **warm
    cache read can never support an ``approve`` decision** on a gate/verify check;
  * decisions approve|needs_human|block, with an explicit human trust boundary.

Usage
-----
    python3 evidence_schema.py validate <artifact.json>
    python3 evidence_schema.py build <bundle_dir> [--out <file>]

Output: pure JSON to stdout. Exit code is always 0 (errors are reported as
``{"error": ..., "usage": ...}`` JSON so a non-zero code never hides the payload).
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"

# --- Controlled vocabularies (enums) ----------------------------------------
LAYERS = ("inspect", "verify", "gate", "report")
STATUSES = ("pass", "fail", "skip")
SEVERITIES = ("S0", "S1", "S2", "S3", "S4")
CACHE_PROVENANCE = ("cold", "warm", "stale-rejected")
DECISIONS = ("approve", "needs_human", "block")
REDACTION_MODES = ("external", "local")

# Severity classes with their risk weights (documented; also rendered in the doc).
# A change's automated risk score is the weighted sum of its failing checks.
SEVERITY_CLASSES = {
    "S0": {"weight": 1,  "meaning": "cosmetic (label/help/whitespace)"},
    "S1": {"weight": 2,  "meaning": "functional annoyance (recoverable, non-data)"},
    "S2": {"weight": 4,  "meaning": "install/upgrade breakage"},
    "S3": {"weight": 8,  "meaning": "business data corruption"},
    "S4": {"weight": 12, "meaning": "silent / security / multi-company breach"},
}

# The invariant only bites on layers whose evidence *gates a merge*. A warm
# (possibly-stale) inspect/report read is advisory; a warm gate/verify read is not
# allowed to justify an auto-approve.
_MERGE_GATING_LAYERS = ("gate", "verify")

# Top-level keys every v1 artifact must carry (instance/addons are optional).
_REQUIRED_TOP = (
    "schema_version", "generated_at", "git",
    "checks", "decision", "human_signoffs", "redaction",
)

# A documented skeleton of the v1 shape — placeholder strings describe each field's
# type/enum. Rendered verbatim in docs/evidence-artifact.md.
ARTIFACT_SPEC = {
    "schema_version": SCHEMA_VERSION,
    "generated_at": "<iso8601 string>",
    "git": {
        "commit_sha": "<str|null>",
        "diff_hash": "<str|null, optional>",
        "branch": "<str|null, optional>",
    },
    "instance": {
        "db_fingerprint": "<str, optional>",
        "odoo_version": "<str, optional>",
        "module_graph_hash": "<str, optional>",
    },
    "addons": {"addon_hash": "<str, optional>"},
    "checks": [{
        "id": "<str, required>",
        "layer": "inspect|verify|gate|report",
        "status": "pass|fail|skip",
        "severity": "S0|S1|S2|S3|S4",
        "cache_provenance": "cold|warm|stale-rejected",
        "summary": "<str>",
        "logs_path": "<str, optional>",
    }],
    "decision": {
        "decision": "approve|needs_human|block",
        "blocking_findings": ["<str>"],
        "required_approvals": ["<str>"],
    },
    "human_signoffs": [{"role": "<str>", "name": "<str, optional>", "at": "<iso8601>"}],
    "redaction": {"mode": "external|local", "scanned": False},
}

_USAGE = ("evidence_schema.py validate <artifact.json>  |  "
          "evidence_schema.py build <bundle_dir> [--out <file>]")


# ---------------------------------------------------------------------------
# Validation (hand-rolled — stdlib only, no jsonschema)
# ---------------------------------------------------------------------------

def _is_nonempty_str(x):
    return isinstance(x, str) and x != ""


def _is_opt_str(x):
    return x is None or isinstance(x, str)


def _validate_check(i, c):
    p = f"checks[{i}]"
    if not isinstance(c, dict):
        return [f"{p} must be an object"]
    e = []
    if not _is_nonempty_str(c.get("id")):
        e.append(f"{p}.id is required and must be a non-empty string")
    if c.get("layer") not in LAYERS:
        e.append(f"{p}.layer must be one of {list(LAYERS)} (got {c.get('layer')!r})")
    if c.get("status") not in STATUSES:
        e.append(f"{p}.status must be one of {list(STATUSES)} (got {c.get('status')!r})")
    if c.get("severity") not in SEVERITIES:
        e.append(f"{p}.severity must be one of {list(SEVERITIES)} (got {c.get('severity')!r})")
    if c.get("cache_provenance") not in CACHE_PROVENANCE:
        e.append(f"{p}.cache_provenance must be one of {list(CACHE_PROVENANCE)} "
                 f"(got {c.get('cache_provenance')!r})")
    if not isinstance(c.get("summary"), str):
        e.append(f"{p}.summary must be a string")
    if "logs_path" in c and not _is_opt_str(c.get("logs_path")):
        e.append(f"{p}.logs_path must be a string or null")
    return e


def _validate_decision(d):
    if not isinstance(d, dict):
        return ["decision must be an object"]
    e = []
    if d.get("decision") not in DECISIONS:
        e.append(f"decision.decision must be one of {list(DECISIONS)} (got {d.get('decision')!r})")
    for f in ("blocking_findings", "required_approvals"):
        v = d.get(f)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            e.append(f"decision.{f} must be a list of strings")
    return e


def _validate_signoff(i, s):
    p = f"human_signoffs[{i}]"
    if not isinstance(s, dict):
        return [f"{p} must be an object"]
    e = []
    if not isinstance(s.get("role"), str):
        e.append(f"{p}.role must be a string")
    if not isinstance(s.get("at"), str):
        e.append(f"{p}.at must be an ISO-8601 string")
    if "name" in s and not _is_opt_str(s.get("name")):
        e.append(f"{p}.name must be a string or null")
    return e


def validate_artifact(obj):
    """Validate an Evidence Artifact v1 object.

    Returns ``{"ok": bool, "errors": [str]}`` — ALL errors are collected (the
    check never stops at the first) so a consumer sees every problem at once.
    """
    if not isinstance(obj, dict):
        return {"ok": False, "errors": ["artifact must be a JSON object"]}

    errors = []
    for k in _REQUIRED_TOP:
        if k not in obj:
            errors.append(f"missing required key: {k}")

    if "schema_version" in obj and obj.get("schema_version") != SCHEMA_VERSION:
        errors.append(f'schema_version must be "{SCHEMA_VERSION}" '
                      f"(got {obj.get('schema_version')!r})")

    if "generated_at" in obj and not _is_nonempty_str(obj.get("generated_at")):
        errors.append("generated_at must be a non-empty ISO-8601 string")

    git = obj.get("git")
    if "git" in obj:
        if not isinstance(git, dict):
            errors.append("git must be an object")
        else:
            if "commit_sha" not in git:
                errors.append("git.commit_sha is required (may be null)")
            for f in ("commit_sha", "diff_hash", "branch"):
                if f in git and not _is_opt_str(git.get(f)):
                    errors.append(f"git.{f} must be a string or null")

    inst = obj.get("instance")
    if inst is not None:
        if not isinstance(inst, dict):
            errors.append("instance must be an object")
        else:
            for f in ("db_fingerprint", "odoo_version", "module_graph_hash"):
                if f in inst and not _is_opt_str(inst.get(f)):
                    errors.append(f"instance.{f} must be a string or null")

    add = obj.get("addons")
    if add is not None:
        if not isinstance(add, dict):
            errors.append("addons must be an object")
        elif "addon_hash" in add and not _is_opt_str(add.get("addon_hash")):
            errors.append("addons.addon_hash must be a string or null")

    checks = obj.get("checks")
    if "checks" in obj:
        if not isinstance(checks, list):
            errors.append("checks must be a list")
        else:
            for i, c in enumerate(checks):
                errors.extend(_validate_check(i, c))

    dec = obj.get("decision")
    if "decision" in obj:
        errors.extend(_validate_decision(dec))

    hs = obj.get("human_signoffs")
    if "human_signoffs" in obj:
        if not isinstance(hs, list):
            errors.append("human_signoffs must be a list")
        else:
            for i, s in enumerate(hs):
                errors.extend(_validate_signoff(i, s))

    red = obj.get("redaction")
    if "redaction" in obj:
        if not isinstance(red, dict):
            errors.append("redaction must be an object")
        else:
            if red.get("mode") not in REDACTION_MODES:
                errors.append(f"redaction.mode must be one of {list(REDACTION_MODES)} "
                              f"(got {red.get('mode')!r})")
            if not isinstance(red.get("scanned"), bool):
                errors.append("redaction.scanned must be a boolean")

    # --- THE INVARIANT: a warm cache read can never justify an approve ---
    if (isinstance(dec, dict) and dec.get("decision") == "approve"
            and isinstance(checks, list)):
        for c in checks:
            if (isinstance(c, dict) and c.get("cache_provenance") == "warm"
                    and c.get("layer") in _MERGE_GATING_LAYERS):
                errors.append("warm-cache evidence cannot support an approve decision")
                break

    return {"ok": not errors, "errors": errors}


# ---------------------------------------------------------------------------
# Assembly from a deploy-gate bundle
# ---------------------------------------------------------------------------

# Map a deploy-gate artifact name onto its public layer (Inspect/Verify/Gate/Report).
# Anything not listed defaults to "gate" (the conservative, merge-gating bucket).
_ARTIFACT_LAYER = {
    "native_check": "inspect", "env_diff": "inspect", "diff_targets": "inspect",
    "trace": "verify", "runtime_path": "verify", "changed_coverage": "verify",
    "scenario_satisfaction": "verify", "mutation_smoke": "verify",
    "red_green_replay": "verify",
    "validate": "gate", "scenarios": "gate", "security": "gate", "upgrade": "gate",
    "scan_secrets": "gate", "test_quality": "gate", "provenance": "gate",
    "evidence": "report",
}


def _load_deploy_gate():
    """Import the sibling ``deploy_gate`` module (tolerating a direct-CWD run)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import deploy_gate  # noqa: E402
    return deploy_gate


def _git(bundle_dir, *args):
    """Run ``git -C <bundle_dir> <args>``; return stripped stdout or None on any
    failure (not a repo, no git, non-zero exit)."""
    try:
        proc = subprocess.run(["git", "-C", str(bundle_dir), *args],
                              capture_output=True, text=True, check=False)
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return None


def _mk_check(name, status, summary):
    """A synthesized check row. Severity is unknown from the gate report, so it
    defaults to S2; a bundle read is a cold (fresh) read."""
    return {
        "id": name,
        "layer": _ARTIFACT_LAYER.get(name, "gate"),
        "status": status,
        "severity": "S2",
        "cache_provenance": "cold",
        "summary": summary,
    }


def _read_signoffs(bundle):
    """Copy human_signoff.json from the bundle, normalising to the v1 shape."""
    raw = _read_json(bundle / "human_signoff.json")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        so = {"role": str(e.get("role", "")), "at": str(e.get("at", ""))}
        if isinstance(e.get("name"), str):
            so["name"] = e["name"]
        out.append(so)
    return out


def _read_redaction(bundle):
    """Default {external, unscanned}; a *redact*.json in the bundle marks it scanned."""
    default = {"mode": "external", "scanned": False}
    try:
        matches = sorted(bundle.glob("*redact*.json"))
    except Exception:  # noqa: BLE001
        matches = []
    if not matches:
        return default
    doc = _read_json(matches[0]) or {}
    mode = doc.get("mode") if isinstance(doc, dict) else None
    return {"mode": mode if mode in REDACTION_MODES else "external", "scanned": True}


def build_artifact(bundle_dir, extra=None):
    """Assemble an Evidence Artifact v1 from a deploy-gate bundle directory.

    Binds the artifact to the current commit (via ``git rev-parse``, gracefully
    None off a repo), maps ``deploy_gate.build_report``'s evidence + decision into
    ``checks[]`` and ``decision`` defensively, and copies human sign-offs /
    redaction status from the bundle. ``extra`` is shallow-merged last so a caller
    can inject ``instance``/``addons`` fingerprints.
    """
    bundle = Path(bundle_dir)

    git = {
        "commit_sha": _git(bundle, "rev-parse", "HEAD"),
        "branch": _git(bundle, "rev-parse", "--abbrev-ref", "HEAD"),
    }

    report = {}
    try:
        report = _load_deploy_gate().build_report(str(bundle)) or {}
    except Exception:  # noqa: BLE001 — deploy_gate is edited concurrently; fail soft
        report = {}
    evidence = report.get("evidence") if isinstance(report.get("evidence"), dict) else {}
    present = [n for n in (evidence.get("present") or []) if isinstance(n, str)]
    missing = [n for n in (evidence.get("missing") or []) if isinstance(n, str)]
    dec_raw = report.get("decision") if isinstance(report.get("decision"), dict) else {}

    checks = [_mk_check(n, "pass", f"{n}: evidence present and parseable") for n in present]
    checks += [_mk_check(n, "skip", f"{n}: not present — not checked") for n in missing]
    blocking = [str(b) for b in (dec_raw.get("blocking_findings") or [])]
    for idx, finding in enumerate(blocking):
        checks.append({
            "id": f"blocking-{idx}", "layer": "gate", "status": "fail",
            "severity": "S2", "cache_provenance": "cold", "summary": finding,
        })

    decision_val = dec_raw.get("decision")
    if decision_val not in DECISIONS:
        decision_val = "needs_human"   # fail closed on an unknown/missing decision
    decision = {
        "decision": decision_val,
        "blocking_findings": blocking,
        "required_approvals": [str(a) for a in (dec_raw.get("required_approvals") or [])],
    }

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git": git,
        "checks": checks,
        "decision": decision,
        "human_signoffs": _read_signoffs(bundle),
        "redaction": _read_redaction(bundle),
    }
    if isinstance(extra, dict):
        artifact.update(extra)
    return artifact


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_validate(path):
    try:
        obj = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {"ok": False, "errors": [f"file not found: {path}"]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "errors": [f"could not parse {path}: {type(exc).__name__}: {exc}"]}
    return validate_artifact(obj)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(json.dumps({"error": "missing command", "usage": _USAGE}, indent=2))
        return
    cmd, rest = args[0], args[1:]

    if cmd == "validate":
        if not rest:
            print(json.dumps({"error": "validate needs <artifact.json>", "usage": _USAGE}, indent=2))
            return
        report = _cli_validate(rest[0])

    elif cmd == "build":
        out, positional, i = None, [], 0
        while i < len(rest):
            if rest[i] == "--out":
                if i + 1 >= len(rest):
                    print(json.dumps({"error": "--out needs a path", "usage": _USAGE}, indent=2))
                    return
                out, i = rest[i + 1], i + 2
            else:
                positional.append(rest[i]); i += 1
        if not positional:
            print(json.dumps({"error": "build needs <bundle_dir>", "usage": _USAGE}, indent=2))
            return
        report = build_artifact(positional[0])
        if out:
            try:
                Path(out).write_text(json.dumps(report, indent=2, default=str, allow_nan=False))
            except OSError as exc:
                report = {"error": f"could not write {out}: {exc}", "artifact": report}

    else:
        print(json.dumps({"error": f"unknown command {cmd!r}", "usage": _USAGE}, indent=2))
        return

    print(json.dumps(report, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
