"""
Evidence provenance / attestation — HMAC-signs a CI-produced evidence artifact
(e.g. ``changed_coverage.json``) inside a small JSON envelope, so a deploy gate
can tell CI-produced evidence apart from JSON an agent (or the code under
test) forged by hand. No Odoo connection required.

A plain sha256 of the artifact is NOT enough: anyone can recompute a hash over
forged content. The envelope's "signature" is instead an HMAC-SHA256 keyed by
a shared secret, so only the holder of the key can produce a signature that
``verify_envelope`` accepts.

KEY HANDLING — SECURITY CRITICAL: the signing key (``ODOO_AI_ATTEST_KEY``)
must live ONLY on the trusted CI host that produces evidence. It must NEVER be
exported inside the Odoo test container that runs the (possibly untrusted)
code under test — a container able to read this env var could sign its own
forged evidence and defeat the whole point of attestation.

Usage
-----
    # On the CI host, right after producing an artifact:
    ODOO_AI_ATTEST_KEY=<secret> python3 provenance.py attest changed_coverage.json \\
        --name changed_coverage --base-sha <sha> --head-sha <sha> --tree-sha <sha> \\
        --runner github-actions --odoo-version 18.0 > changed_coverage.envelope.json

    # Anywhere the same key is available (e.g. the deploy gate host):
    ODOO_AI_ATTEST_KEY=<secret> python3 provenance.py verify \\
        changed_coverage.envelope.json --artifact changed_coverage.json

Output: pure JSON to stdout. Exit code is always 0 so the JSON output is never
suppressed by a non-zero return.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys

SCHEMA = "odoo-ai-evidence/v1"


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo, unit-testable)
# ---------------------------------------------------------------------------

def sha256_hex(data):
    """Return "sha256:<hexdigest>" for *data* (bytes, or str encoded as utf-8)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_bytes(envelope):
    """Deterministic JSON serialization of *envelope* MINUS "signature" — the
    exact bytes that get signed and later re-verified. Uses sort_keys=True so
    the result is identical regardless of the order keys were inserted in the
    source dict, and a fixed separator/ensure_ascii so re-running never drifts.
    """
    subject = {k: v for k, v in envelope.items() if k != "signature"}
    return json.dumps(subject, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")


def sign_envelope(envelope, key):
    """Return a COPY of *envelope* with "signature" set to the hex HMAC-SHA256
    of canonical_bytes(envelope without "signature"), keyed by *key*.

    HMAC (not a bare hash) is required: a bare sha256 lets anyone recompute a
    valid digest over forged content. Only the holder of *key* can produce a
    signature that verify_envelope will accept.
    """
    env = dict(envelope)
    env.pop("signature", None)
    mac = hmac.new(key, canonical_bytes(env), hashlib.sha256).hexdigest()
    env["signature"] = mac
    return env


def _is_hex(s):
    if not isinstance(s, str) or not s:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def _is_well_formed_sha256(s):
    """True iff *s* looks like "sha256:<64 hex chars>"."""
    if not isinstance(s, str) or not s.startswith("sha256:"):
        return False
    hexpart = s[len("sha256:"):]
    return len(hexpart) == 64 and _is_hex(hexpart)


def verify_envelope(envelope, key):
    """Verify *envelope*'s shape and signature against *key*.

    Returns ``{"ok": bool, "reasons": [str]}`` — NEVER raises, even when
    *envelope* is wildly malformed (not a dict, missing fields, wrong types):
    a gate reading forged/corrupt evidence must get ``ok=False`` plus reasons,
    not a traceback that might get swallowed and treated as "no problem
    found".

    Checks: schema matches; "signature" is present and a hex string;
    artifact.sha256 is well-formed; the recomputed HMAC (constant-time
    compared via hmac.compare_digest) matches the stored signature.
    """
    if not isinstance(envelope, dict):
        return {"ok": False, "reasons": ["envelope is not a JSON object"]}

    reasons = []

    if envelope.get("schema") != SCHEMA:
        reasons.append(
            f"schema mismatch: expected {SCHEMA!r}, got {envelope.get('schema')!r}")

    sig = envelope.get("signature")
    sig_is_hex = _is_hex(sig)
    if not sig_is_hex:
        reasons.append("signature missing or not a hex string")

    artifact = envelope.get("artifact")
    art_sha = artifact.get("sha256") if isinstance(artifact, dict) else None
    if not _is_well_formed_sha256(art_sha):
        reasons.append("artifact.sha256 missing or malformed")

    if key is None:
        reasons.append(
            "no signing key available (ODOO_AI_ATTEST_KEY not set) — cannot verify signature")
    elif sig_is_hex:
        env_wo_sig = {k: v for k, v in envelope.items() if k != "signature"}
        try:
            expected = hmac.new(key, canonical_bytes(env_wo_sig), hashlib.sha256).hexdigest()
        except Exception as exc:  # noqa: BLE001 — never raise out of verify_envelope
            reasons.append(f"could not recompute signature: {type(exc).__name__}: {exc}")
        else:
            if not hmac.compare_digest(expected, sig):
                reasons.append("signature does not match recomputed HMAC")

    return {"ok": not reasons, "reasons": reasons}


def verify_artifact_bytes(envelope, artifact_bytes):
    """True iff sha256_hex(artifact_bytes) constant-time-equals
    envelope["artifact"]["sha256"]. Never raises on a malformed envelope."""
    if not isinstance(envelope, dict):
        return False
    artifact = envelope.get("artifact")
    expected = artifact.get("sha256") if isinstance(artifact, dict) else None
    if not isinstance(expected, str):
        return False
    return hmac.compare_digest(sha256_hex(artifact_bytes), expected)


def attest(artifact_name, artifact_bytes, subject, producer, environment, command, key):
    """Build and sign an evidence envelope for *artifact_bytes*.

    ``artifact.sha256`` is computed from *artifact_bytes* here (never trust a
    caller-supplied hash). Returns the SIGNED envelope dict.
    """
    envelope = {
        "schema": SCHEMA,
        "artifact_name": artifact_name,
        "subject": subject,
        "producer": producer,
        "environment": environment,
        "command": command,
        "artifact": {
            "path": artifact_name,
            "sha256": sha256_hex(artifact_bytes),
        },
    }
    return sign_envelope(envelope, key)


def load_key():
    """Read the signing key from ODOO_AI_ATTEST_KEY (utf-8 encoded). Returns
    None when unset or empty.

    SECURITY: this key must live ONLY on the trusted CI host — see the module
    docstring. Never export it inside the Odoo test container.
    """
    val = os.environ.get("ODOO_AI_ATTEST_KEY")
    if not val:
        return None
    return val.encode("utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_arg_parser():
    parser = argparse.ArgumentParser(prog="provenance.py")
    sub = parser.add_subparsers(dest="cmd")

    p_attest = sub.add_parser("attest")
    p_attest.add_argument("artifact_path")
    p_attest.add_argument("--name", default="")
    p_attest.add_argument("--base-sha", default="")
    p_attest.add_argument("--head-sha", default="")
    p_attest.add_argument("--tree-sha", default="")
    p_attest.add_argument("--runner", default="")
    p_attest.add_argument("--odoo-version", default="")

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("envelope_path")
    p_verify.add_argument("--artifact", default=None)

    return parser


def _cmd_attest(args, raw_argv):
    try:
        with open(args.artifact_path, "rb") as fh:
            artifact_bytes = fh.read()
    except OSError as exc:
        print(json.dumps({"error": f"could not read artifact: {exc}", "ok": False}))
        return

    key = load_key()
    if key is None:
        print(json.dumps({"error": "ODOO_AI_ATTEST_KEY not set", "ok": False}))
        return

    subject = {"base_sha": args.base_sha, "head_sha": args.head_sha, "tree_sha": args.tree_sha}
    producer = {"runner": args.runner, "runner_image_digest": "", "tool_digest": {}}
    environment = {
        "odoo_version": args.odoo_version,
        "database_uuid": "",
        "db_template_digest": "",
    }
    command = {
        "argv": list(raw_argv),
        "exit_code": 0,
        "stdout_sha256": "",
        "stderr_sha256": "",
    }
    name = args.name or args.artifact_path
    envelope = attest(name, artifact_bytes, subject, producer, environment, command, key)
    print(json.dumps(envelope, sort_keys=True))


def _cmd_verify(args):
    try:
        with open(args.envelope_path, "r", encoding="utf-8") as fh:
            envelope = json.loads(fh.read())
    except (OSError, ValueError) as exc:
        print(json.dumps({
            "ok": False,
            "reasons": [f"could not read/parse envelope: {exc}"],
            "artifact_match": None,
        }))
        return

    key = load_key()
    result = verify_envelope(envelope, key)

    artifact_match = None
    if args.artifact:
        try:
            with open(args.artifact, "rb") as fh:
                artifact_bytes = fh.read()
        except OSError as exc:
            artifact_match = False
        else:
            artifact_match = verify_artifact_bytes(envelope, artifact_bytes)

    print(json.dumps({
        "ok": result["ok"],
        "reasons": result["reasons"],
        "artifact_match": artifact_match,
    }))


def main(argv=None):
    """Entry point: ``provenance.py attest <artifact.json> ...`` or
    ``provenance.py verify <envelope.json> [--artifact <artifact.json>]``.

    *argv*, when given, is the argument list WITHOUT the program name (e.g.
    ``["attest", "file.json"]``) — matching argparse's own convention.
    """
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_arg_parser()
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit:
        # Never let argparse's own sys.exit escape main(): callers rely on
        # main() always returning normally with JSON already printed.
        print(json.dumps({
            "error": "invalid arguments",
            "usage": "provenance.py {attest|verify} ...",
        }))
        return

    if args.cmd == "attest":
        _cmd_attest(args, raw_argv)
    elif args.cmd == "verify":
        _cmd_verify(args)
    else:
        print(json.dumps({
            "error": "no subcommand given",
            "usage": "provenance.py {attest|verify} ...",
        }))


if __name__ == "__main__":
    main()
