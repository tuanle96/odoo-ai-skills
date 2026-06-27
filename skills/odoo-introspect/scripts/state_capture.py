"""
Odoo runtime state capture (Layer F) — run INSIDE `odoo-bin shell`.

`trace_flow.py` (Layer D) tells you WHICH addon methods run and in what order,
plus SQL per call. It does NOT tell you the VALUES: the arguments, the locals,
the `self` recordset contents, or the call stack with each frame's locals when
something raises. That's the gap an IDE debugger fills (inspect variables, watch,
call stack). This script fills it in the suite's own idiom — non-interactive,
deterministic, JSON between sentinels — so an agent can read runtime state the
same way it reads the other layers, without sitting at a `(Pdb)` prompt.

It does two things, independently or together:

  1. Breakpoint snapshot — when execution enters a target `model.method` (or hits
     a target source line inside addon code), capture that frame's args + locals
     + a safe summary of `self` (and, on request, named field values).
  2. Exception post-mortem — if the method raises, walk the traceback and capture
     EVERY addon frame's locals. A normal traceback throws this away; here you
     keep the full stack-with-state that explains the failure.

It reuses Layer D's approach: `sys.settrace` filtered to `odoo.addons.*` frames
(by module name, not by a `/addons/` path), and a
SAVEPOINT that rolls back by default so nothing persists.

⚠️  This EXECUTES the method. Run on a dev/staging DB, on a throwaway record.
    The SAVEPOINT rollback only undoes DB changes — NOT emails, webhooks, HTTP/API
    calls, or queued jobs the method already fired.
⚠️  COMMIT=1 RELEASEs the savepoint and calls `env.cr.commit()` — effects PERSIST.

The env-dependent work lives in run(); the pure helpers (truncate, should_break,
serialize_value, summarize_recordset, …) are module-level so they are
importable/unit-testable without Odoo. run() executes only when `env` is present
(i.e. inside `odoo-bin shell`).

Usage
-----
    # break when execution enters sale.order.action_confirm; dump its state:
    MODEL=sale.order RECORD_ID=42 METHOD=action_confirm \
        BREAK_AT=sale.order.action_confirm \
        odoo-bin shell -d <DB> --no-http < state_capture.py

    # also read a few fields off `self` at the breakpoint:
    ... BREAK_AT=sale.order._action_confirm FIELDS=state,partner_id,amount_total ...

    # break at a specific source line inside the addon method:
    ... BREAK_AT=sale.order.action_confirm BREAK_LINE=315 ...

    # no breakpoint — just capture the full stack-with-locals if it raises:
    MODEL=sale.order RECORD_ID=42 METHOD=action_confirm ON_EXCEPTION=1 ...

    MAX_HITS=3 MAX_DEPTH=20 MAX_STRING=200 MAX_RECORDS=10 OUT=/tmp/state.json ...

    # redaction is ON by default (password/token/secret/... → "<redacted>");
    # add keys, or turn it off on a trusted dev box:
    ... REDACT_EXTRA=ssn,iban ...        # extend the default key set
    ... NO_REDACT=1 ...                  # disable redaction entirely

Output: pure JSON wrapped in ===ODOO_STATE_START=== / ===ODOO_STATE_END===.
"""
import os
import re
import sys
import json

# Identify addon frames by MODULE NAME (`odoo.addons.<module>...`), NOT by the
# source file path. A path filter like "/addons/" misses custom/enterprise
# addons mounted at e.g. /mnt/extra-addons/<repo>/<module>/ (no "/addons/" in
# the path), while the module name is "odoo.addons.<module>" for *every* addon
# regardless of where it lives on disk. This also naturally excludes core ORM
# plumbing (odoo.models / odoo.api / odoo.fields → not under odoo.addons.*).
ADDON_MOD_RE = re.compile(r"^odoo\.addons\.([^.]+)")

# Type names treated as scalars by repr (avoid importing datetime/decimal here).
_STR_LIKE_TYPES = {"datetime", "date", "time", "timedelta", "Decimal", "UUID"}

# Default-on redaction. Layer F dumps args/locals/self field values, so a local
# named `password` or a vals dict carrying `access_token` would otherwise land
# in the JSON. Keys are matched case-insensitively as substrings, so `password`
# also catches `db_password` / `smtp_password`, and `token` catches `csrf_token`.
# Bias is toward over-redaction; disable with NO_REDACT=1 on a trusted dev box.
DEFAULT_REDACT_KEYS = frozenset({
    "password", "passwd", "pwd", "secret", "secret_key", "client_secret",
    "token", "access_token", "refresh_token", "security_token", "csrf_token",
    "api_key", "apikey", "authorization", "auth", "cookie", "session",
    "private_key", "passphrase", "credential", "credentials", "otp", "totp",
})
REDACTED = "<redacted>"


def is_sensitive_key(name, redact_keys):
    """True if a var/field/dict-key name should be redacted.

    Case-insensitive substring match against `redact_keys` (a set). Empty/falsey
    `redact_keys` disables redaction entirely (NO_REDACT path).
    """
    if not name or not redact_keys:
        return False
    low = str(name).lower()
    return any(k in low for k in redact_keys)


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def addon_from_module(modname):
    """Return the addon name for an `odoo.addons.<addon>...` module, else None.

    Frame-level addon detection: use frame.f_globals['__name__']. Robust to
    custom/enterprise addons that don't sit under a literal /addons/ path.
    """
    if not modname:
        return None
    m = ADDON_MOD_RE.match(modname)
    return m.group(1) if m else None


def truncate(s, max_len=200):
    """Stringify and cap length, marking truncation."""
    if not isinstance(s, str):
        s = str(s)
    if max_len and len(s) > max_len:
        return s[:max_len] + f"…(+{len(s) - max_len} chars)"
    return s


def should_break(frame_model, frame_method, break_at):
    """Does this (model, method) frame match the BREAK_AT spec?

    BREAK_AT forms:
      - "sale.order.action_confirm"  -> match model AND method
      - "action_confirm"             -> match method only (any model)
      - "sale.order.*"               -> match model only (any method)
    """
    if not break_at:
        return False
    spec = break_at.strip()
    if "." not in spec:                      # bare method name
        return frame_method == spec
    model_part, _, method_part = spec.rpartition(".")
    if method_part == "*":                   # model-only wildcard
        return frame_model == model_part
    if not model_part:                       # ".method" → method only
        return frame_method == method_part
    return frame_model == model_part and frame_method == method_part


def is_recordset(v):
    """Duck-type an Odoo recordset without importing Odoo."""
    return (
        isinstance(getattr(v, "_name", None), str)
        and hasattr(v, "ids")
        and hasattr(v, "browse")
    )


def summarize_recordset(rs, max_records=10):
    """Compact, query-cheap summary of a recordset: model + ids + length."""
    try:
        ids = list(rs.ids)
    except Exception:  # noqa: BLE001
        ids = []
    return {
        "__recordset__": getattr(rs, "_name", None),
        "len": len(ids),
        "ids": ids[:max_records],
        "truncated": len(ids) > max_records,
    }


def serialize_value(v, depth=0, max_depth=3, max_string=200, max_records=10, max_items=50,
                    redact_keys=None):
    """JSON-safe, bounded representation of an arbitrary runtime value.

    Recordsets become a model+ids summary (no field reads → no surprise queries
    or recursion). Containers recurse with depth/element caps. Dict values whose
    key looks sensitive (see `is_sensitive_key`) are replaced with `<redacted>`.
    Everything else falls back to a guarded, truncated repr. Never raises.
    """
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return truncate(v, max_string)
    if isinstance(v, bytes):
        return f"<bytes len={len(v)}>"
    if is_recordset(v):
        return summarize_recordset(v, max_records)
    if type(v).__name__ in _STR_LIKE_TYPES:
        return truncate(repr(v), max_string)
    if depth >= max_depth:
        return truncate(f"<{type(v).__name__}>", max_string)
    try:
        if isinstance(v, dict):
            out, extra = {}, 0
            for i, (k, val) in enumerate(v.items()):
                if i >= max_items:
                    extra = len(v) - max_items
                    break
                key = truncate(str(k), 80)
                if is_sensitive_key(k, redact_keys):
                    out[key] = REDACTED
                else:
                    out[key] = serialize_value(
                        val, depth + 1, max_depth, max_string, max_records, max_items,
                        redact_keys)
            if extra:
                out["__truncated__"] = f"+{extra} more keys"
            return out
        if isinstance(v, (list, tuple, set, frozenset)):
            seq = list(v)
            out = [serialize_value(x, depth + 1, max_depth, max_string, max_records, max_items,
                                   redact_keys)
                   for x in seq[:max_items]]
            if len(seq) > max_items:
                out.append(f"…(+{len(seq) - max_items} more items)")
            return out
    except Exception as e:  # noqa: BLE001
        return f"<unserializable {type(v).__name__}: {type(e).__name__}>"
    try:
        return truncate(repr(v), max_string)
    except Exception:  # noqa: BLE001
        return f"<unreprable {type(v).__name__}>"


def serialize_locals(frame_locals, max_string=200, max_records=10, max_locals=40,
                     redact_keys=None):
    """Serialize a frame's locals dict, capping count and hiding obvious junk.

    A local whose name looks sensitive is replaced with `<redacted>` before its
    value is ever serialized.
    """
    out, count = {}, 0
    for name, val in frame_locals.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        if count >= max_locals:
            out["__truncated__"] = f"+{len(frame_locals) - count} more locals"
            break
        if is_sensitive_key(name, redact_keys):
            out[name] = REDACTED
        else:
            out[name] = serialize_value(val, max_string=max_string, max_records=max_records,
                                        redact_keys=redact_keys)
        count += 1
    return out


# --- env-dependent work (only inside odoo-bin shell) -------------------------
def run():
    MODEL = os.environ.get("MODEL")
    RECORD_ID = os.environ.get("RECORD_ID")
    METHOD = os.environ.get("METHOD")
    if not (MODEL and RECORD_ID and METHOD):
        raise SystemExit("Set MODEL, RECORD_ID and METHOD")

    RECORD_ID = int(RECORD_ID)
    BREAK_AT = os.environ.get("BREAK_AT") or ""
    BREAK_LINE = os.environ.get("BREAK_LINE")
    BREAK_LINE = int(BREAK_LINE) if BREAK_LINE else None
    FIELDS = [f.strip() for f in (os.environ.get("FIELDS") or "").split(",") if f.strip()]
    # default: if no breakpoint requested, capture the stack on exception.
    ON_EXCEPTION = os.environ.get("ON_EXCEPTION")
    ON_EXCEPTION = (ON_EXCEPTION in ("1", "true", "yes")) if ON_EXCEPTION is not None else (not BREAK_AT)
    MAX_HITS = int(os.environ.get("MAX_HITS", "3"))
    MAX_DEPTH = int(os.environ.get("MAX_DEPTH", "20"))
    MAX_STRING = int(os.environ.get("MAX_STRING", "200"))
    MAX_RECORDS = int(os.environ.get("MAX_RECORDS", "10"))
    COMMIT = os.environ.get("COMMIT") in ("1", "true", "yes")
    OUT = os.environ.get("OUT")

    # Redaction (default ON). NO_REDACT=1 disables it; REDACT_EXTRA=a,b adds keys.
    NO_REDACT = os.environ.get("NO_REDACT") in ("1", "true", "yes")
    REDACT_EXTRA = [k.strip().lower()
                    for k in (os.environ.get("REDACT_EXTRA") or "").split(",") if k.strip()]
    REDACT = frozenset() if NO_REDACT else (DEFAULT_REDACT_KEYS | set(REDACT_EXTRA))

    record = env[MODEL].browse(RECORD_ID)        # noqa: F821
    if not record.exists():
        raise SystemExit(f"{MODEL},{RECORD_ID} does not exist")

    snapshots = []
    depth = {"n": 0}

    def _snapshot(frame, kind, addon, model_name):
        snap = {
            "hit": len(snapshots) + 1,
            "kind": kind,                         # "call" | "line"
            "addon": addon,
            "model": model_name,
            "method": frame.f_code.co_name,
            "file_line": frame.f_lineno,
            "depth": depth["n"],
            "locals": serialize_locals(frame.f_locals, MAX_STRING, MAX_RECORDS, redact_keys=REDACT),
        }
        self_obj = frame.f_locals.get("self")
        if is_recordset(self_obj):
            snap["self"] = summarize_recordset(self_obj, MAX_RECORDS)
            if FIELDS and len(self_obj.ids) <= MAX_RECORDS:
                vals = {}
                for f in FIELDS:
                    if is_sensitive_key(f, REDACT):
                        vals[f] = REDACTED
                        continue
                    try:
                        vals[f] = [serialize_value(rec[f], max_string=MAX_STRING,
                                                   max_records=MAX_RECORDS, redact_keys=REDACT)
                                   for rec in self_obj]
                    except Exception as e:        # noqa: BLE001
                        vals[f] = f"<unreadable: {type(e).__name__}>"
                snap["self_fields"] = vals
        snapshots.append(snap)

    def tracer(frame, event, arg):
        code = frame.f_code
        addon = addon_from_module(frame.f_globals.get("__name__"))
        if not addon or code.co_name.startswith("<"):
            return tracer
        if event == "call":
            self_obj = frame.f_locals.get("self")
            model_name = getattr(self_obj, "_name", None) if self_obj is not None else None
            if depth["n"] <= MAX_DEPTH and len(snapshots) < MAX_HITS \
                    and BREAK_AT and BREAK_LINE is None \
                    and should_break(model_name, code.co_name, BREAK_AT):
                _snapshot(frame, "call", addon, model_name)
            depth["n"] += 1
            return tracer
        if event == "line" and BREAK_LINE is not None and frame.f_lineno == BREAK_LINE \
                and len(snapshots) < MAX_HITS:
            self_obj = frame.f_locals.get("self")
            model_name = getattr(self_obj, "_name", None) if self_obj is not None else None
            if not BREAK_AT or should_break(model_name, code.co_name, BREAK_AT):
                _snapshot(frame, "line", addon, model_name)
        elif event == "return":
            depth["n"] = max(0, depth["n"] - 1)
        return tracer

    error = None
    exc_stack = []
    cr = env.cr                                  # noqa: F821
    cr.execute("SAVEPOINT odoo_state")
    use_trace = bool(BREAK_AT) or BREAK_LINE is not None
    if use_trace:
        sys.settrace(tracer)
    try:
        getattr(record, METHOD)()
    except Exception as e:                       # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        if ON_EXCEPTION:
            tb = sys.exc_info()[2]
            while tb is not None:
                fr = tb.tb_frame
                addon = addon_from_module(fr.f_globals.get("__name__"))
                if addon and not fr.f_code.co_name.startswith("<"):
                    self_obj = fr.f_locals.get("self")
                    exc_stack.append({
                        "addon": addon,
                        "model": getattr(self_obj, "_name", None) if self_obj is not None else None,
                        "method": fr.f_code.co_name,
                        "file_line": tb.tb_lineno,
                        "self": summarize_recordset(self_obj, MAX_RECORDS) if is_recordset(self_obj) else None,
                        "locals": serialize_locals(fr.f_locals, MAX_STRING, MAX_RECORDS, redact_keys=REDACT),
                    })
                tb = tb.tb_next
    finally:
        if use_trace:
            sys.settrace(None)
        if COMMIT:
            cr.execute("RELEASE SAVEPOINT odoo_state")
            env.cr.commit()                      # real persist — throwaway/dev DB only  # noqa: F821
        else:
            cr.execute("ROLLBACK TO SAVEPOINT odoo_state")

    result = {
        "root": f"{MODEL}({RECORD_ID}).{METHOD}",
        "committed": COMMIT,
        "break_at": BREAK_AT or None,
        "break_line": BREAK_LINE,
        "captured_fields": FIELDS,
        "redaction": {
            "enabled": not NO_REDACT,
            "key_count": len(REDACT),
            "extra": REDACT_EXTRA,
        },
        "error": error,
        "breakpoint_hits": len(snapshots),
        "breakpoints": snapshots,
        "exception_stack": exc_stack,            # deepest-last, only addon frames
        "_caveat": "Values are bounded snapshots (recordsets → ids only unless FIELDS given). "
                   "Reading FIELDS executes computes/queries; breakpoints fire only in addon "
                   "(odoo.addons.*) frames, not core ORM plumbing.",
    }

    payload = json.dumps(result, indent=2, default=str)
    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}  ({len(snapshots)} snapshots, exc_frames={len(exc_stack)})")
    else:
        print("===ODOO_STATE_START===")
        print(payload)
        print("===ODOO_STATE_END===")


# `env` is injected by `odoo-bin shell`; its presence means we're running for
# real. Absent (e.g. an import in a unit test) → run() is skipped and only the
# pure helpers above are exposed.
if "env" in globals():
    run()
