# Odoo version matrix (v16 → v19)

Cross-cutting reference for the API renames and behavior changes the skills suite depends on. **Version floor for the suite is Odoo 17/18, and Odoo 19 is the current LTS** (released Sept 2025; `saas-19.x` is the faster-moving online series). v16 is listed because a few changes landed there and you may still meet it. When in doubt, don't trust this table over the instance — introspect (that's the whole point). But these are the renames agents get wrong from memory, and the v18.1 → 19 deltas below are the highest-risk "looks correct, silently wrong" zone because most model training predates them.

**"Suite use" column:** `script` = a layer script reads/calls it directly (breaks below the listed version); `skill` = other suite skills teach writing it; `—` = advisory only.

## ORM / Python API

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Display name | `name_get(self)` returning `[(id, name)]` | `_compute_display_name(self)` with `@api.depends`, sets `self.display_name`. Deprecated **16.4**, `name_get` **removed v17** | **v16.4 / v17** | skill |
| View loader | `fields_view_get(...)` → `{arch, fields}` | `get_view(view_id=None, view_type='form', **options)` → `{arch, models}` | **v16** | **script** (Layer B calls `get_view`) |
| Low-level search | `_search(self, args, ...)` | `_search(self, domain, offset=0, limit=None, order=None)` — first arg renamed `args`→`domain` | **v17** | skill |
| Copy | `copy_data` returns a single dict | `copy_data(self, default=None)` returns a **list of dicts** (batch copy) | **v17** | skill |
| Grouping | `read_group(...)` → list of dicts | internal `_read_group(domain, groupby=(), aggregates=(), having=(), ...)` returns **list of value-tuples**; `read_group` **deprecated 18.2** in favor of `_read_group` + public `formatted_read_group` | **v17 / 18.2** | skill |
| Aggregate attr | field `group_operator='sum'` | field **`aggregator='sum'`** (`group_operator` renamed) | **v17.2** | skill |
| Access check | `check_access_rights(op)` + `check_access_rule(op)` (two calls) | unified **`check_access(op)`** (raises) / **`has_access(op)`** (bool) / **`_filtered_access(op)`** (filters a recordset). Old pair **superseded v18, deprecated → v19** | **v18 / v19** | skill |
| RPC exposure | public methods callable via RPC implicitly | public methods are RPC-callable **by default**; mark internal ones **`@api.private`** to block RPC | **v18.2** | skill |
| Domain helper | `from odoo.osv import expression` + list-domain helpers | **`odoo.Domain`** / `odoo.domain` manipulation API; `odoo.osv` **deprecated** | **v18.1 / v19** | skill |
| Env shortcuts | `record._cr`, `record._context`, `record._uid` | `record.env.cr`, `record.env.context`, `record.env.uid` — the `_`-prefixed shortcuts **deprecated** | **v19** | skill |
| Constraints / indexes | `_sql_constraints = [(...)]` tuples | constraints and indexes declarable as **model attributes** (new API alongside the tuple form) | **v18.1** | skill |

## Views / XML

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Conditional modifiers | `attrs="{'invisible': [...] }"`, `states="..."` | direct Python exprs: `invisible="..."`, `readonly="..."`, `required="..."`, `column_invisible="..."`; `attrs` **and** `states` **removed** | **v17** | **script** (Layer B reads these modifier attrs) |
| QWeb output | `t-esc` (escapes) / `t-raw` (unescaped, XSS footgun) | **`t-out`** (escapes by default; raw only for `markup()`); `t-raw` **removed**, `t-esc` superseded | **v17** | skill |
| List root | `<tree>` + `view_mode="tree,form"` | `<list>` + `view_mode="list,form"` | **v18** | skill |
| Chatter | `<div class="oe_chatter"> …message_ids… </div>` | single `<chatter/>` tag | **v18** | skill |
| Asset bundles | XML `<template inherit_id="web.assets_backend">` | `__manifest__.py` `"assets": {"web.assets_backend": [...]}` dict | **v15** | skill |

## Web framework (JS)

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Frontend framework | Owl 1.x + legacy jQuery/`Widget` system | **Owl 2** is the framework; legacy backend widgets progressively removed v16 → v17 | **v16** (→ v17) | skill |
| Field value API | `props.value` / `props.update(v)` | read `props.record.data[props.name]`, write `props.record.update({[props.name]: v})` | **v17** | skill |
| Public-site JS | `publicWidget.Widget.extend({selector, events, this.$el})` (jQuery) | **Interactions**: extend `Interaction` from `@web/public/interaction`, `static selector`, `dynamicContent` (`t-on-click`, `t-att-*`), register in `public.interactions`; lifecycle `setup → willStart → start (sync) → destroy` | **v18 (→ v19)** | skill |
| JSON controller | `@http.route(type='json')` | `@http.route(type='jsonrpc')` (`'json'` renamed) | **v18.1** | skill |

## Automation / data

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Automations | "Automated Actions" (model `base.automation`) | UI renamed **"Automation Rules"**; trigger mechanism reworked (`trigger` values like `on_create_or_write`, `on_time`, `on_unlink`, stage/tag/priority triggers). Model id **unchanged** (`base.automation`) | **v17** | **script** (Layer A reads `base.automation`) |
| Demo data (online) | demo data loaded by default | demo data **no longer loaded by default** on newer online versions — seed/test assumptions break | **v18 (saas)** | skill |

## v18.1 / 18.2 / 19.0 deltas — the highest-risk zone

Most assistant training centers on v15–v17 patterns, so these recent changes are where AI confidently emits code that loads on 18 and breaks (or silently misbehaves) on 18.1+/19. Confirm against the instance; never type these from memory.

- **`check_access_rights` / `check_access_rule` → `check_access` / `has_access` / `_filtered_access`** (v18 unify, v19 supersede). The old pair still appears everywhere in training data. → `odoo-security`, `odoo-review`.
- **`@api.private` (v18.2)** — public model methods are now **RPC-callable by default**; an internal helper left public and unmarked is a new remote-exposure surface. Mark non-RPC methods `@api.private`. **Security-relevant.** → `odoo-security`, `odoo-web`.
- **`type='json'` → `type='jsonrpc'` (v18.1)** on controllers. → `odoo-web`.
- **`read_group` → `_read_group` (tuples) / `formatted_read_group` (v18.2)**. Code indexing results by dict key silently breaks. → `odoo-perf`.
- **`group_operator` → `aggregator` (v17.2)** field attribute. → `odoo-dev`, `odoo-perf`.
- **`record._cr` / `._context` / `._uid` deprecated → `record.env.*` (v19).** Pervasive in older code. → `odoo-dev`.
- **`odoo.osv` deprecated; `odoo.Domain` domain API (v18.1 → v19).** AI still imports `from odoo.osv import expression`. → `odoo-dev`.
- **Constraints/indexes as model attributes (v18.1)** alongside `_sql_constraints` tuples. → `odoo-dev`.
- **Demo data not loaded by default on newer online versions** — don't rely on a demo record existing. → `odoo-data`, `odoo-testing`.

## Notes the suite relies on

- **`get_view` (Layer B) needs v16+.** On v15 and below, `entrypoints.py`'s view parse won't work — fall back to `fields_view_get` or raw `ir.ui.view.arch`. Everything else (window actions, reports) still reads fine.
- **`base.automation` requires the `base_automation` module installed.** If it isn't, Layer A's `auto_triggers.automated_actions` read fails and the failure is surfaced in the brief's `_warnings` (not silently dropped). Absence of the module ≠ absence of automations elsewhere (server actions, crons are separate).
- **The v17 view-modifier change is load-bearing for Layer B.** `entrypoints.py` reports `invisible` / `readonly` / `required` / `column_invisible` straight off each `<field>` / `<button>`. On a v16 instance you may still see `attrs` strings instead — read them as the legacy shape.
- **`_read_group` / `read_group`:** if a skill or override touches grouping, target the v17 signature (tuples). The public `read_group` is **deprecated in 18.2**; new code should use `_read_group` with `aggregates=`/`having=`, or `formatted_read_group` for the formatted public output.
- **Chatter tag is v18-only.** Writing `<chatter/>` on v17 fails; writing the `oe_chatter` div on v18+ is deprecated. Check the target version before emitting either.
- **The introspection scripts themselves are v19-safe** — they read the registry (`fields_get`, `ir.model.access`, `ir.rule`, `get_view`) and do **not** call the deprecated `read_group` / `check_access_rights` / `name_get`, so the engine runs unchanged on 17 → 19. The version drift lives in the *guidance*, not the tooling.

When a skill needs a fact not in this table, it should **introspect the running instance** rather than extend this from memory — consistent with the suite's core rule: read ground truth first.
