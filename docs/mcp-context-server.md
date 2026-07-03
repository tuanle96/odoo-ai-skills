# MCP Context Server (`mcp_server.py`)

A **bounded, read-only MCP server** that gives an AI coding agent *pre-edit
instance truth* about a live Odoo database — before it writes a line of code.
It is the agent-facing half of the odoo-ai product; the gate/CI half produces
the merge-approval evidence.

- **What it is:** a fixed set of deterministic *context primitives* (a model's
  fields, its ACLs, its views, its flows, an instance dossier summary, and
  "does Odoo already ship this?"), spoken over MCP stdio.
- **What it is not:** a general RPC bridge. There is **no arbitrary domain, no
  ORM write, no code execution, no free-form SQL**. Each tool is a fixed shape
  with validated inputs.

## Trust boundary (read this first)

> Context **accelerates** the agent. It never **approves** a merge.

Every tool response carries a `_cache` stamp with `provenance`
(`cold` | `warm`) and a `merge_eligible` flag. A **warm** cache hit is context
only — `merge_eligible` is `false`. Merge-approval **evidence must come from a
COLD gate run in CI** (`odoo-ai deploy-gate --strict`), never from this server's
warm context. This is enforced in the data (`snapshot_cache.mark_provenance`),
not just in prose, so a downstream gate can refuse a warm payload even if it is
byte-identical to a fresh one.

Payloads are always run through `redaction.redact_payload(..., mode="external")`
**before** they are stored or returned, so PII / secrets / source bodies never
leave your environment through this channel.

## Tools

| Tool | Arguments | Returns |
|------|-----------|---------|
| `odoo_facts_model` | `model` (required) | Compacted fields, inherit chain, method names, module origins |
| `odoo_facts_security` | `model` (required) | ACL rows (crwu) + record rules (domain, multi-company) |
| `odoo_facts_views` | `model` (required), `arch` (bool, optional) | The model's views; `arch=true` adds the effective primary-form arch |
| `odoo_facts_flows` | `model` (required) | Form buttons, window/server actions, automations, crons |
| `odoo_dossier_summary` | *(none)* | Compact dossier subset: `meta`, `custom_summary`, `studio_footprint` (counts only), `upgrade_risk_flags` |
| `odoo_native_check` | `requirement` (≤500 chars, required), `model` (optional) | Recall-matched curated capability cards, existence-gated against the live instance |

**Input validation:** `model` must match `^[a-z0-9_.]{1,80}$`; `requirement`
is plain text, 1–500 chars. Invalid input is returned as a tool error
(`isError: true`) — never executed.

**Result shape:** success →
`{ "content": [{ "type": "text", "text": "<redacted JSON>" }], "isError": false }`.
Tool errors set `isError: true` with a message in the text block. The JSON in
the text block always carries the `_cache` provenance stamp.

## Registration

### Claude Code

```bash
claude mcp add odoo-context -- \
  python3 /abs/path/skills/odoo-introspect/scripts/mcp_server.py \
  --db mydb --addons-path /path/to/addons
```

### Generic MCP client (`mcpServers` JSON — Claude Desktop, etc.)

```json
{
  "mcpServers": {
    "odoo-context": {
      "command": "python3",
      "args": [
        "/abs/path/skills/odoo-introspect/scripts/mcp_server.py",
        "--db", "mydb",
        "--addons-path", "/path/to/addons"
      ],
      "env": { "ODOO_BIN": "odoo-bin" }
    }
  }
}
```

### Codex CLI (`~/.codex/config.toml`)

```toml
[mcp_servers.odoo-context]
command = "python3"
args = [
  "/abs/path/skills/odoo-introspect/scripts/mcp_server.py",
  "--db", "mydb",
  "--addons-path", "/path/to/addons",
]

[mcp_servers.odoo-context.env]
ODOO_BIN = "odoo-bin"
```

## Configuration

| Flag | Env | Default | Purpose |
|------|-----|---------|---------|
| `--db` | `ODOO_DB` | *(required to serve)* | Database to read |
| `--conf` | `ODOO_CONF` | — | Path to `odoo.conf` |
| `--odoo-bin` | `ODOO_BIN` | `odoo-bin` | Odoo binary |
| `--timeout` | — | `600` | Per-call subprocess timeout (s) |
| `--addons-path` | — | — | Comma-separated addons paths for the cache fingerprint |
| `--cache-dir` | `ODOO_AI_CACHE_DIR` | `~/.cache/odoo-ai/snapshots` | Snapshot cache directory |
| `--cache-ttl` | — | `900` | Max age (s) a warm snapshot may be served |
| `--no-cache` | — | off | Disable the warm cache (always run cold) |
| `--selftest` | — | — | Print `{"ok": true, "tools": [...]}` and exit 0 (no DB, no serve) |

## Protocol

MCP over stdio: **newline-delimited JSON-RPC 2.0**, one JSON object per line on
stdin/stdout. Responses are compact single-line JSON, stdout flushed after each.

- `initialize` → `protocolVersion "2024-11-05"`, `serverInfo {name:
  "odoo-ai-context", version: "0.14.0"}`, `capabilities {tools: {}}`
- `notifications/initialized` → ignored (no response)
- `tools/list` → the six tools with their input schemas
- `tools/call` → tool result (see above)
- `ping` → `{}`
- Unknown method → JSON-RPC error `-32601`
- Any message without an `id` is a notification and never receives a response
- Unparseable line → error `-32700` with `id: null`

## Cache provenance semantics

1. **Cold miss** — the tool shells out to `odoo-bin` (via `facts.py` /
   `dossier.py` / `native_check.py`), the payload is redacted, stamped
   `provenance: "cold"` / `merge_eligible: true`, stored, and returned.
2. **Warm hit** — a stored snapshot within `--cache-ttl` is re-served with
   `provenance: "warm"` / `merge_eligible: false`. The runner is **not**
   re-invoked (no new subprocess). Context only.
3. The cache key is derived from `db`, tool name, arguments, and an addons
   **fingerprint** (mtime/size of the source tree). Touching a source file
   flips the fingerprint, so a warm hit can never outlive a code change.

If `snapshot_cache` is unavailable, the server degrades gracefully to
cold-only serving; responses still carry a `cold` provenance stamp.

## Security notes

- **Read-only.** No writes, no arbitrary domains/RPC, no code execution.
- **Validated inputs.** Model names are pattern-checked; requirement length is
  capped; unknown tools / bad arguments are refused as tool errors.
- **Redaction always applied** before any payload is stored or returned.
- **Warm ≠ evidence.** The provenance stamp keeps warm context out of the
  merge-approval path by construction.
