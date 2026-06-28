# noupdate, sequences & config lifecycle — reference

Targets Odoo 17/18, through Odoo 19 (current LTS). Older versions: `skills/odoo-introspect/references/version-matrix.md`. Companion to `data-and-sequences.md` (authoring syntax) — this file is the **lifecycle**: what `-i` vs `-u` do to shipped data, what's protected, and how to verify before you edit.

## noupdate — loaded once, then frozen

A `<record>` is created on **install** (`-i`). What `-u` does to it afterwards depends entirely on `noupdate`:

| `noupdate` | `-i` | `-u` | Consequence |
|---|---|---|---|
| `0` (default) | created | **re-asserted from XML** | XML is source of truth; UI / runtime edits **revert** on every `-u` |
| `1` | created | **untouched** | created once, then frozen — your **later XML edits never apply** |

The `noupdate=1` trap is the quiet one: you ship a record, later change its XML, run `-u`, and nothing happens — Odoo loaded it once on the original install and will not touch it again. Existing databases keep the **old** values; only **fresh** installs get the new XML. Fresh-install tests pass while the upgrade silently does nothing. (This is the data-side of high-risk playbook #4: an upgrade applies cleanly yet leaves protected rows unchanged.)

- Scope it: `<odoo noupdate="1">…</odoo>` for a whole file, or `<data noupdate="1">…</data>` for a block inside a default `<odoo>`.
- Ship as `noupdate="1"` anything you expect admins to edit at runtime — sequences, `ir.config_parameter` defaults, mail templates — so `-u` doesn't wipe their changes.
- Ship as `noupdate="0"` (default) anything the module must keep authoritative — ACL data, view definitions, structural records.

## Verify what's seeded & protected before editing (`odoo-ai metadata`)

Don't grep the source tree and guess — read `ir.model.data` from the live instance:

```bash
odoo-ai --db <DB> metadata <model>
```

`seeded_data.noupdate_records` lists the protected external IDs in `"<module>.<xmlid> (res_id=<id>)"` form, e.g. `"sale.sale_order_rule_personal (res_id=33)"`; `by_module` and `sample` show who seeded what. If your target xmlid appears in `noupdate_records`, a `-u` will **not** apply your XML change — you need a migration (below). If `metadata` already lists an xmlid for a record you were about to add, **reuse it with `ref()`** instead of creating a duplicate.

## Changing protected data — the migration path (playbook #4)

For a `noupdate="1"` record on databases that already have it, editing the XML is a no-op on `-u`. To actually change installed rows:

1. `odoo-ai --db <DB> metadata <model>` → confirm the xmlid is in `noupdate_records`.
2. Write a **post-migration** that resolves the xmlid and writes the new value:
   ```python
   # <module>/migrations/<version>/post-migrate.py
   def migrate(cr, version):
       from odoo import api, SUPERUSER_ID
       env = api.Environment(cr, SUPERUSER_ID, {})
       rec = env.ref('my_module.my_seeded_record', raise_if_not_found=False)
       if rec:
           rec.value = 'new'
   ```
3. Bump the module version in `__manifest__.py` so `-u` runs the migration. (Field rename/drop on `-u` is the other half of playbook #4 — `odoo-ai upgrade-check <model> --against <old_brief>` classifies rename vs drop and flags noupdate records needing a migration; see `odoo-migration`.)

A one-off on a single DB can instead be edited in the UI — but a fleet needs the migration.

## ir.sequence — generated numbers

```xml
<record id="seq_membership" model="ir.sequence">
  <field name="name">Membership Ref</field>
  <field name="code">membership.ref</field>          <!-- lookup key for next_by_code -->
  <field name="prefix">MEM/%(range_year)s/</field>
  <field name="padding">5</field>                     <!-- zero-pad width -->
  <field name="implementation">no_gap</field>
  <field name="company_id" eval="False"/>             <!-- global; set a company for per-company -->
</record>
```

```python
ref = self.env['ir.sequence'].next_by_code('membership.ref')   # 'MEM/2026/00001'; False if no seq
```

- **`next_by_code(code)`** is the public call (it **advances** the counter — call it **once** per record, e.g. in a `create()` override). `seq.next_by_id()` does the same on a specific `ir.sequence` record; both wrap the internal `_next()`.
- **`implementation`**: `'standard'` (Postgres sequence, fast, **may gap** on rollback) vs `'no_gap'` (gap-free but serializes callers — use only when law/audit demands it, e.g. fiscal invoice numbers).
- **Per-company**: set `company_id` on the sequence; with `use_date_range` Odoo keeps a sub-sequence per date range. A global sequence (`company_id = False`) shares one counter across companies — usually wrong for legal document numbers.
- `padding`, `prefix` / `suffix` interpolation (`%(year)s`, `%(range_year)s`, …): see `data-and-sequences.md`.

## ir.config_parameter vs res.config.settings

Two layers, often confused:

| | `ir.config_parameter` | `res.config.settings` |
|---|---|---|
| What | key/value store (one row per key) | the **Settings UI** (a `TransientModel`) on top of it |
| Persistence | the actual stored value | **transient** — nothing persists by itself; fields with `config_parameter=` write through to `ir.config_parameter` on save |
| Read in code | `env['ir.config_parameter'].sudo().get_param(key, default)` | don't — read the underlying param |
| Ship a default | `<record model="ir.config_parameter">` inside `<odoo noupdate="1">` | a field `default=` |

```python
# on res.config.settings — the field is a façade over the param:
api_timeout = fields.Integer(config_parameter='my_module.api_timeout', default=30)
# reading anywhere in code:
ICP = self.env['ir.config_parameter'].sudo()
timeout = int(ICP.get_param('my_module.api_timeout', default=30))   # ALWAYS a str → cast
```

- `get_param` **always returns a string** — `'False'`, `'0'`, `'5'` are all truthy; cast before testing.
- A `res.config.settings` field is **not** state storage — it's a façade; the value lives in `ir.config_parameter` (or a company field via `related=`).
- Ship config-param defaults as `noupdate="1"` so admins can change them without the next `-u` resetting the value (ties back to the noupdate table above).

## Command.* / x2many writes

Writing one2many / many2many in data or Python uses command tuples; prefer the named **`Command.*`** from `odoo.fields` (clearer, identical wire format):

| Tuple | `Command` | Effect |
|---|---|---|
| `(6, 0, [ids])` | `Command.set([ids])` | replace the whole set |
| `(4, id, 0)` | `Command.link(id)` | add an existing record |
| `(0, 0, {vals})` | `Command.create({vals})` | create + link a new record |
| `(3, id, 0)` | `Command.unlink(id)` | remove from set, keep the record |
| `(2, id, 0)` | `Command.delete(id)` | unlink **and** delete |

```xml
<field name="tax_ids" eval="[Command.set([ref('account.tax_15')])]"/>
<field name="line_ids" eval="[Command.create({'name': 'Row', 'qty': 1})]"/>
```

`Command` is in scope in XML `eval=` and importable in Python (`from odoo.fields import Command`). A bare m2o uses `ref=`; only multi-value / computed sets need `eval` + `Command` — mixing them silently writes the wrong thing. Full tuple table: `data-and-sequences.md`.
