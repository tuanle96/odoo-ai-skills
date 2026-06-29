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

Do not add an MCP wrapper unless shell access to `odoo-ai` is no longer enough.
