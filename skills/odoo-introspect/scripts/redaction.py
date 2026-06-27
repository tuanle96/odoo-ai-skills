"""
Privacy redaction for external-LLM-safe introspection output — pure local tool.

Takes introspection JSON (Layer F `state`, any `--source` dump, or arbitrary
odoo-ai output) and produces a version safe to pass to an external LLM or share
outside your environment: strips/masks PII, redacts sensitive-key values, removes
source bodies, and scans for embedded secrets.

Today, redaction inside the running scripts is key-name-only and advisory; this
module makes it enforceable as a post-processing step before the payload leaves
your environment.

Usage
-----
    # Redact a JSON file before sending to an external LLM (default):
    python3 redaction.py redact state_output.json

    # Keep source/locals intact (trusted dev box):
    python3 redaction.py redact state_output.json --mode local

    # Scan any file for embedded secrets before sharing:
    python3 redaction.py scan-secrets state_output.json

Output: pure JSON to stdout.  No Odoo dependency; no `env` / `run()` guard.
"""
import re
import sys
import json
import copy
import argparse
from pathlib import Path


# ---------------------------------------------------------------------------
# Module-level constants (public API)
# ---------------------------------------------------------------------------

SENSITIVE_MODELS = frozenset({
    "res.partner",
    "res.users",
    "account.move",
    "account.payment",
    "hr.employee",
    "hr.payslip",
    "payment.transaction",
    "mail.message",
    "ir.attachment",
})

SENSITIVE_KEY_RE = re.compile(
    r"(?i)(passw(?:or)?d|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|secret[_-]?key|client[_-]?secret|authorization|auth[_-]?token|bearer"
    r"|session|credential|private[_-]?key|signing[_-]?key|webhook|dsn"
    r"|smtp[_-]?pass)"
)

# Execution-state / source keys stripped wholesale in external mode (matched
# case-insensitively). These Layer-F shapes can carry arbitrary secrets/PII.
_STRIP_KEYS = frozenset({
    "source", "code", "locals", "local_vars", "frame_locals", "globals",
    "self", "args", "kwargs", "vals", "vals_list",
})

# ---------------------------------------------------------------------------
# PII mask patterns (applied in mask_value, most-specific first)
# ---------------------------------------------------------------------------

# JWT: three dot-separated base64url/base64 groups (>= 10 chars each)
_JWT_RE = re.compile(
    r"[A-Za-z0-9+/\-_]{10,}\.[A-Za-z0-9+/\-_]{10,}\.[A-Za-z0-9+/\-_=]{10,}"
)
# IBAN: 2 letters + 2 digits + 12-28 alphanum (ISO 13616)
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,28}\b")
# Payment card: 13-19 digits optionally grouped by space or dash
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")
# Email address
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Phone: optional + then 9+ digit groups separated by spaces/dashes/parens/dots
_PHONE_RE = re.compile(r"(?<!\d)(\+?[\d][\d\s\-\(\)\.]{7,}\d)(?!\d)")
# Pure hex token: 24+ hex chars not part of a larger alphanumeric run
_HEX_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{24,}(?![A-Za-z0-9])")
# Base64 (classical +/ or URL-safe) token: >= 24 chars; safe-replace filters prose
_B64_TOKEN_RE = re.compile(r"[A-Za-z0-9+/\-_]{24,}={0,2}")
# Provider secret tokens (specific shapes — masked before the generic token catch-all)
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_GH_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")
_STRIPE_RE = re.compile(r"\b[srp]k_(?:live|test)_[A-Za-z0-9]{16,}\b")
_SLACK_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")
_GOOGLE_API_RE = re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")
_PEM_RE = re.compile(r"-----BEGIN (?:[A-Z ]*)?PRIVATE KEY-----")

# Fields always classified as high-sensitivity regardless of model
_SENSITIVE_FIELDS = frozenset({
    "email", "phone", "mobile", "vat", "iban",
    "acc_number", "login",
})

# ---------------------------------------------------------------------------
# Secret-scan patterns (for scan_secrets — broader, never echo full value)
# ---------------------------------------------------------------------------
_SCAN_AWS_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_SCAN_JWT_RE = re.compile(
    r"[A-Za-z0-9+/\-_]{20,}\.[A-Za-z0-9+/\-_]{20,}\.[A-Za-z0-9+/\-_=]{20,}"
)
_SCAN_PK_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_SCAN_TOKEN_RE = re.compile(r"[A-Za-z0-9+/\-_]{32,}={0,2}")


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo; unit-testable)
# ---------------------------------------------------------------------------

def _safe_card_replace(m):
    """Replace only if the matched run contains >= 13 actual digits."""
    return "<card>" if len(re.sub(r"\D", "", m.group(0))) >= 13 else m.group(0)


def _safe_phone_replace(m):
    """Replace only if the matched run contains >= 9 actual digits."""
    return "<phone>" if len(re.sub(r"\D", "", m.group(0))) >= 9 else m.group(0)


def _safe_b64_replace(m):
    """Replace long tokens; skip likely-prose strings.

    Classical base64 (contains + or /) is always masked when >= 24 chars.
    URL-safe base64 / generic identifiers are masked only when they carry mixed
    case AND digits — a conservative heuristic that avoids clobbering camelCase
    variable names or purely-uppercase constants.
    """
    raw = m.group(0)
    if "+" in raw or "/" in raw:
        return "<token>"
    if re.search(r"[A-Z]", raw) and re.search(r"[a-z]", raw) and re.search(r"[0-9]", raw):
        return "<token>"
    return raw


def mask_value(value):
    """Replace PII patterns inside *value* with safe placeholders.

    Only operates on ``str``; returns non-str values unchanged.  Patterns are
    applied most-specific first so JWT/IBAN/card are caught before the generic
    token catch-all.  Conservative by design: plain prose, small integers, and
    date strings (e.g. ``"2024-01-15"``) survive unmasked.

    Masks applied:
        ``<jwt>``   – three-part base64url token (JWT shape)
        ``<iban>``  – ISO 13616 IBAN
        ``<card>``  – 13-19 digit payment card (consecutive or space/dash grouped)
        ``<email>`` – RFC-5321-ish email address
        ``<phone>`` – international/grouped phone with >= 9 digits
        ``<token>`` – long hex (>= 24 chars) or base64 token (>= 24 chars)
    """
    if not isinstance(value, str):
        return value
    s = value
    # Provider secrets first (most specific) — a static index never sees these,
    # but a Layer-F locals dump or a server-action body can.
    s = _PEM_RE.sub("<private_key>", s)
    s = _AWS_KEY_RE.sub("<aws_key>", s)
    s = _GH_TOKEN_RE.sub("<github_token>", s)
    s = _STRIPE_RE.sub("<stripe_key>", s)
    s = _SLACK_RE.sub("<slack_token>", s)
    s = _GOOGLE_API_RE.sub("<google_api_key>", s)
    s = _JWT_RE.sub("<jwt>", s)
    s = _IBAN_RE.sub("<iban>", s)
    s = _CARD_RE.sub(_safe_card_replace, s)
    s = _EMAIL_RE.sub("<email>", s)
    s = _PHONE_RE.sub(_safe_phone_replace, s)
    s = _HEX_TOKEN_RE.sub("<token>", s)
    s = _B64_TOKEN_RE.sub(_safe_b64_replace, s)
    return s


def classify_field_sensitivity(model, field):
    """Return ``"high"`` when model or field falls into a sensitive category.

    High when any of:
    - *model* is in :data:`SENSITIVE_MODELS` (partner, users, HR, payment…),
    - *field* name matches :data:`SENSITIVE_KEY_RE` (password, token, secret…),
    - *field* is in the fixed set (``email``, ``phone``, ``mobile``, ``vat``,
      ``iban``, ``acc_number``, ``login``).

    Returns ``"normal"`` otherwise.
    """
    if model in SENSITIVE_MODELS:
        return "high"
    if SENSITIVE_KEY_RE.search(field):
        return "high"
    if field in _SENSITIVE_FIELDS:
        return "high"
    return "normal"


def scan_secrets(text):
    """Scan *text* for high-risk secret patterns; return a list of hit dicts.

    Each hit: ``{"kind": str, "match_preview": str}`` where ``match_preview``
    is the first **6 chars** of the match + ``"…"``.  The full secret is never
    echoed.

    Kinds detected (kept in lock-step with mask_value's provider patterns so a
    secret the redactor would mask is also one the scanner counts):
        ``aws_key`` (AKIA/ASIA) · ``github_token`` · ``stripe_key`` ·
        ``slack_token`` · ``google_api_key`` · ``jwt`` · ``private_key_header`` ·
        ``generic_token`` (high-entropy 32+-char, mixed case + digits)
    """
    hits = []
    seen_spans = set()

    def _add(m, kind):
        span = m.span()
        if span in seen_spans:
            return
        seen_spans.add(span)
        hits.append({"kind": kind, "match_preview": m.group(0)[:6] + "…"})

    # Same compiled REs/thresholds the redactor masks with — so anything
    # mask_value would mask as a secret also increments the scan count (JWT 10+
    # per segment, hex/base64 token 24+), keeping the gate honest.
    for rx, kind in ((_AWS_KEY_RE, "aws_key"), (_GH_TOKEN_RE, "github_token"),
                     (_STRIPE_RE, "stripe_key"), (_SLACK_RE, "slack_token"),
                     (_GOOGLE_API_RE, "google_api_key"), (_PEM_RE, "private_key_header"),
                     (_JWT_RE, "jwt")):
        for m in rx.finditer(text):
            _add(m, kind)
    for m in _HEX_TOKEN_RE.finditer(text):
        _add(m, "generic_token")
    for m in _B64_TOKEN_RE.finditer(text):
        raw = m.group(0)
        if ("+" in raw or "/" in raw
                or (re.search(r"[A-Z]", raw) and re.search(r"[a-z]", raw) and re.search(r"[0-9]", raw))):
            _add(m, "generic_token")

    return hits


def redact_payload(obj, mode="external"):
    """Return a deep-copied, redacted version of *obj* (dict, list, or scalar).

    Two modes:

    ``external`` *(default — safe for external LLMs / shared channels)*
        - Keys named ``source``, ``code``, ``locals`` → ``"<stripped:external-mode>"``.
        - Any key matching :data:`SENSITIVE_KEY_RE` → ``"<redacted>"``.
        - All remaining string values are run through :func:`mask_value`.

    ``local`` *(trusted dev box)*
        - Only :data:`SENSITIVE_KEY_RE` keys are redacted.
        - ``source`` / ``code`` / ``locals`` are kept intact.
        - No PII masking on string values.
    """
    return _redact(copy.deepcopy(obj), mode)


_META_KEYS = ("model", "id", "_name")  # kept as-is even inside a sensitive record


def _redact(obj, mode, force=False):
    """Recursive worker (operates on the deep copy).

    ``force`` propagates "this is sensitive-record data" down into nested dicts
    and lists: every string under a sensitive-model record dump is redacted —
    including ``display_name``, many2one display tuples ``[id, "Name"]``, and
    nested child records that lack their own ``model`` key — because a plain
    name/address isn't caught by mask_value's PII regexes.
    """
    if isinstance(obj, dict):
        # {model, field, value} triple → redact value by model+field sensitivity.
        if (mode == "external" and "value" in obj
                and ("model" in obj or "field" in obj)
                and classify_field_sensitivity(str(obj.get("model") or ""),
                                               str(obj.get("field") or "")) == "high"):
            obj["value"] = "<redacted:sensitive-field>"
        model = obj.get("model")
        sensitive_record = (mode == "external" and isinstance(model, str)
                            and model in SENSITIVE_MODELS)
        for key in list(obj.keys()):
            lk = str(key).lower()
            if mode == "external" and lk in _STRIP_KEYS:
                obj[key] = "<stripped:external-mode>"
                continue
            if SENSITIVE_KEY_RE.search(str(key)):
                obj[key] = "<redacted>"
                continue
            if lk in _META_KEYS or lk.startswith("_"):
                continue  # keep the model name / id / dunder metadata itself
            # inside a sensitive record, force-redact every value of this field
            obj[key] = _redact(obj[key], mode, force=force or sensitive_record)
        return obj
    if isinstance(obj, list):
        return [_redact(item, mode, force) for item in obj]
    if isinstance(obj, str):
        if force:
            return "<redacted:sensitive-model-field>"
        return mask_value(obj) if mode == "external" else obj
    return obj


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_CAVEAT = (
    "Redaction is pattern-based. It will NOT catch secrets stored under benign "
    "key names, secrets in binary blobs, or secrets this tool does not parse. "
    "Always review output before sharing. external mode is the safe default for "
    "external-LLM / shared-channel use; local mode only suppresses key-value redaction."
)


def main(argv=None):
    """CLI entry point.

    Subcommands
    -----------
    redact <file.json> [--mode external|local]
        Load JSON, apply :func:`redact_payload`, print result to stdout.

    scan-secrets <file>
        Read any text file, run :func:`scan_secrets`, print
        ``{"hits":[...], "count":N, "_caveat":"..."}`` to stdout.
    """
    parser = argparse.ArgumentParser(
        prog="redaction.py",
        description="Privacy redaction for odoo-ai introspection output.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_redact = sub.add_parser("redact", help="Redact a JSON file for safe sharing.")
    p_redact.add_argument("file", help="Path to the JSON file to redact.")
    p_redact.add_argument(
        "--mode",
        choices=["external", "local"],
        default="external",
        help="external (default): strip source/code/locals + mask PII.  "
             "local: redact sensitive-key values only.",
    )

    p_scan = sub.add_parser("scan-secrets", help="Scan a file for embedded secrets.")
    p_scan.add_argument("file", help="Path to the file to scan.")

    args = parser.parse_args(argv)

    if args.cmd == "redact":
        path = Path(args.file)
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            sys.exit(f"error: file not found: {args.file}")
        except json.JSONDecodeError as exc:
            sys.exit(f"error: invalid JSON in {args.file}: {exc}")
        result = redact_payload(obj, mode=args.mode)
        if isinstance(result, dict):
            result["_caveat"] = _CAVEAT
        print(json.dumps(result, indent=2, default=str))

    elif args.cmd == "scan-secrets":
        path = Path(args.file)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            sys.exit(f"error: file not found: {args.file}")
        hits = scan_secrets(text)
        print(json.dumps({"hits": hits, "count": len(hits), "_caveat": _CAVEAT}, indent=2))


if __name__ == "__main__":
    main()
