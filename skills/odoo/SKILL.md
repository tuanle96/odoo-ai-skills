---
name: odoo
description: >-
  Entry point and router for doing Odoo development with an AI agent. Start here
  whenever a task touches an Odoo codebase and you're not sure which specific
  skill applies — it maps the task to the right one (introspection, models &
  overrides, module scaffolding, views, OWL frontend, security, testing, reports,
  data/sequences, migration, deploy, or domain playbooks), even if the user never
  says the word "skill". The rule shared by every skill in this suite: Odoo
  composes each model at runtime from the installed addon graph, so READ GROUND
  TRUTH FROM THE RUNNING INSTANCE FIRST, then build — never guess fields, MRO,
  super() chains, view arch, or security. Targets Odoo 17/18/19.
---

# Odoo — development suite (router)

Odoo builds every model, view, security rule, and automation **at runtime** from the installed addon dependency graph. None of it is reliably knowable from memory or `grep` — it exists only in **this** running instance. So the whole suite turns on one move:

**Read ground truth first (the `odoo-introspect` skill), then build the smallest correct change, then prove it (the `odoo-testing` skill).**

**Version floor: Odoo 17/18; Odoo 19 is the current LTS (Sept 2025).** For v16 and older — and for the recent v18.1 → 19 API renames AI gets wrong (`check_access`/`has_access`, `@api.private` RPC exposure, `type='jsonrpc'`, `_read_group`/`formatted_read_group`, `aggregator`, `record.env.*`, `odoo.Domain`) — check `skills/odoo-introspect/references/version-matrix.md` before trusting a signature or view syntax.

## Always start here

0. **`odoo-capabilities`** — Step 0, *only* when the task would **add** a field/model/wizard/report/cron/automation or **override a core flow**: check what Odoo already ships before reinventing it — `odoo-ai native-check "<requirement>"` (matches curated cards, existence-gated against the instance), or `odoo-ai capabilities <model>` / `--module <addon>` for the full surface. Skip for bug-fixes, view tweaks, or work inside your own module.
1. **`odoo-introspect`** — Tier 0 ground-truth engine. Dump the model/flow as JSON (Layer A fields+MRO+super+security, B view/buttons, C menu/data/reports, D real runtime trace) with `odoo-ai all <model>`. Do this **before** writing code.
   - **Don't know *where* to start?** `odoo-ai surface` (Layer K) ranks the live entrypoints — buttons, crons, automations, routes — so you don't guess the entry method; `odoo-ai esg` samples the real cross-app flow from the top roots. New to a customized instance → start here.
2. Pick the build skill from the table below.
3. **`odoo-testing`** — prove it (test fails before, passes after; non-admin / multi-company / batch; `-i` + `-u`).

> **Enforcement (recommended): no-introspect-no-edit.** Wire `odoo-ai gate-edit` as a Claude Code PreToolUse hook (see `odoo-introspect/references/enforcement-hooks.md`) so an edit to an Odoo model is *blocked* until that model has an introspection brief — the tools become inevitable, not optional. Verify the gate still catches hallucinations with `odoo-ai eval`.

## Task → skill

| If the task is… | Use |
|---|---|
| **New to the instance / don't know where to start** — what entrypoints & flows even exist here? | **odoo-introspect** `surface` / `esg` (Layer K discovery) |
| About to **add** a field/model/wizard/report/cron/automation, or **override a core flow** — does Odoo already ship this? | **odoo-capabilities** (Step 0, before introspect) |
| Find out what a model/flow really is (fields, MRO, `super()`, security, views, runtime order) | **odoo-introspect** (always first) |
| Add/modify fields; override `create`/`write`/compute/onchange/constrains; pick inheritance mode & MRO layer | **odoo-dev** |
| Create a new module / `__manifest__.py` / directory structure | **odoo-module-scaffold** |
| Write or edit view XML (form/list/kanban/search), inheritance & xpath | **odoo-views** |
| Build or patch web UI components / custom field widgets (OWL 2 / JS) | **odoo-owl** |
| HTTP controllers / routes, website pages, portal `/my`, public frontend JS | **odoo-web** |
| ACL, record rules, groups, multi-company access | **odoo-security** |
| QWeb PDF / HTML reports | **odoo-reports** |
| Seed data, demo, `noupdate`, sequences, config parameters | **odoo-data** |
| Version upgrade / migration scripts | **odoo-migration** |
| `odoo.conf`, workers, Docker, odoo.sh, CI test runs, deployment | **odoo-deploy** |
| Diagnose an error, trace what actually runs, fix slowness | **odoo-debug**, **odoo-perf** |
| Prove a change with tests (`at_install` vs `post_install`, etc.) | **odoo-testing** |
| Review / audit a patch or PR before merge (AI-generated code especially) | **odoo-review** |
| Customize a standard app (sale/stock/account/mrp/purchase/hr) | **odoo-domain-playbooks** (+ introspect) |
| Present an audit / review / analysis / findings as a shareable **HTML report** | **html-report** |

## The tiers

- **Tier 0 — foundation:** `odoo-capabilities` (Step 0: is it already native?) → `odoo-introspect` (every other skill calls it).
- **Tier 1 — core loop:** `odoo-dev` · `odoo-module-scaffold` · `odoo-views` · `odoo-security` · `odoo-testing` · `odoo-review` · `odoo-debug`.
- **Tier 2 — frontend & report:** `odoo-owl` · `odoo-web` · `odoo-reports`.
- **Tier 3 — lifecycle:** `odoo-data` · `odoo-migration` · `odoo-perf` · `odoo-deploy`.
- **Tier 4 — domain playbooks:** `odoo-domain-playbooks`.

## Context strategy — don't pour the whole codebase in

Odoo core + Enterprise + OCA + the project's custom addons vastly exceed any context window, and over-stuffing context *degrades* output. Don't paste source trees. Instead, load the **smallest ground-truth artifact** for the task:

- **Per task, feed the introspection JSON, not source.** `odoo-ai all <model>` produces a compact JSON brief (fields, MRO, security, depends) — that one file answers "what exists here" far more reliably than dumping addon `.py`/`.xml`. Attach only the layer(s) the task needs (A always; B for views/buttons; C for menus/data/reports; D for big flows).
- **Read canonical source narrowly, on demand.** For OWL/JS, open the *one* reference widget you're extending (the `odoo-owl` rule), not the whole `web` addon.
- **The skills carry the durable rules; the instance carries the facts.** Keep version/contract rules in these skills (and `version-matrix.md`); pull instance-specific names/arch from `odoo-introspect` each task rather than memorizing them into a context file that goes stale.

## Odoo's own AI features ≠ this suite

Odoo 19 ships built-in AI (AI Agents, natural-language search, AI server actions, Studio AI fields). That's end-user/runtime automation **inside** an Odoo instance — distinct from this suite, which is about an AI agent *writing correct Odoo source code*. When a task is "configure an in-app AI agent / AI field," that's Studio/server-action territory (→ `odoo-data`, `odoo-domain-playbooks`), not module code.

## The one anti-pattern that breaks everything

Writing Odoo code from memory. Field names, method signatures, the `super()` chain, view arch, and record rules differ per instance and per version. If you're about to guess any of them, stop and run `odoo-introspect` instead.
