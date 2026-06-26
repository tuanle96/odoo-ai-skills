# odoo-ai-skills

A Claude Code **skills suite for Odoo development done 100% by an AI agent.**

Odoo composes every model, view, security rule, and automation **at runtime** from the installed addon dependency graph. Field names, the method-resolution order, the `super()` chain, the rendered view arch, record rules — none of it is reliably knowable from memory or `grep`. It exists only in **the running instance**. Guessing it is the root cause of "half-working" customizations that break elsewhere.

**So every skill in this suite turns on one rule:**

> **Read ground truth from the running instance first → build the smallest correct change → prove it with a test.**

## How to use

- New to a task? Invoke the **`odoo`** skill — it routes you to the right sub-skill.
- About to write code? Invoke **`odoo-introspect`** first to dump the model/flow as JSON (`odoo-ai all <model>`), then the relevant build skill, then **`odoo-testing`**.

## Requirements

- **Odoo 17 / 18** (version floor). v16 deltas are noted per-skill and in `skills/odoo-introspect/references/version-matrix.md`.
- For introspection: shell access to run `odoo-bin shell` against a dev/staging DB (or the RPC fallback for Odoo Online/SaaS — see `odoo-introspect/references/introspection.md`).
- Optional: the [`tuanle96/mcp-odoo`](https://github.com/tuanle96/mcp-odoo) MCP server to expose introspection as agent tools.

## The skills

### Tier 0 — Foundation (the ground-truth engine)
| Skill | What it does |
|-------|--------------|
| **odoo-introspect** | 4 introspection layers (fields+MRO+super+security, view/buttons, menu/data/reports, real runtime trace) + the `odoo-ai` CLI. Every other skill calls this first. |

### Tier 1 — Core loop
| Skill | What it does |
|-------|--------------|
| **odoo-dev** | Customize safely: fields, overrides, inheritance mode, the right hook, MRO layer. |
| **odoo-module-scaffold** | New module skeleton + correct `__manifest__.py`. |
| **odoo-views** | View XML (form/list/kanban/search) + inheritance/xpath; the v17/18 `attrs`-removal & `<list>`/`<chatter/>` changes. |
| **odoo-security** | ACL, record rules, groups, multi-company — authoring + the real eval order. |
| **odoo-testing** | The test gate: `at_install`/`post_install`, non-admin, multi-company, batch, `-i`/`-u`. |
| **odoo-debug** | Symptom→tool table, traceback decoder, `--dev`, runtime tracing. |

### Tier 2 — Frontend & reporting
| Skill | What it does |
|-------|--------------|
| **odoo-owl** | OWL 2 web client: components, custom field widgets, patching, services, assets. |
| **odoo-reports** | QWeb PDF/HTML reports: actions, templates, `_get_report_values`, paperformat. |

### Tier 3 — Lifecycle
| Skill | What it does |
|-------|--------------|
| **odoo-data** | Data/demo, `noupdate`, sequences, config parameters. |
| **odoo-migration** | Version upgrades & migration scripts (`migrations/<version>/`). |
| **odoo-perf** | Recordset hygiene, prefetch/cache, stored-compute cost, indexes. |
| **odoo-deploy** | `odoo.conf`, workers, Docker, CI test runs. |

### Tier 4 — Domain playbooks
| Skill | What it does |
|-------|--------------|
| **odoo-domain-playbooks** | Per-app maps (sale/stock/account/mrp/purchase/hr): key models, methods to introspect, right hooks, gotchas. |

### Router
| Skill | What it does |
|-------|--------------|
| **odoo** | Entry point: task → skill decision table. |

## Layout

```
.claude-plugin/plugin.json   # plugin manifest
skills/<name>/SKILL.md        # one skill per directory
skills/<name>/references/     # progressive-disclosure deep dives
skills/odoo-introspect/scripts/  # the introspection engine + odoo-ai CLI
```

## License

See `.claude-plugin/plugin.json`.
