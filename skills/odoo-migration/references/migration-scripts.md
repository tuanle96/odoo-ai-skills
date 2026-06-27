# Migration scripts — reference

Targets Odoo 17/18, through Odoo 19 (current LTS). Per-version renames: `skills/odoo-introspect/references/version-matrix.md`.

## The two helper libraries

| | upgrade-util | openupgradelib |
|---|---|---|
| Source | Odoo (the upgrade platform) | OCA |
| Install | bundled on Odoo's upgrade service; `pip install odoo-upgrade` for local | `pip install openupgradelib` |
| Import | `from odoo.upgrade import util` | `from openupgradelib import openupgrade` |
| Test base | `from odoo.upgrade import testing` | — |

Both are optional — plain SQL via `cr.execute` always works; the helpers exist so renames also fix the *references* (domains, filters, views, m2m tables) that a bare `ALTER TABLE` leaves broken.

### Common functions

```python
# upgrade-util
util.rename_field(cr, 'sale.order', 'x_note', 'x_remark')
util.rename_model(cr, 'old.thing', 'new.thing')
util.rename_table(cr, 'old_table', 'new_table')
util.remove_field(cr, 'sale.order', 'x_dead')
util.column_exists(cr, 'sale_order', 'x_new')        # guard before DDL
util.create_column(cr, 'sale_order', 'x_new', 'varchar')
util.recompute_fields(cr, 'sale.order', ['amount_total'])
util.m2o_to_x2m(cr, model, table, field, source_field)

# openupgradelib
openupgrade.rename_fields(env, [('sale.order', 'sale_order', 'x_note', 'x_remark')])
openupgrade.rename_columns(cr, {'sale_order': [('x_note', 'x_remark')]})
openupgrade.rename_models(cr, [('old.thing', 'new.thing')])
openupgrade.rename_tables(cr, [('old_table', 'new_table')])
openupgrade.logged_query(cr, "UPDATE ...")           # logs rowcount + timing
openupgrade.load_data(env.cr, 'module', 'migrations/x/data.xml')
```

## pre- : rename / convert before the ORM reshapes the table

The old column still exists here; the module's new schema has **not** been applied yet.

```python
# migrations/18.0.2.0.0/pre-rename.py
from odoo.upgrade import util

def migrate(cr, version):
    if not version:
        return
    util.rename_field(cr, 'res.partner', 'x_legacy_code', 'x_ref')
    # plain-SQL equivalent if you can't use a helper:
    # cr.execute("ALTER TABLE res_partner RENAME COLUMN x_legacy_code TO x_ref")
```

### New required field on a populated table — backfill in pre-

If the new field is `required=True`, the NOT NULL is enforced *during* the schema update. Create + fill the column in `pre-` so the constraint finds every row populated:

```python
# migrations/18.0.2.0.0/pre-fill-required.py
def migrate(cr, version):
    if not version:
        return
    cr.execute("ALTER TABLE sale_order ADD COLUMN IF NOT EXISTS x_channel varchar")
    cr.execute("UPDATE sale_order SET x_channel = 'web' WHERE x_channel IS NULL")
```

(Alternative: ship the field as non-required, backfill in `post-`, add the constraint in a later version.)

## post- : data transforms with the ORM available

Models and dependencies are loaded and updated; build an environment and use the ORM.

```python
# migrations/18.0.2.0.0/post-backfill.py
from odoo import api, SUPERUSER_ID

def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    orders = env['sale.order'].with_context(active_test=False).search([('x_remark', '=', False)])
    for batch in (orders[i:i + 1000] for i in range(0, len(orders), 1000)):
        batch.write({'x_remark': 'migrated'})
        env.cr.commit()        # checkpoint long runs; safe inside migrate()
```

Prefer set-based SQL for big tables; reserve the ORM loop for logic that needs business methods.

## Recompute a changed stored compute

Adding a stored compute needs nothing — `-u` fills it. **Changing** an existing one leaves old rows stale. Force it:

```python
# post-recompute.py
from odoo import api, SUPERUSER_ID

def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    records = env['sale.order'].with_context(active_test=False).search([])
    records._compute_margin()      # stored compute assigns self.field → persists
    env.flush_all()                # ensure writes hit the DB
```

Or, with the helper: `util.recompute_fields(cr, 'sale.order', ['margin'])`. To invalidate via dependencies instead, `records.modified(['cost', 'price'])` then `env.flush_all()`.

## Type change (e.g. char → many2one)

Rename the old column aside in `pre-`, create the new typed column, map values in `post-`.

```python
# pre-type-change.py
def migrate(cr, version):
    if not version: return
    cr.execute("ALTER TABLE product_template RENAME COLUMN x_brand TO x_brand_legacy")
    # new x_brand (many2one) column is created by -u from the updated model

# post-type-change.py
from odoo import api, SUPERUSER_ID
def migrate(cr, version):
    if not version: return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Brand = env['product.brand']
    cr.execute("SELECT id, x_brand_legacy FROM product_template WHERE x_brand_legacy IS NOT NULL")
    for tmpl_id, name in cr.fetchall():
        brand = Brand.search([('name', '=', name)], limit=1) or Brand.create({'name': name})
        env['product.template'].browse(tmpl_id).x_brand = brand.id
    cr.execute("ALTER TABLE product_template DROP COLUMN x_brand_legacy")
```

## end- : cross-module final cleanup

Runs after every module in the upgrade finished for that version — use when your fix depends on another module's post step having run.

## Version bump checklist

- [ ] `version` in `__manifest__.py` raised (full `18.0.x.y.z`).
- [ ] `migrations/<that exact version>/` folder created.
- [ ] Phase chosen right: rename/required-backfill → `pre-`; data/recompute → `post-`.
- [ ] `if not version: return` guard (skip on fresh installs).
- [ ] Long loops batched + `cr.commit()` checkpoints.
- [ ] Helper used for renames so references aren't orphaned.

## Testing the upgrade path

A migration is unverified until proven on data. Run the module update against a **DB with real records** and let tests assert the result:

```bash
odoo-bin -d staging_copy -u my_module --test-enable --stop-after-init   # exit 0 = pass
```

See `odoo-testing` for the `-i clean` + `-u data` gate, non-admin/multi-company cases, and `@tagged` selection. upgrade-util's `odoo.upgrade.testing` base classes (or a `@tagged('post_install')` case asserting migrated values) cover the assertions.
