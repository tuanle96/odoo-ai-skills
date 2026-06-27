# Data files, sequences & configuration — reference

Targets Odoo 17/18, through Odoo 19 (current LTS). Older versions: `skills/odoo-introspect/references/version-matrix.md`.

## XML record syntax

```xml
<odoo>
  <record id="partner_acme" model="res.partner">
    <field name="name">ACME Inc</field>
    <field name="is_company" eval="True"/>
    <field name="country_id" ref="base.us"/>                  <!-- m2o by external id -->
    <field name="user_id" eval="ref('base.user_admin')"/>     <!-- same, via eval -->
    <field name="comment">Multi-line
text is fine</field>
  </record>
</odoo>
```

- `id` = the external ID (xmlid). Bare `partner_acme` → `<current_module>.partner_acme`; cross-module needs the full `module.xmlid`.
- `ref("module.xmlid")` resolves to that record's **database id**. Use the `ref=` attribute for a single many2one; use `eval=` when you need an expression.
- `eval` evaluates a Python expression with `ref`, `obj()`, `time`, `datetime`, `timedelta`, `Command` in scope: `eval="(DateTime.today() + timedelta(days=7)).strftime('%Y-%m-%d')"`.
- Search-domain reference (rare, slower than `ref`): `<field name="x" search="[('code','=','US')]"/>` writes the first match — use only when no stable xmlid exists.
- `<delete model="x" id="m.xmlid"/>` or `search=` removes shipped records.
- `<function model="x" name="method"><value eval="[...]"/></function>` calls a method at load time (e.g. to post-process data).

## x2many command tuples (one2many / many2many)

In XML use the tuple form via `eval`; in Python prefer the named `Command.*` from `odoo.fields`.

| Tuple | `Command` | Effect |
|---|---|---|
| `(6, 0, [ids])` | `Command.set([ids])` | replace whole set with these ids |
| `(4, id, 0)` | `Command.link(id)` | add existing record to the set |
| `(3, id, 0)` | `Command.unlink(id)` | remove from set, keep the record |
| `(0, 0, {vals})` | `Command.create({vals})` | create new record and link it |
| `(1, id, {vals})` | `Command.update(id, {vals})` | write `vals` onto a linked record |
| `(2, id, 0)` | `Command.delete(id)` | unlink **and delete** the record |
| `(5, 0, 0)` | `Command.clear()` | unlink all (keeps records) |

```xml
<field name="tax_ids" eval="[Command.set([ref('account.tax_15')])]"/>
<field name="line_ids" eval="[Command.create({'name': 'Row', 'qty': 1})]"/>
```

## CSV data files

- Filename **is** the model: `res.partner.csv`, `account.account.csv`.
- First column header `id` = external id (enables update-on-`-u` and `ref` from XML).
- Many2one by external id: column `field/id` (e.g. `parent_id/id`, `country_id/id`).
- Many2one by database id (dev only): `field/.id`. By display name: `field` (ambiguous; avoid).
- Booleans: `1`/`0` or `True`/`False`. Empty cell = not written.

```csv
id,name,parent_id/id,country_id/id,is_company
partner_acme,ACME Inc,,base.us,1
partner_acme_hq,ACME HQ,partner_acme,base.us,0
```

CSV loads faster than XML for bulk rows and is the convention for `ir.model.access.csv` (ACLs).

## __manifest__ data list — order is execution order

```python
'data': [
    'security/ir.model.access.csv',   # groups/ACLs first — later records may need them
    'data/ir_sequence.xml',
    'data/mail_template.xml',
    'views/partner_views.xml',         # actions/menus after the records they reference
],
'demo': ['demo/partner_demo.xml'],
```

Files load top-to-bottom in one transaction. A `ref()` to an xmlid defined later raises `External ID not found`. Common order: security → base data → sequences/params → views → menus.

## ir.sequence

Fields: `name`, `code` (lookup key for `next_by_code`), `prefix`, `suffix`, `padding` (zero-pad width), `number_next` / `number_next_actual` (next value; the `_actual` variant is the UI-safe computed wrapper), `number_increment`, `implementation`, `company_id`, `use_date_range`.

```xml
<record id="seq_membership" model="ir.sequence">
  <field name="name">Membership Ref</field>
  <field name="code">membership.ref</field>
  <field name="prefix">MEM/%(range_year)s/</field>
  <field name="padding">5</field>
  <field name="implementation">no_gap</field>
  <field name="company_id" eval="False"/>   <!-- global; set a company for per-company -->
</record>
```

```python
ref = self.env['ir.sequence'].next_by_code('membership.ref')   # 'MEM/2026/00001'; False if no seq
# on a specific record: seq.next_by_id()
```

- `implementation`: `'standard'` = Postgres sequence, fast, **may leave gaps** on rollback; `'no_gap'` = gap-free but serializes callers (slower under contention). Use `no_gap` only when law/audit needs it (e.g. fiscal invoice numbers).
- Prefix/suffix interpolation: `%(year)s %(month)s %(day)s %(y)s %(doy)s %(woy)s %(weekday)s %(h24)s %(min)s %(sec)s`, and `%(range_year)s` etc. when `use_date_range` is on.
- Call `next_by_code` once per record at create time (e.g. in `create()` override) — calling it advances the counter.

## Configuration: res.config.settings + ir.config_parameter

`ir.config_parameter` = key/value store. `res.config.settings` = the Settings UI on top of it.

```python
class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'
    # persisted to ir.config_parameter automatically:
    api_timeout = fields.Integer(config_parameter='my_module.api_timeout', default=30)
    # or mirror a company field:
    default_warehouse_id = fields.Many2one('stock.warehouse', related='company_id.default_warehouse_id', readonly=False)
```

```python
ICP = self.env['ir.config_parameter'].sudo()
ICP.set_param('my_module.api_timeout', 30)
timeout = int(ICP.get_param('my_module.api_timeout', default=30))   # ALWAYS returns str → cast
```

Ship defaults as `noupdate="1"` data so admins can change them without `-u` wiping the value:

```xml
<record id="param_timeout" model="ir.config_parameter">
  <field name="key">my_module.api_timeout</field>
  <field name="value">30</field>
</record>   <!-- inside <odoo noupdate="1"> -->
```

## Safely updating records the base already ships

1. Run `metadata.py`; check whether the target xmlid is `noupdate`.
2. **`noupdate=0`** → edit the XML and `-u`; the change re-asserts cleanly.
3. **`noupdate=1`** → `-u` won't touch it. Either change it in the UI (one-off), or for a fleet write a post-migration that writes the new value (see `odoo-migration`).
4. Never duplicate — extend the existing record by its xmlid; create a new one only if `metadata.py` shows none exists.

## i18n (brief)

- Wrap user-facing strings in code with `_()` (translated at call time) or `_lt()` (lazy, for module-level/class-attribute strings evaluated before a request).
- `_()` takes a literal; interpolate **after**: `_("Missing %s") % name`, never `_("Missing %s" % name)` — the latter can't be extracted/translated.
- Translations live in `<module>/i18n/<lang>.po` (+ `<module>.pot` template). Export via `--i18n-export`, or Settings → Translations.
- XML/CSV field values are the **source term**; translate through `.po`, never by shipping a record per language. Mark a model field translatable with `translate=True`.
