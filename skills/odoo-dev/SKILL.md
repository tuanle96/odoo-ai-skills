---
name: odoo-dev
description: >-
  Customizing or extending Odoo server-side — adding/modifying fields, overriding
  model methods (create/write/compute/onchange/constrains/unlink), extending core
  addons, choosing the right inheritance mode, or deciding where an override should
  land in the MRO. Use whenever working on an Odoo codebase, even if the user
  doesn't say "skill" — any time you would otherwise GUESS at Odoo's field
  inventory, ORM API, method resolution order, super() chain, or which hook to
  extend. Read ground truth first (via the odoo-introspect skill), then make the
  smallest safe patch and prove it with the odoo-testing gate. Targets Odoo 17/18.
---

# Odoo development

Odoo composes each model at runtime from the installed addon dependency graph. The field list, the method resolution order (MRO), the `super()` chain, the view layout, the security rules, and the automations that fire on write — none are reliably knowable from memory or source-grep. They exist only in **this** running instance. Guessing is the root cause of "half-working" customizations that break elsewhere.

**The rule: read ground truth from the running registry first, then customize.** Discovery is a solved problem — delegate it to the **`odoo-introspect`** skill. This skill is the *customize-safely* loop layered on top of it.

**Version floor: Odoo 17/18.** On v16 or older, some ORM/method names differ — check `skills/odoo-introspect/references/version-matrix.md` before relying on a signature below.

## Three different "orders" — do not conflate them

1. **Module load order** — from `depends` in `__manifest__.py`. Decides what exists when the registry builds, and **which MRO layer your override lands at**. Override `sale.order.action_confirm` while depending only on `sale` (not `sale_stock`) and your code sits at a different layer than you meant.
2. **Method resolution order (MRO)** — the class chain of the final registry model. The **potential** `super()` path, not a guarantee of what runs: a layer that skips `super()` cuts the chain; an early `return` under a context flag skips the rest.
3. **Runtime call order** — what actually executes on a click or cron: onchange → constrains → method → procurement → stock moves → invoice hooks → automations → recomputes. A **graph across many models**, not a list. Static analysis can't fully reconstruct it — trace it.

## Workflow: discover → plan → code (never skip step 1)

### 1. Discover — read the instance (delegate to `odoo-introspect`)

Pull ground truth before any code. Don't hand-roll introspection — invoke the **`odoo-introspect`** skill, which runs four layers:

- **Layer A — `model_brief`**: fields, MRO + super-analysis (`has_super` / `super_position` / `returns_before_super`), security (ACL + record rules), auto-triggers, recommended `depends`. **Always run this.**
- **Layer B — `entrypoints`**: form/list buttons → which method/action they call, view-level field modifiers, window actions. Run when a button/view/action is in scope.
- **Layer C — `metadata`**: menu graph, seeded `noupdate` data, report definitions.
- **Layer D — `trace_flow`**: the real runtime call sequence + SQL on a throwaway record (rolls back). Run for any sizable flow (sale/stock/account/mrp) — MRO alone is not enough there.

One command — `odoo-ai all <model>` — dumps A–D as JSON. No shell (Odoo Online/SaaS)? Use the skill's RPC fallback.

### 2. Plan — confirm before coding

From the briefs, state: model + **inheritance mode**; which fields to **reuse** vs add (half the "new" fields already exist — check the inventory); the **smallest** extension point (prefer a `_prepare_*` / `_action_*` / `_get_*` hook over overriding `create` / `write` / `action_*`); whether you call `super()` and **where**; the `depends` your `__manifest__.py` needs so the override lands at the right MRO layer; and the security / multi-company / performance risks. Read `references/override-surface.md` before overriding a method you haven't touched before, and `references/patterns.md` for inheritance/recordset/compute correctness.

### 3. Code — then prove it

Write the smallest patch extending the real `super()` from step 1. Then satisfy the **test gate** — invoke the **`odoo-testing`** skill (test-first; non-admin / multi-company / batch where relevant; `-i` clean DB + `-u` data DB). A patch without that is vibe coding with extra steps.

## Pick the built-in, don't hand-roll it

| Need | Use | Not |
|------|-----|-----|
| A derived value | computed field (`@api.depends`) or `related=` | code in `write` |
| React to UI input | `@api.onchange` | manual recompute |
| Enforce a rule | `@api.constrains` (or `_sql_constraints` for DB-level) | ad-hoc `if` in `write` |
| React to a save | override `create`/`write` (`@api.model_create_multi`, `vals` is a **list**) | a separate sync method |
| Auto numbering | `ir.sequence` | string formatting |
| Chatter / activities | inherit `mail.thread` / `mail.activity.mixin` | custom log fields |
| Scheduled logic | `ir.cron` / automated action | hand-written loop + flag |

If you're writing procedural code inside `write()` to *compute* or *validate*, you're almost certainly using the wrong hook.

## References & related skills

**This skill's references**
- `references/override-surface.md` — every safe-to-override v18 method + signature + the v17/v18 renames.
- `references/patterns.md` — inheritance modes, recordset hygiene, compute/depends, security eval order, silent gotchas.

**Other skills in the loop**
- `odoo-introspect` — Tier 0 ground-truth engine (Layers A–D + `odoo-ai`). Run first.
- `odoo-testing` — the test/PR gate every patch must pass.
- `odoo-views` — XML view authoring (xpath, v17/18 `invisible=` / `readonly=`).
- `odoo-security` — authoring ACL CSV, record rules, groups.
- `odoo-module-scaffold` — new module skeleton + `__manifest__.py`.
