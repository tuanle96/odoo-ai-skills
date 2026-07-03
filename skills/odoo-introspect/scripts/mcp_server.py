"""
Bounded, read-only MCP context server for pre-edit instance truth (local, no
Odoo import — shells out to odoo-bin).

This is the agent-facing half of the product: a small set of deterministic fact
tools an agent can call BEFORE editing, to prime itself with what the live Odoo
instance actually looks like (a model's fields, its ACLs, its views, its flows,
an instance dossier, and "does Odoo already ship this?"). It is intentionally
NOT a general RPC bridge — there is no arbitrary domain, ORM write, or code
execution. Every tool is a fixed shape with validated inputs.

Trust boundary (enforced in data, not just prose): a warm cache hit is CONTEXT
only. Every response carries `_cache.provenance` and `merge_eligible` (see
snapshot_cache.mark_provenance); merge-approval evidence must come from a COLD
gate run in CI, never from this server's warm context. Payloads are redacted
(redaction.redact_payload, external mode) before they are ever stored or returned.

Protocol: MCP over stdio — newline-delimited JSON-RPC 2.0, one JSON object per
line on stdin/stdout, compact single-line responses, stdout flushed per message.

Usage
-----
    # Serve (long-running stdio server; a client speaks JSON-RPC on its stdin):
    python3 mcp_server.py --db mydb --addons-path /path/addons

    # Register with Claude Code:
    claude mcp add odoo-context -- \
        python3 /abs/path/skills/odoo-introspect/scripts/mcp_server.py \
        --db mydb --addons-path /path/addons

    # Self-test (no DB, no serve): prints {"ok": true, "tools": [...]} and exits 0
    python3 mcp_server.py --selftest

This is a LONG-RUNNING stdio server: it blocks reading stdin line by line and
only exits on EOF. Do not expect it to return like the one-shot local tools.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent  # the skill's scripts/ dir

# Import siblings (snapshot_cache, redaction) regardless of how this file is
# loaded — as `python mcp_server.py` (dir already on path) or importlib in tests.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import redaction  # noqa: E402  (stdlib-only; hard dependency — payloads are always redacted)

try:  # cache is optional: absence must degrade gracefully to cold-only serving.
    import snapshot_cache  # noqa: E402
except ImportError:  # pragma: no cover — module ships in the same dir
    snapshot_cache = None

# Curated capability cards consumed by native_check live in the sibling
# odoo-capabilities skill; resolved relative to this file so it works in both a
# clone and the installed-plugin layout (mirrors the odoo-ai CLI).
CARDS_DIR = HERE.parent.parent / "odoo-capabilities" / "references" / "cards"
DEFAULT_LEARN_FILE = Path.home() / ".odoo-ai" / "learned.json"

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "odoo-ai-context", "version": "0.14.0"}

# Odoo model name: dotted lowercase identifier, bounded length (input hardening).
MODEL_RE = re.compile(r"^[a-z0-9_.]{1,80}$")

# Each fact script wraps its JSON payload in a pair of sentinels on stdout.
SCRIPT_SENTINELS = {
    "facts.py": ("===ODOO_FACTS_START===", "===ODOO_FACTS_END==="),
    "dossier.py": ("===ODOO_DOSSIER_START===", "===ODOO_DOSSIER_END==="),
    "native_check.py": ("===ODOO_NCHECK_START===", "===ODOO_NCHECK_END==="),
}

_MODEL_PATTERN = MODEL_RE.pattern


# --------------------------------------------------------------------------- #
# Tool catalog — names + MCP inputSchema. Small, fixed shapes only.
# --------------------------------------------------------------------------- #
def _model_schema(extra=None, required=("model",)):
    props = {"model": {"type": "string", "pattern": _MODEL_PATTERN,
                        "description": "Odoo model name, e.g. sale.order"}}
    if extra:
        props.update(extra)
    return {"type": "object", "properties": props,
            "required": list(required), "additionalProperties": False}


TOOLS = [
    {"name": "odoo_facts_model",
     "description": "Compact facts for one model: fields (compacted), inherit "
                    "chain, method names, module origins. Read-only context.",
     "inputSchema": _model_schema()},
    {"name": "odoo_facts_security",
     "description": "Access-control facts for one model: ACL rows (crwu) and "
                    "record rules (domain, multi-company). Read-only context.",
     "inputSchema": _model_schema()},
    {"name": "odoo_facts_views",
     "description": "The model's views; set arch=true to include the effective "
                    "primary-form arch. Read-only context.",
     "inputSchema": _model_schema(
         {"arch": {"type": "boolean",
                   "description": "Include effective primary-form arch (larger)."}})},
    {"name": "odoo_facts_flows",
     "description": "Flow surface for one model: form buttons, window/server "
                    "actions, automations, crons. Read-only context.",
     "inputSchema": _model_schema()},
    {"name": "odoo_dossier_summary",
     "description": "Compact instance dossier summary: meta, custom_summary, "
                    "studio_footprint counts, upgrade_risk_flags. No arguments.",
     "inputSchema": {"type": "object", "properties": {},
                     "additionalProperties": False}},
    {"name": "odoo_native_check",
     "description": "Does Odoo already ship this? Recall-match curated capability "
                    "cards against a plain-text requirement, gated against the "
                    "live instance. Read-only.",
     "inputSchema": {
         "type": "object",
         "properties": {
             "requirement": {"type": "string", "maxLength": 500,
                             "description": "Plain-text requirement, e.g. "
                                            "'auto-number delivery slips'."},
             "model": {"type": "string", "pattern": _MODEL_PATTERN,
                       "description": "Optional model for context."},
         },
         "required": ["requirement"], "additionalProperties": False}},
]
TOOL_NAMES = [t["name"] for t in TOOLS]


# --------------------------------------------------------------------------- #
# Pure helpers (no Odoo, no serving — unit-testable)
# --------------------------------------------------------------------------- #
def read_cards_blob():
    """Read every shipped capability card on the HOST into one compact JSON blob.

    native_check needs the card corpus as DATA inside the shell (odoo-bin may run
    in a different filesystem namespace), so it is read here and injected on the
    script's own stdin as CARDS_JSON. Returns a JSON string ("[]" if none)."""
    cards = []
    if CARDS_DIR.is_dir():
        for path in sorted(CARDS_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except Exception:  # noqa: BLE001 — one bad file shouldn't sink the run
                continue
            cards += data if isinstance(data, list) else data.get("cards", [])
    return json.dumps(cards, separators=(",", ":"), ensure_ascii=False)


def inject_env_preamble(values):
    """Python to PREPEND to a stdin-piped script that sets os.environ[name]=raw
    for each pair. Payload is base64'd so the preamble is pure ASCII with no
    quoting hazards — the same channel the script uses, so it crosses into a
    Docker/remote odoo-bin exactly where the script does."""
    lines = ["import os as _os, base64 as _b64"]
    for name, raw in values.items():
        b64 = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        lines.append(f"_os.environ[{name!r}] = _b64.b64decode({b64!r}).decode('utf-8')")
    return "\n".join(lines) + "\n"


def extract(stdout, start, end):
    """Take from the FIRST start marker to the LAST end marker so a payload that
    echoes the end sentinel doesn't truncate early. None if not found."""
    if start in stdout and end in stdout:
        return stdout.split(start, 1)[1].rsplit(end, 1)[0].strip()
    return None


def _footprint_counts(footprint):
    """Reduce a studio_footprint to COUNTS only — never echo the detail rows."""
    if isinstance(footprint, dict):
        out = {}
        for key, val in footprint.items():
            out[key] = len(val) if isinstance(val, (list, dict, str)) else val
        return out
    if isinstance(footprint, list):
        return {"total": len(footprint)}
    return {}


def summarize_dossier(payload):
    """Reduce a full dossier payload to the compact context subset the tool
    returns: meta, custom_summary, studio_footprint counts, upgrade_risk_flags.
    Defensive about the source schema (dossier.py owns the full shape)."""
    p = payload if isinstance(payload, dict) else {}
    return {
        "meta": p.get("meta", {}),
        "custom_summary": p.get("custom_summary", {}),
        "studio_footprint": _footprint_counts(p.get("studio_footprint")),
        "upgrade_risk_flags": p.get("upgrade_risk_flags", []),
    }


_MODEL_TOOLS = frozenset({
    "odoo_facts_model", "odoo_facts_security", "odoo_facts_views", "odoo_facts_flows",
})


def _validate_args(name, args):
    """Return an error string for invalid tool arguments, or None if valid."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    if name in _MODEL_TOOLS:
        model = args.get("model")
        if not isinstance(model, str) or not MODEL_RE.match(model):
            return f"invalid or missing 'model' (must match {_MODEL_PATTERN})"
    if name == "odoo_native_check":
        req = args.get("requirement")
        if not isinstance(req, str) or not (1 <= len(req) <= 500):
            return "invalid 'requirement' (plain text, 1-500 chars)"
        model = args.get("model")
        if model is not None and (not isinstance(model, str) or not MODEL_RE.match(model)):
            return f"invalid 'model' (must match {_MODEL_PATTERN})"
    return None


# --------------------------------------------------------------------------- #
# Handlers — each maps validated args to a raw payload dict via the runner.
# build_handlers(runner) is injectable so tests drive it with a fake runner.
# --------------------------------------------------------------------------- #
def build_handlers(runner):
    def _facts(kind):
        def handler(args):
            env = {"FACT_KIND": kind, "MODEL": args["model"]}
            if kind == "views" and args.get("arch"):
                env["ARCH"] = "1"
            return runner("facts.py", env)
        return handler

    def _dossier(_args):
        return summarize_dossier(runner("dossier.py", {}))

    def _native(args):
        env = {"REQUIREMENT": args["requirement"], "CARDS_DIR": str(CARDS_DIR)}
        if args.get("model"):
            env["MODEL"] = args["model"]
        inject = {"CARDS_JSON": read_cards_blob()}
        try:  # learned mappings are optional; a missing/unreadable file is fine
            if DEFAULT_LEARN_FILE.is_file():
                env["LEARN_FILE"] = str(DEFAULT_LEARN_FILE)
                inject["LEARNED_JSON"] = DEFAULT_LEARN_FILE.read_text()
        except OSError:
            pass
        return runner("native_check.py", env, inject_env_preamble(inject))

    return {
        "odoo_facts_model": _facts("model"),
        "odoo_facts_security": _facts("security"),
        "odoo_facts_views": _facts("views"),
        "odoo_facts_flows": _facts("flows"),
        "odoo_dossier_summary": _dossier,
        "odoo_native_check": _native,
    }


def make_real_runner(cfg):
    """The production runner: replicate the odoo-ai CLI subprocess pattern —
    `odoo-bin shell -d <db> --no-http --log-level=warn`, script piped on stdin
    (optionally prefixed by a preamble), JSON extracted between the sentinels."""
    def runner(script_name, env_extra, preamble=""):
        start, end = SCRIPT_SENTINELS[script_name]
        script_path = HERE / script_name
        cmd = [cfg["odoo_bin"], "shell", "-d", cfg["db"], "--no-http", "--log-level=warn"]
        if cfg.get("conf"):
            cmd += ["-c", cfg["conf"]]
        env = {**os.environ, "SCRIPTS_DIR": str(HERE),
               **{k: str(v) for k, v in env_extra.items()}}
        try:
            proc = subprocess.run(
                cmd, input=preamble + script_path.read_text(),
                env=env, capture_output=True, text=True, timeout=cfg["timeout"])
        except FileNotFoundError:
            raise RuntimeError(f"'{cfg['odoo_bin']}' not found (set --odoo-bin/ODOO_BIN)")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"timed out after {cfg['timeout']}s running {script_name}")
        body = extract(proc.stdout, start, end)
        if body is None:
            tail = " | ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-6:])
            raise RuntimeError(f"{script_name}: no JSON between sentinels. Last: {tail}")
        return json.loads(body)
    return runner


# --------------------------------------------------------------------------- #
# Cache configuration
# --------------------------------------------------------------------------- #
class CacheConfig:
    """Serving-side cache parameters. `enabled` and a present snapshot_cache
    module are both required for the warm path; otherwise every call runs cold."""
    def __init__(self, db, fingerprint, cache_dir=None, ttl=900, enabled=True):
        self.db = db
        self.fingerprint = fingerprint
        self.cache_dir = cache_dir
        self.ttl = ttl
        self.enabled = enabled


def _mark_cold_fallback(payload):
    """Stamp provenance when snapshot_cache is unavailable, so every response
    still carries `_cache` (mirrors mark_provenance's cold shape)."""
    out = dict(payload or {})
    out["_cache"] = {"provenance": "cold", "merge_eligible": True,
                     "_note": "snapshot_cache unavailable; provenance stamped locally"}
    return out


# --------------------------------------------------------------------------- #
# JSON-RPC framing helpers
# --------------------------------------------------------------------------- #
def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_result(text, is_error):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# --------------------------------------------------------------------------- #
# The server: dispatch + cache-aware tool execution
# --------------------------------------------------------------------------- #
class ContextServer:
    def __init__(self, handlers, cache=None):
        self.handlers = handlers
        self.cache = cache

    # -- protocol dispatch --------------------------------------------------- #
    def dispatch(self, request):
        """Map one parsed JSON-RPC request to a response dict, or None for a
        notification (no `id` — never answered)."""
        if not isinstance(request, dict):
            return _error(None, -32600, "Invalid Request")
        if "id" not in request:
            return None  # notification (incl. notifications/initialized) — ignore
        req_id = request.get("id")
        method = request.get("method")
        if method == "initialize":
            return _ok(req_id, {"protocolVersion": PROTOCOL_VERSION,
                                "serverInfo": SERVER_INFO,
                                "capabilities": {"tools": {}}})
        if method == "tools/list":
            return _ok(req_id, {"tools": TOOLS})
        if method == "tools/call":
            return _ok(req_id, self._call_tool(request.get("params") or {}))
        if method == "ping":
            return _ok(req_id, {})
        return _error(req_id, -32601, f"Method not found: {method}")

    def handle_message(self, line):
        """Parse one stdin line and return the serialized response line, or None
        (notification / blank). Parse errors → -32700 with id null."""
        line = line.strip()
        if not line:
            return None
        try:
            request = json.loads(line)
        except ValueError:
            return json.dumps(_error(None, -32700, "Parse error"), separators=(",", ":"))
        response = self.dispatch(request)
        if response is None:
            return None
        return json.dumps(response, separators=(",", ":"), default=str)

    # -- tool execution ------------------------------------------------------ #
    def _call_tool(self, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in self.handlers:
            return _tool_result(f"unknown tool: {name!r}", is_error=True)
        err = _validate_args(name, args)
        if err:
            return _tool_result(err, is_error=True)
        try:
            payload = self._run(name, args)
        except Exception as exc:  # noqa: BLE001 — a tool fault is a result, not a crash
            return _tool_result(f"{type(exc).__name__}: {exc}", is_error=True)
        return _tool_result(json.dumps(payload, default=str), is_error=False)

    def _run(self, name, args):
        """Execute a tool, applying the cache when configured. Payloads are
        redacted before store/return; warm hits are re-stamped as context-only."""
        handler = self.handlers[name]
        cache = self.cache
        if cache is None or not cache.enabled or snapshot_cache is None:
            return self._cold(handler, args)
        key = snapshot_cache.cache_key(cache.db, name, args, cache.fingerprint)
        hit = snapshot_cache.lookup(key, cache_dir=cache.cache_dir, max_age_s=cache.ttl)
        if hit is not None:
            return snapshot_cache.mark_provenance(hit["payload"], "warm")
        marked = self._cold(handler, args)
        snapshot_cache.store(key, marked, {"tool": name, "params": args},
                             cache_dir=cache.cache_dir)
        return marked

    @staticmethod
    def _cold(handler, args):
        redacted = redaction.redact_payload(handler(args), mode="external")
        if snapshot_cache is not None:
            return snapshot_cache.mark_provenance(redacted, "cold")
        return _mark_cold_fallback(redacted)


# --------------------------------------------------------------------------- #
# Serving + CLI
# --------------------------------------------------------------------------- #
def serve(server, stdin=None, stdout=None):
    """Block reading newline-delimited JSON-RPC from stdin, write one compact
    response line per request (notifications produce none), flush each time."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        response = server.handle_message(line)
        if response is not None:
            stdout.write(response + "\n")
            stdout.flush()


def _build_cache(args):
    if args.no_cache or snapshot_cache is None:
        return None
    addons = [p.strip() for p in (args.addons_path or "").split(",") if p.strip()]
    fingerprint = snapshot_cache.addons_fingerprint(addons) if addons else "no-addons-fp"
    return CacheConfig(db=args.db, fingerprint=fingerprint,
                       cache_dir=args.cache_dir, ttl=args.cache_ttl)


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="mcp_server.py",
        description="Bounded read-only MCP context server for Odoo instance truth.")
    p.add_argument("--db", default=os.environ.get("ODOO_DB"),
                   help="database name (required to serve; or ODOO_DB)")
    p.add_argument("--conf", default=os.environ.get("ODOO_CONF"),
                   help="path to odoo.conf (or ODOO_CONF)")
    p.add_argument("--odoo-bin", default=os.environ.get("ODOO_BIN", "odoo-bin"),
                   help="odoo binary (default: odoo-bin; or ODOO_BIN)")
    p.add_argument("--timeout", type=int, default=600, help="per-call subprocess timeout (s)")
    p.add_argument("--cache-dir", default=None, help="snapshot cache dir override")
    p.add_argument("--cache-ttl", type=int, default=900,
                   help="max age (s) a warm snapshot may be served (default 900)")
    p.add_argument("--no-cache", action="store_true", help="disable the warm cache")
    p.add_argument("--addons-path", default="",
                   help="comma-separated addons paths for the cache fingerprint")
    p.add_argument("--selftest", action="store_true",
                   help="print {ok, tools} and exit without serving")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(sys.argv[1:] if argv is None else list(argv))
    if args.selftest:
        print(json.dumps({"ok": True, "tools": TOOL_NAMES}))
        return 0
    if not args.db:
        sys.stderr.write("❌ No database. Pass --db or set ODOO_DB.\n")
        return 2
    cfg = {"db": args.db, "conf": args.conf,
           "odoo_bin": args.odoo_bin, "timeout": args.timeout}
    server = ContextServer(build_handlers(make_real_runner(cfg)), _build_cache(args))
    serve(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
