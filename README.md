# odoo-ai-skills

[![ci](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/ci.yml)
[![tests](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/tests.yml/badge.svg)](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/tests.yml)
[![integration](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/integration.yml/badge.svg)](https://github.com/tuanle96/odoo-ai-skills/actions/workflows/integration.yml)
[![Odoo 17/18/19](https://img.shields.io/badge/Odoo-17%20%7C%2018%20%7C%2019-714B67)](https://www.odoo.com)
[![license: LGPL-3](https://img.shields.io/badge/license-LGPL--3-blue)](#license)

A [Claude Code](https://docs.claude.com/en/docs/claude-code) **skills suite for doing Odoo development with an AI agent — correctly.**

> **Static indexes *suggest*; `odoo-ai-skills` *verifies* — against your running Odoo instance.**

It's the **local-first verification & deploy gate for AI-written Odoo changes**. Bring any coding agent, any hosted knowledge index, any `grep` — they propose names and patterns; this suite decides whether the patch is *actually safe on this customer's instance*: real fields, real MRO, real security, real runtime, real upgrade path. **No SaaS, no seats, no API key, no metadata leaves your box.**

🌐 **Landing page: [tuanle96.github.io/odoo-ai-skills](https://tuanle96.github.io/odoo-ai-skills/)**


Odoo composes every model, view, security rule, and automation **at runtime** from the installed addon dependency graph. Field names, the method-resolution order, the `super()` chain, the rendered view arch, record rules — none of it is reliably knowable from memory or `grep`. It exists only in **the running instance**. Guessing it is the single biggest cause of AI-written Odoo code that looks right, runs for admin on one record, and breaks for a real user, on a second company, in a batch, or on the next upgrade.

**So every skill in this suite turns on one rule:**

> **Read ground truth from the running instance first → build the smallest correct change → prove it with a test → review it before it merges.**

![odoo-ai workflow demo](examples/demo.gif)

See the full [worked example](examples/sale-order-walkthrough.md) — a real `sale.order` change taken through introspect → patch → test, with its module tested in CI.

## Why this exists

Left to memory, LLMs invent Odoo field and model names, reach for APIs that were removed (`attrs`/`states`, `<tree>`, `name_get`), call `super()` at the wrong MRO layer, sprinkle `sudo()` to silence access errors, and ship stored computes with an incomplete `@api.depends`. These fail **silently at runtime**, not at lint time — exactly where confidence is most dangerous. This suite closes that gap by making the agent read the live registry before it writes, and by encoding the Odoo-specific contracts (security, MRO, manifest wiring, version deltas) that a generic model doesn't know.

## Not a hosted knowledge index — a runtime verification gate

A hosted Odoo *knowledge index* (a cloud service that pre-indexes Odoo's source across versions) is great at **breadth**: "what does standard `sale.order` look like across v8→19? show me examples from many repos." Use one if you like — as an **upstream source**.

But a static index, by construction, **cannot know what is true in _your_ instance**: which modules are installed, what Studio/OCA/local patches changed the final registry, the effective view arch for this group/company, per-user/per-company security, runtime behaviour, dev↔prod drift, or whether an upgrade preserves real data. Those are exactly the failures that pass review and break in production (see the [high-risk playbooks](docs/high-risk-playbooks.md)).

`odoo-ai-skills` is the other half: it reads **this running instance** and turns a proposed change into proof — then gates the merge. The line is **static indexes suggest; the running instance disposes.**

- **Local-first / sovereign.** Everything runs in your shell. No account, no API key, no per-seat fee; sensitive instance data (that's why [`redact`](#the-enforcement-gates-layer-i) exists) never leaves your environment.
- **Instance-grounded, not memory-grounded.** The instance *is* the index for what's installed here — no per-version re-indexing treadmill.
- **Verification & enforcement, not just lookup.** [Layer I](#the-enforcement-gates-layer-i) gates the deploy: scenario tests, env drift, validation, redaction, migration risk, and an `approve / needs-human / block` verdict.

Want ecosystem breadth too? Feed an external index's suggestions in as *claims* — `odoo-ai-skills` verifies each against the live instance rather than trusting it (see `verify-claims`). The suite's own `docs` lookup (Layer J) is just one such upstream source, built locally and existence-gated.

## Install as a Claude Code plugin

This repo is a Claude Code plugin (a `.claude-plugin/plugin.json` manifest plus the `skills/` directory) **and** its own marketplace (`.claude-plugin/marketplace.json`). Install it in two commands:

```bash
claude plugin marketplace add tuanle96/odoo-ai-skills   # register the marketplace
claude plugin install odoo-ai-skills@odoo-ai            # install the plugin
```

The 19 skills then load namespaced — `/odoo-ai-skills:odoo` (router), `/odoo-ai-skills:odoo-introspect`, etc. Update later with `claude plugin update odoo-ai-skills@odoo-ai`.

To try it before installing, load it straight from a local clone:

```bash
claude --plugin-dir /path/to/odoo-ai-skills    # then /plugin to browse
claude plugin validate /path/to/odoo-ai-skills # check the manifest
```

**Running the bundled `odoo-ai` CLI after install.** A plugin is copied into Claude's cache, so reference the CLI through the plugin-root variable rather than a relative path:

```bash
"${CLAUDE_PLUGIN_ROOT}"/skills/odoo-introspect/scripts/odoo-ai --db <DB> all sale.order
```

(When working in a clone, `scripts/odoo-ai` as shown elsewhere is fine.)

## How to use

- **New to a task?** Invoke the **`odoo`** skill — it routes you to the right sub-skill.
- **New to the instance — don't know where to start?** `odoo-ai surface` ranks the live entrypoints (buttons, crons, automations, routes) so you don't guess the entry method; `odoo-ai esg` then samples the real cross-app flow.
- **About to *add* a field/model/wizard/report/cron/automation (or override a core flow)?** Invoke **`odoo-capabilities`** first — `odoo-ai native-check "<requirement>"` (matches curated cards, existence-gated against the instance) or `odoo-ai capabilities <model>` for the full surface — to check what Odoo already ships before reinventing it. The best patch is sometimes no patch.
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
| **odoo-capabilities** | **Step 0** — before reinventing platform behavior, ask what Odoo already ships. `odoo-ai native-check "<requirement>"` (Layer H gate-then-rank) recall-matches ~34 curated capability cards (TF-IDF + intent-phrase), then **existence-gates** each against the live instance and returns candidates with cited evidence; `odoo-ai capabilities <model>` / `--module <addon>` maps the full native surface (wizards, actions, crons, automations, sequences, mixins, fields) with xmlids as evidence. `odoo-ai native-learn "<phrase>" --card <id>` teaches it mappings so recall improves from use. Fires only for *additive* / core-override tasks. |
| **odoo-introspect** | The engine every other skill calls first. JSON layers — A: fields+MRO+super+security · B: views/buttons · C: menu/data/reports · D: real runtime trace (with SQL-hotspot / write-map / exception summary) · **G: effective per-user/company security** — plus focused scanners: **refs** (reverse field impact, graph-resolved dotted paths), **preflight** (is it even loaded?), and **state_capture** (Layer F: runtime values at a breakpoint + exception post-mortem) — and the `odoo-ai` CLI. Also hosts the **Layer I enforcement gates** (scenario tests · env parity · static validator · redaction · upgrade harness · deploy-gate · evidence bundle · BYO-index `verify-claims`). |
| **odoo-docs** | **Layer J** — local developer-docs lookup. Build a TF-IDF index of the official Odoo docs once (`odoo-ai docs-build --version 18`), then `odoo-ai docs "<question>"` returns ranked passages + canonical odoo.com URLs. Subordinate to introspection (docs *propose*, the instance *disposes*); built locally, never vendored (clean CC-BY-SA). |

### Tier 1 — Core loop
| Skill | What it does |
|-------|--------------|
| **odoo-dev** | Customize safely: fields, overrides, inheritance mode, the right hook, MRO layer. |
| **odoo-module-scaffold** | New module skeleton + correct `__manifest__.py` (incl. `external_dependencies` hygiene). |
| **odoo-views** | View XML (form/list/kanban/search) + inheritance/xpath; the v17/18 `attrs`-removal & `<list>`/`<chatter/>` changes. |
| **odoo-security** | ACL, record rules, groups, multi-company — authoring + the real eval order. |
| **odoo-testing** | The test gate: `at_install`/`post_install`, non-admin, multi-company, batch, `-i`/`-u`. |
| **odoo-review** | The review gate: catch the security / data-loss / silent-correctness / perf defects AI ships before merge. |
| **odoo-debug** | Symptom→tool table, traceback decoder, `--dev`, runtime tracing + **runtime state capture / exception post-mortem** (Layer F) and **debugpy/DAP** step-through, "my change didn't apply" preflight. |

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

### Report output
| Skill | What it does |
|-------|--------------|
| **html-report** | Render any audit / review / analysis / RCA / summary as **one consistent, self-contained HTML page** — shared bold "Magazine" theme, CSS inlined (no CDN, no server), auto-opens. Presentation only; *not* Odoo QWeb business documents (that's `odoo-reports`). |

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
scripts/odoo-ai --db <DB> refs sale.order commitment_date --resolve-paths  # who breaks if I change this field
scripts/odoo-ai --db <DB> preflight my_module               # installed? loaded from where? shadowed?
scripts/odoo-ai --db <DB> security sale.order --user 7      # effective ACL + record rules + restricted fields

# runtime values (Layer F) — the JSON analog of an IDE's "inspect variables":
scripts/odoo-ai --db <DB> state sale.order 42 action_confirm \
    --break sale.order._action_confirm --fields state,amount_total   # args/locals/self at the breakpoint
scripts/odoo-ai --db <DB> state sale.order 42 action_confirm --on-exception   # full stack + locals if it raises
```

See `skills/odoo-introspect/` for the JSON shape of each layer and the SaaS RPC fallback.

## The enforcement gates (Layer I)

Reading ground truth stops the agent *guessing*; it doesn't yet *prove* the change is safe. Layer I turns the evidence into gates — the realistic target is **agent-written, tool-verified, human-approved**, not blind autonomous deploy. Four of these are **pure and local** (no `odoo-bin shell`, no DB — they run in CI or on a laptop):

```bash
# turn introspection into the MANDATORY tests for this change (risk-tiered):
scripts/odoo-ai --db <DB> scenarios sale.order --methods action_confirm   # → required scenarios + a TransactionCase skeleton

# don't claim production safety against a divergent env:
scripts/odoo-ai --db dev  env-fingerprint   # capture each side, then:
scripts/odoo-ai env-diff dev.json prod.json                 # LOCAL — modules/edition/studio/config drift

# the odoo-review checklist, as an executable linter (LOCAL, no DB):
scripts/odoo-ai validate addons/my_module                   # attrs/sudo/N+1/batch/version anti-patterns

# make introspection JSON safe to share with an external LLM (LOCAL):
scripts/odoo-ai redact /tmp/odoo-ai/sale_order.state.json   # strip source/locals, mask PII, redact secrets
scripts/odoo-ai scan-secrets path/to/file                   # secret/key scan before it leaves the box

# upgrade safety: a RENAME (keep data) vs a DROP (lose it), + a pre-migrate scaffold:
scripts/odoo-ai --db <DB> upgrade-check sale.order --against old_brief.json
scripts/odoo-ai upgrade-diff old_brief.json new_brief.json  # LOCAL

# aggregate all the evidence into a go/no-go for high-risk modules (LOCAL):
scripts/odoo-ai deploy-gate /tmp/odoo-ai/evidence_bundle/   # → approve | needs-human | block
```

These came out of the v0.7 codebase evaluation (under `plans/reports/`), which found the suite excellent at *grounding* but advisory-only at *enforcement*. Each gate's pure logic is unit-tested without Odoo.

## Discovery, sampling, measurement & enforcement (Layer K)

The layers above all assume you already know *what* to introspect. Layer K answers the cold-start problem — *where does reality start in this instance?* — and makes the tools impossible to skip:

```bash
# DISCOVER where to start — rank the live entrypoint surface (buttons, server
# actions, crons, automations, reports, HTTP routes), instance-wide or scoped:
scripts/odoo-ai --db <DB> surface                       # → ranked roots + top_trace_seeds
scripts/odoo-ai --db <DB> surface sale.order            # ...around one model

# UNDERSTAND the overall process — sample the top entrypoints' real traces and
# merge them into a cross-model / cross-app flow skeleton (NOT a static map):
scripts/odoo-ai --db <DB> esg sale.order                # → Execution Surface Graph

# MEASURE that hallucinations actually drop — score the gate on a benchmark of the
# classic LLM Odoo mistakes (account.invoice, customer_id, fields_view_get, …):
scripts/odoo-ai --db <DB> eval                          # → detection_rate / truth_recall

# ENFORCE no-introspect-no-edit (LOCAL) — block an edit until its model is read:
scripts/odoo-ai gate-edit addons/my_module/models/sale_order.py
```

Wire `gate-edit` as a Claude Code **PreToolUse hook** (`skills/odoo-introspect/references/enforcement-hooks.md`) so the agent *cannot* edit an Odoo model before reading its ground truth — the Oracle's "even perfect tools ≠ used tools" failure mode, closed. `surface`/`esg` stay true to *runtime-grounded, never memorized*: process understanding **emerges from sampled traces**, never a stale stored atlas. (Design rationale: the codebase analysis under `plans/reports/`.)

## Tested against real Odoo

Beyond the unit suite, the integration smoke runs **every layer + gate against live Odoo 17 / 18 / 19** in CI (`.github/workflows/integration.yml`) — including Layer K (`surface`/`esg`/`eval`): **89/89 checks pass on each of 17, 18, and 19**, reproducible locally in one command via `docker-compose.e2e.yml` (Postgres + the three Odoo versions). The `eval` harness scores **detection_rate 1.0 / truth_recall 1.0** on all three (every classic hallucination caught, every real confirmed). The suite has also been validated end-to-end against a real **390-module Enterprise** instance (Studio fields, custom addons, multi-company): all read-only layers (A–H), the enforcement gates, the BYO-index `verify-claims` (it correctly flagged an external claim about a module *absent* from that instance), and the write/execute layers — a runtime `trace` of `sale.order.action_confirm` captured the real cross-app cascade (`stock.picking` / `stock.move` / `quality.check`) and rolled back. The failures a static index can't catch are in `docs/high-risk-playbooks.md`.

## Security — handling introspection output

The introspection layers dump real instance data. **Layer F (`state`) captures runtime args, locals, and `self` field values, and `SOURCE=1` on Layer A includes full method bodies.** This output can contain secrets, tokens, API keys, passwords, customer PII, or proprietary business logic.

- **`state` redacts common sensitive keys by default** — locals/dict-keys/fields named like `password`, `token`, `secret`, `api_key`, `authorization`, `session`, etc. become `<redacted>`. Extend with `--redact-extra ssn,iban`; disable with `--no-redact` only on a trusted dev box. Redaction is key-name based, so it won't catch a secret stored under an innocuous name.
- **Source bodies and field *values* are not redacted** — `SOURCE=1` and `--fields` can still surface sensitive content. **Do not paste raw `state` / source JSON into an external LLM or a public issue unless it's been reviewed and redacted.**
- Run introspection against a **dev/staging** DB where practical, not production.
- Treat the JSON like a debugger session: useful for the agent in-loop, but not something to ship around.

## Layout

```
.claude-plugin/plugin.json       # plugin manifest
.claude-plugin/marketplace.json  # self-hosted marketplace (install source)
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

**Integration smoke test (needs a real Odoo).** `scripts/tests/integration_smoke.py` runs the layers against a live instance and asserts on the JSON (selection literals, manifest `by_location` split, view `inheritance_chain`, seeded `noupdate`, Layer F redaction). It's opt-in — skipped unless `ODOO_DB` is set — so it never breaks the unit CI. Run it against a dev container or let `.github/workflows/integration.yml` run it on the official `odoo:17.0` / `18.0` / `19.0` images (with a dedicated job running the `sale_confirm_guard` worked example on `odoo:18.0`). See `skills/odoo-introspect/references/introspection.md` for the container wrapper and exact invocation.

## Contributing & security

- Contributions: see `CONTRIBUTING.md` (project layout, the import-safe script pattern, running the unit + integration tests).
- Changes are tracked in `CHANGELOG.md`.
- Handling introspection output safely (redaction, what not to share): see `SECURITY.md`.

## License

LGPL-3.0-or-later. See [`LICENSE`](LICENSE).
