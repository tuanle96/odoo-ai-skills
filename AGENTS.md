# odoo-ai-skills

This repo is a Codex plugin and skill suite for Odoo development.

Core rule: for Odoo work, read ground truth from the running instance before
writing code. Do not guess fields, MRO, view arch, record rules, method
signatures, or runtime flow from memory or static search alone.

Use `srcwalk` first for codebase exploration.

For Odoo tasks:

- Start with the `odoo` skill router when the exact skill is unclear.
- Use `odoo-introspect` before editing Odoo models, views, security, reports,
  or business flows.
- For additive work, check native capability first with `odoo-capabilities`.
- Prefer the bundled CLI at `skills/odoo-introspect/scripts/odoo-ai`.
- Keep runtime JSON and state captures local unless redacted with
  `odoo-ai redact`.
- Prove changes with the smallest relevant test or gate before claiming done.

The bundled read-only MCP context server (`skills/odoo-introspect/scripts/mcp_server.py`,
ships in v0.14) is the sanctioned bounded-facts surface: no arbitrary RPC, redacted
output, cache-provenance-labeled. Use it when an agent needs Odoo facts as tools.
Arbitrary-RPC MCP wrappers remain forbidden — they reopen the guessing/leak surface the
suite exists to close.
