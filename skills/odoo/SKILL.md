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
  super() chains, view arch, or security. Targets Odoo 17/18.
---

# Odoo — development suite (router)

Odoo builds every model, view, security rule, and automation **at runtime** from the installed addon dependency graph. None of it is reliably knowable from memory or `grep` — it exists only in **this** running instance. So the whole suite turns on one move:

**Read ground truth first (the `odoo-introspect` skill), then build the smallest correct change, then prove it (the `odoo-testing` skill).**

**Version floor: Odoo 17/18.** For v16 and older, check `skills/odoo-introspect/references/version-matrix.md` before trusting a signature or view syntax.

## Always start here

1. **`odoo-introspect`** — Tier 0 ground-truth engine. Dump the model/flow as JSON (Layer A fields+MRO+super+security, B view/buttons, C menu/data/reports, D real runtime trace) with `odoo-ai all <model>`. Do this **before** writing code.
2. Pick the build skill from the table below.
3. **`odoo-testing`** — prove it (test fails before, passes after; non-admin / multi-company / batch; `-i` + `-u`).

## Task → skill

| If the task is… | Use |
|---|---|
| Find out what a model/flow really is (fields, MRO, `super()`, security, views, runtime order) | **odoo-introspect** (always first) |
| Add/modify fields; override `create`/`write`/compute/onchange/constrains; pick inheritance mode & MRO layer | **odoo-dev** |
| Create a new module / `__manifest__.py` / directory structure | **odoo-module-scaffold** |
| Write or edit view XML (form/list/kanban/search), inheritance & xpath | **odoo-views** |
| Build or patch web UI components / custom field widgets (OWL 2 / JS) | **odoo-owl** |
| ACL, record rules, groups, multi-company access | **odoo-security** |
| QWeb PDF / HTML reports | **odoo-reports** |
| Seed data, demo, `noupdate`, sequences, config parameters | **odoo-data** |
| Version upgrade / migration scripts | **odoo-migration** |
| `odoo.conf`, workers, Docker, CI test runs, deployment | **odoo-deploy** |
| Diagnose an error, trace what actually runs, fix slowness | **odoo-debug**, **odoo-perf** |
| Prove a change with tests (`at_install` vs `post_install`, etc.) | **odoo-testing** |
| Customize a standard app (sale/stock/account/mrp/purchase/hr) | **odoo-domain-playbooks** (+ introspect) |

## The tiers

- **Tier 0 — foundation:** `odoo-introspect` (every other skill calls it).
- **Tier 1 — core loop:** `odoo-dev` · `odoo-module-scaffold` · `odoo-views` · `odoo-security` · `odoo-testing` · `odoo-debug`.
- **Tier 2 — frontend & report:** `odoo-owl` · `odoo-reports`.
- **Tier 3 — lifecycle:** `odoo-data` · `odoo-migration` · `odoo-perf` · `odoo-deploy`.
- **Tier 4 — domain playbooks:** `odoo-domain-playbooks`.

## The one anti-pattern that breaks everything

Writing Odoo code from memory. Field names, method signatures, the `super()` chain, view arch, and record rules differ per instance and per version. If you're about to guess any of them, stop and run `odoo-introspect` instead.
