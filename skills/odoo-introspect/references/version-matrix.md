# Odoo version matrix (v16 / v17 / v18)

Cross-cutting reference for the API renames and behavior changes the skills suite depends on. **Version floor for the suite is Odoo 17/18**; v16 is listed because a few changes landed there and you may still meet it. When in doubt, don't trust this table over the instance — introspect (that's the whole point). But these are the renames agents get wrong from memory.

**"Suite use" column:** `script` = a layer script reads/calls it directly (breaks below the listed version); `skill` = other suite skills teach writing it; `—` = advisory only.

## ORM / Python API

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Display name | `name_get(self)` returning `[(id, name)]` | `_compute_display_name(self)` with `@api.depends`, sets `self.display_name`; `name_get` **removed** | **v17** | skill |
| View loader | `fields_view_get(...)` → `{arch, fields}` | `get_view(view_id=None, view_type='form', **options)` → `{arch, models}` | **v16** | **script** (Layer B calls `get_view`) |
| Low-level search | `_search(self, args, ...)` | `_search(self, domain, offset=0, limit=None, order=None)` — first arg renamed `args`→`domain` | **v17** | skill |
| Copy | `copy_data` returns a single dict | `copy_data(self, default=None)` returns a **list of dicts** (batch copy) | **v17** | skill |
| Grouping | `read_group(...)` → list of dicts | internal `_read_group(domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None)` returns **list of value-tuples**; public `read_group` kept as compat wrapper | **v17** | skill |

## Views / XML

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Conditional modifiers | `attrs="{'invisible': [...] }"`, `states="..."` | direct Python exprs: `invisible="..."`, `readonly="..."`, `required="..."`, `column_invisible="..."`; `attrs` **and** `states` **removed** | **v17** | **script** (Layer B reads these modifier attrs) |
| Chatter | `<div class="oe_chatter"> …message_ids… </div>` | single `<chatter/>` tag | **v18** | skill |
| Asset bundles | XML `<template inherit_id="web.assets_backend">` | `__manifest__.py` `"assets": {"web.assets_backend": [...]}` dict | **v15** | skill |

## Web framework (JS)

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Frontend framework | Owl 1.x + legacy jQuery/`Widget` system | **Owl 2** is the framework; legacy backend widgets progressively removed v16 → v17 | **v16** (→ v17) | skill |

## Automation / data

| Concept | Old (≤ prior) | New | Landed | Suite use |
|---------|---------------|-----|--------|-----------|
| Automations | "Automated Actions" (model `base.automation`) | UI renamed **"Automation Rules"**; trigger mechanism reworked (`trigger` values like `on_create_or_write`, `on_time`, `on_unlink`, stage/tag/priority triggers). Model id **unchanged** (`base.automation`) | **v17** | **script** (Layer A reads `base.automation`) |

## Notes the suite relies on

- **`get_view` (Layer B) needs v16+.** On v15 and below, `entrypoints.py`'s view parse won't work — fall back to `fields_view_get` or raw `ir.ui.view.arch`. Everything else (window actions, reports) still reads fine.
- **`base.automation` requires the `base_automation` module installed.** If it isn't, Layer A's `auto_triggers.automated_actions` read fails and the failure is surfaced in the brief's `_warnings` (not silently dropped). Absence of the module ≠ absence of automations elsewhere (server actions, crons are separate).
- **The v17 view-modifier change is load-bearing for Layer B.** `entrypoints.py` reports `invisible` / `readonly` / `required` / `column_invisible` straight off each `<field>` / `<button>`. On a v16 instance you may still see `attrs` strings instead — read them as the legacy shape.
- **`_read_group` / `read_group`:** if a skill or override touches grouping, target the v17 signature. The public `read_group` still exists for back-compat but new code should use `_read_group` with `aggregates=`/`having=`.
- **Chatter tag is v18-only.** Writing `<chatter/>` on v17 fails; writing the `oe_chatter` div on v18 is deprecated. Check the target version before emitting either.

When a skill needs a fact not in this table, it should **introspect the running instance** rather than extend this from memory — consistent with the suite's core rule: read ground truth first.
