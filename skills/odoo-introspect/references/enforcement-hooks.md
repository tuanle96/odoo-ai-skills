# Enforcement — make the tools inevitable (no-introspect-no-edit)

> The single biggest failure mode of an Odoo agent is not a missing tool — it is
> a present tool the agent **skips** because the change "looks obvious," then
> edits a model from memory and ships a hallucinated field / wrong MRO layer.
> Reading ground truth is only worth building if it actually runs. This turns the
> suite's core rule into an **executable precondition**, not a prompt.

## The rule

**No edit to an Odoo model is allowed until that model has been introspected.**
`odoo-ai gate-edit <files>` enforces it locally (no DB): it extracts the models a
patch touches (`_name`/`_inherit` in `.py`; `<field name="model">` / `model=` in
`.xml`), checks the evidence dir for an introspection brief of each, runs the
static validator, and returns `allow` / `block` with the exact commands to unblock.

```bash
odoo-ai gate-edit addons/my_module/models/sale_order.py
# block → { "required_commands": ["odoo-ai all sale.order"] }
# (after you run that, the brief lands in /tmp/odoo-ai and the gate flips to allow)
```

## Wire it as a Claude Code PreToolUse hook (the inevitable path)

Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (global).
The hook runs `gate-edit` before every `Edit`/`Write`/`MultiEdit`; a `block`
stops the edit and feeds the reason + required command back to the agent, which
then introspects and retries — automatically, with no extra prompt text.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/skills/odoo-introspect/scripts/hooks/pre_edit_gate.py\""
          }
        ]
      }
    ]
  }
}
```

In a local clone use the repo path instead of `${CLAUDE_PLUGIN_ROOT}`:
`python3 /path/to/odoo-ai-skills/skills/odoo-introspect/scripts/hooks/pre_edit_gate.py`.

The hook only fires for files **inside an Odoo module** (a `__manifest__.py`
ancestor) and only `.py`/`.xml` — everything else passes straight through. It is
**fail-open**: any hook error, or a missing `odoo-ai`, allows the edit, so a
misconfiguration never bricks your editing. Tune via env:

| env | effect |
|-----|--------|
| `ODOO_AI_OUT` | evidence dir the gate reads (default `/tmp/odoo-ai`) |
| `ODOO_AI_BIN` | path to the `odoo-ai` CLI (default: alongside the hook) |
| `ODOO_AI_GATE_DISABLE=1` | bypass the gate for this session |
| `ODOO_AI_GATE_STRICT=1` | **fail closed**: if the gate itself can't run (missing CLI, timeout, no decision), block the edit instead of allowing it. For teams that want hard enforcement over convenience. |

## The auto-trigger rules (what to introspect for which edit)

`gate-edit` enforces the *minimum* (a brief exists). These are the **risk-tiered
follow-ups** the agent should run by reflex — encode them in your workflow / the
`odoo-dev` and `odoo-review` skills, or as additional hooks:

| You are about to… | Mandatory before the edit |
|---|---|
| override `write`/`create`/a compute | `odoo-ai refs <model> <field>` (reverse impact) + a Layer D `trace` of a real flow that writes it |
| override a core flow (`action_*`) | `odoo-ai all <model>` (MRO/super) **+** `odoo-ai trace <model> <id> <method>` (real cross-app cascade) |
| add a field/model/wizard/report/cron | `odoo-ai native-check "<requirement>"` first — does Odoo already ship it? |
| edit a view (xpath/modifier) | `odoo-ai entrypoints <model>` (resolved arch + inheritance chain) **+** `odoo-ai security <model> --user <non-admin>` (group-gated fields) |
| change ACL / record rules | `odoo-ai security <model> --user <id> --company <id>` (effective domain) |
| rename/drop a field | `odoo-ai refs <model> <field>` **+** `odoo-ai upgrade-check` (rename vs drop) — **before** the rename |
| not sure where to start | `odoo-ai surface` (rank the entrypoints) → `odoo-ai esg` (sample the real flow) |

## Via the mcp-odoo MCP server

If you expose the engine through [`tuanle96/mcp-odoo`](https://github.com/tuanle96/mcp-odoo),
register `gate-edit` as a tool the agent must call before its edit tool, or run
the same `pre_edit_gate.py` logic in the server's pre-write middleware. The
principle is identical: the introspect→verify step is not optional, it is on the
only path to a committed edit.

## Why fail-open, not fail-closed

A guardrail that blocks legitimate work when *it* is broken gets disabled, and
then enforces nothing. Fail-open keeps the gate trusted: it nudges the common
case (skip-introspection) onto the right path, and never stands between you and a
fix when the tooling itself is misconfigured. Enforcement is about changing the
*default*, not building a cage.
