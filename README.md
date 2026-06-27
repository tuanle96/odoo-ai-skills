# odoo-ai-skills

[![ci](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/ci.yml)
[![tests](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/tests.yml)
[![Odoo 17/18/19](https://img.shields.io/badge/Odoo-17%20%7C%2018%20%7C%2019-714B67)](https://www.odoo.com)
[![license: LGPL-3](https://img.shields.io/badge/license-LGPL--3-blue)](#license)

A [Claude Code](https://docs.claude.com/en/docs/claude-code) **skills suite for doing Odoo development with an AI agent — correctly.**

Odoo composes every model, view, security rule, and automation **at runtime** from the installed addon dependency graph. Field names, the method-resolution order, the `super()` chain, the rendered view arch, record rules — none of it is reliably knowable from memory or `grep`. It exists only in **the running instance**. Guessing it is the single biggest cause of AI-written Odoo code that looks right, runs for admin on one record, and breaks for a real user, on a second company, in a batch, or on the next upgrade.

**So every skill in this suite turns on one rule:**

> **Read ground truth from the running instance first → build the smallest correct change → prove it with a test → review it before it merges.**

## Why this exists

Left to memory, LLMs invent Odoo field and model names, reach for APIs that were removed (`attrs`/`states`, `<tree>`, `name_get`), call `super()` at the wrong MRO layer, sprinkle `sudo()` to silence access errors, and ship stored computes with an incomplete `@api.depends`. These fail **silently at runtime**, not at lint time — exactly where confidence is most dangerous. This suite closes that gap by making the agent read the live registry before it writes, and by encoding the Odoo-specific contracts (security, MRO, manifest wiring, version deltas) that a generic model doesn't know.

## How to use

- **New to a task?** Invoke the **`odoo`** skill — it routes you to the right sub-skill.
- **About to write code?** Invoke **`odoo-introspect`** first to dump the model/flow as JSON (`odoo-ai all <model>`), then the relevant build skill, then **`odoo-testing`**, then **`odoo-review`** before you merge.
- **Something "didn't apply"?** `odoo-ai preflight <module>` before assuming a code bug.
- **About to rename/drop a field?** `odoo-ai refs <model> <field>` to see everything that depends on it first.

## Requirements

- **Odoo 17 / 18** (version floor), through **Odoo 19** (current LTS, released Sept 2025). v16 deltas and the v18.1 → 19 API changes (`check_access`/`has_access`, `@api.private`, `type='jsonrpc'`, `_read_group`/`formatted_read_group`, `aggregator`, `record.env.*`, `odoo.Domain`) are noted per-skill and in `skills/odoo-introspect/references/version-matrix.md`.
- For introspection: shell access to run `odoo-bin shell` against a dev/staging DB (self-hosted or an odoo.sh branch), or the RPC fallback for Odoo Online/SaaS — see `skills/odoo-introspect/references/introspection.md`.
- Optional: the [`tuanle96/mcp-odoo`](https://github.com/tuanle96/mcp-odoo) MCP server to expose introspection as agent tools.

## The skills

### Tier 0 — Foundation (the ground-truth engine)
| Skill | What it does |
|-------|--------------|
| **odoo-introspect** | The engine every other skill calls first. Four JSON layers (A: fields+MRO+super+security · B: views/buttons · C: menu/data/reports · D: real runtime trace), plus focused scanners — **refs** (reverse field impact), **preflight** (is it even loaded?), and **state_capture** (Layer F: runtime values at a breakpoint + exception post-mortem) — and the `odoo-ai` CLI. |

### Tier 1 — Core loop
| Skill | What it does |
|-------|--------------|
| **odoo-dev** | Customize safely: fields, overrides, inheritance mode, the right hook, MRO layer. |
| **odoo-module-scaffold** | New module skeleton + correct `__manifest__.py` (incl. `external_dependencies` hygiene). |
| **odoo-views** | View XML (form/list/kanban/search) + inheritance/xpath; the v17/18 `attrs`-removal & `<list>`/`<chatter/>` changes. |
| **odoo-security** | ACL, record rules, groups, multi-company — authoring + the real eval order. |
| **odoo-testing** | The test gate: `at_install`/`post_install`, non-admin, multi-company, batch, `-i`/`-u`. |
| **odoo-review** | The review gate: catch the security / data-loss / silent-correctness / perf defects AI ships before merge. |
| **odoo-debug** | Symptom→tool table, traceback decoder, `--dev`, runtime tracing, "my change didn't apply" preflight. |

### Tier 2 — Frontend & reporting
| Skill | What it does |
|-------|--------------|
| **odoo-owl** | OWL 2 **backend** web client: components, custom field widgets, patching, services, assets. |
| **odoo-web** | **Public** web: HTTP controllers (`http.route`), website pages, the portal `/my`, and the `publicWidget`→Interactions shift. |
| **odoo-reports** | QWeb PDF/HTML reports: actions, templates, `_get_report_values`, paperformat. |

### Tier 3 — Lifecycle
| Skill | What it does |
|-------|--------------|
| **odoo-data** | Data/demo, `noupdate`, sequences, config parameters. |
| **odoo-migration** | Version upgrades & migration scripts (`migrations/<version>/`), reverse-impact before renames. |
| **odoo-perf** | Recordset hygiene, prefetch/cache, stored-compute cost, indexes. |
| **odoo-deploy** | `odoo.conf`, workers, Docker, CI test runs — plus **odoo.sh** (git-push deploy, staging rehearsal) and Odoo Online limits. |

### Tier 4 — Domain playbooks
| Skill | What it does |
|-------|--------------|
| **odoo-domain-playbooks** | Per-app maps (sale/stock/account/mrp/purchase/hr): key models, methods to introspect, right hooks, gotchas. |

### Router
| Skill | What it does |
|-------|--------------|
| **odoo** | Entry point: task → skill decision table. |

## The introspection engine (`odoo-ai`)

One command gathers ground truth for the agent before any code is written:

```bash
# everything (Layers A+B+C) for a model:
scripts/odoo-ai --db <DB> all sale.order --methods action_confirm,write,create

# add the real runtime trace (Layer D):
scripts/odoo-ai --db <DB> all sale.order --methods action_confirm \
    --record-id 42 --method action_confirm

# focused scanners:
scripts/odoo-ai --db <DB> refs sale.order commitment_date   # who breaks if I change this field
scripts/odoo-ai --db <DB> preflight my_module               # installed? loaded from where? shadowed?

# runtime values (Layer F) — the JSON analog of an IDE's "inspect variables":
scripts/odoo-ai --db <DB> state sale.order 42 action_confirm \
    --break sale.order._action_confirm --fields state,amount_total   # args/locals/self at the breakpoint
scripts/odoo-ai --db <DB> state sale.order 42 action_confirm --on-exception   # full stack + locals if it raises
```

See `skills/odoo-introspect/` for the JSON shape of each layer and the SaaS RPC fallback.

## Layout

```
.claude-plugin/plugin.json       # plugin manifest
skills/<name>/SKILL.md           # one skill per directory
skills/<name>/references/        # progressive-disclosure deep dives
skills/odoo-introspect/scripts/  # the introspection engine + odoo-ai CLI
```

## Development

The introspection scripts are import-safe: the env-dependent work runs only inside `odoo-bin shell`, while the pure helpers are unit-tested without Odoo.

```bash
python -m pytest skills/odoo-introspect/scripts/tests -q     # pure-function tests
python skills/odoo-introspect/scripts/tests/test_pure_functions.py   # no-pytest fallback
```

CI (`.github/workflows/`) compiles every script and runs these tests on each push.

## License

LGPL-3. See `.claude-plugin/plugin.json`.
