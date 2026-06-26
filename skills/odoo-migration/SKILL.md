---
name: odoo-migration
description: >-
  Writing Odoo upgrade/migration scripts — when a module bump needs code beyond
  what the ORM does on -u, and how to write it. Use whenever renaming a field or
  model, changing a field's type, backfilling a new required field, merging or
  moving data, dropping obsolete columns, recomputing a changed stored compute,
  or asking "will -u handle this automatically or will I lose data?". Covers the
  migrations/<version>/ pre-/post-/end- layout, the migrate(cr, version) hook,
  odoo.upgrade.util / openupgradelib helpers, and version bumps. Read the live
  field inventory from odoo-introspect before assuming a column's name or type.
---

# Odoo migrations

On `-u` the ORM auto-applies *additive, non-destructive* schema changes. Anything that **moves, renames, or reinterprets existing data** it cannot infer — and its default for a renamed field is to drop the old column and create an empty new one. **That silent data loss is what migration scripts prevent.** Most "needs a migration?" mistakes are writing one that wasn't needed, or skipping one that was.

**The rule: if existing rows must change shape or move, you write the migration; if you're only adding, let `-u` do it.**

Targets Odoo 17/18. Cross-version deprecations: `skills/odoo-introspect/references/version-matrix.md`.

## Does this need a migration?

Confirm the real column/field names and types first with `odoo-introspect` (`MODEL=x … < scripts/model_brief.py` → fields + types + stored/compute). For a **rename or drop**, also run the reverse-impact scan — `odoo-ai refs <model> <field>` — so the migration covers *every* dependent (computes, related fields, views, record rules, saved filters, server actions), not just the column you noticed. Then:

| Change | Migration? | Why |
|---|---|---|
| Add a new field | **No** | `-u` creates the column, applies `default` |
| Add a new model / index / constraint | **No** | created on `-u` |
| Add a **stored computed** field | **No** | `-u` computes it for existing rows |
| **Change** an existing stored compute's logic | **Yes (post)** | old rows keep stale values — force recompute |
| Rename a field or model | **Yes (pre)** | ORM drops old + makes empty new → data loss |
| Change a field's type (char→m2o, etc.) | **Yes (pre/post)** | values must be converted, not reinterpreted |
| New **required** field on a populated table | **Yes (pre)** | backfill before the NOT NULL lands |
| Merge/move data across fields or models | **Yes (post)** | pure data transform |
| Drop an obsolete field's leftover column | **Maybe** | ORM leaves removed columns dangling |
| Dedup / repair corrupted data | **Yes** | not derivable from schema |

## Folder layout & the migrate hook

```
my_module/
  migrations/            # 'upgrades/' is also valid (Odoo 13+); pick one
    18.0.2.0.0/          # = FULL version: <odoo major>.<your manifest version>
      pre-rename.py
      post-backfill.py
      end-cleanup.py
```

```python
def migrate(cr, version):          # cr = DB cursor; version = currently-installed version
    if not version:                # None on a fresh -i → migrations never run on install
        return
    cr.execute("UPDATE my_table SET state = 'done' WHERE state = 'closed'")
```

| Phase (file prefix) | Runs | ORM state | Use for |
|---|---|---|---|
| `pre-` | **before** this module's schema is updated | old columns still present | rename/convert columns *before* the ORM reshapes them |
| `post-` | after module + deps loaded & updated | new schema live, `env` usable | data backfills, recompute, cross-model moves |
| `end-` | after **all** modules in the run finished | everything updated | final cleanup spanning modules |

`-` after the prefix, then any name; files run alphabetically within a phase.

## What triggers a script — the bump that bites

Scripts in `migrations/<V>/` run **only on `-u`** (never `-i`) and **only when `<V>` > the installed version and ≤ the new manifest version**. So:

1. Bump `version` in `__manifest__.py` (use the full `18.0.x.y.z` form).
2. Name the folder to **match that new version exactly**.
3. Run `odoo-bin -u my_module -d <DB>`.

Forget the bump (or mismatch the folder) and the script silently never runs — the #1 "my migration did nothing" cause.

## Helpers — don't hand-write reference fixups

| Library | Import | When |
|---|---|---|
| upgrade-util (Odoo) | `from odoo.upgrade import util` | Odoo's upgrade platform / installed alongside |
| openupgradelib (OCA) | `from openupgradelib import openupgrade` | self-hosted community upgrades |

```python
from odoo.upgrade import util
def migrate(cr, version):
    util.rename_field(cr, 'res.partner', 'x_old', 'x_new')   # also fixes filters, domains, views
    util.rename_model(cr, 'old.model', 'new.model')          # handles m2m tables, references
```

Renames touch far more than one column (domains, `ir_filters`, views, server actions) — these helpers chase the references a raw `ALTER TABLE` would miss. See `references/migration-scripts.md` for recompute, type-change, and backfill recipes.

## Gotchas that fail silently

- **Folder version ≠ bumped manifest version** → script never runs, no error.
- **Renaming with the ORM instead of a pre-script** → old data gone, new column empty, install still "succeeds".
- **Changed stored-compute logic without a recompute** → old rows keep stale values forever; new rows look correct, so it hides.
- **Backfilling a required field in `post-`** → too late; the NOT NULL was enforced during the schema update. Do it in `pre-`.
- **Heavy `search([])` + Python loop on a big table** → migration runs for hours. Use set-based SQL / `util` batching.
- **`api.Environment(cr, SUPERUSER_ID, {})` in `pre-`** → models not yet updated; only safe in `post-`/`end-`.

## References & scripts

- `references/migration-scripts.md` — full pre/post/end examples, `odoo.upgrade.util` + `openupgradelib` function map, recompute-a-stored-field recipes, type-change and required-field backfill patterns, env-in-post idiom.
- Field/type ground truth before writing: `odoo-introspect` `model_brief.py`.
- Proving the upgrade path (`-u` on a data DB, `--test-enable`): `odoo-testing`.
- Per-version API renames/deprecations: `skills/odoo-introspect/references/version-matrix.md`.
