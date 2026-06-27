---
name: odoo-data
description: >-
  Shipping data with an Odoo module — XML/CSV data files, seeded records,
  ir.sequence numbering, and configuration via res.config.settings /
  ir.config_parameter. Use whenever adding records to data/ or demo/, writing
  <record>/<field> XML, wiring external IDs (ref/eval/command tuples), setting
  noupdate, ordering the __manifest__ data list, generating document numbers, or
  chasing "my edit reverts on -u" / "duplicate seeded record" bugs. Don't guess
  what's already seeded or whether a record is protected — read ground truth
  from ir.model.data via odoo-introspect's metadata script first.
---

# Odoo data, sequences & configuration

Data files are code: records load on `-i` and, unless protected, are **re-asserted from XML on every `-u`**. Two failures dominate — a seeded record's manual edits silently vanish on the next upgrade, or you create a second copy of a record the base already ships. Both come from guessing what exists and how it's flagged.

**The rule: before adding or editing shipped data, read what's already seeded and its `noupdate` flag from `ir.model.data` — don't guess.**

Targets Odoo 17/18, through Odoo 19 (current LTS). For older versions check `skills/odoo-introspect/references/version-matrix.md`.

## Discover first (introspect, don't grep)

Run `odoo-introspect`'s scripts against the live DB before touching data:

```bash
# Layer D — seeded external IDs for a model: owning module, xmlid, and noupdate.
#           noupdate_records is the list whose UI edits revert on -u.
MODEL=res.partner odoo-bin shell -d <DB> --no-http < scripts/metadata.py
# Layer A — field names/types, so record <field name> values aren't guessed.
MODEL=res.partner odoo-bin shell -d <DB> --no-http < scripts/model_brief.py
```

If `metadata.py` lists `module.xmlid` for what you're about to add, **reuse it with `ref()`** instead of creating a duplicate.

## data/ vs demo/ — when each loads

| Folder / manifest key | Loads on | Loaded on `-u` | Use for |
|---|---|---|---|
| `data` | every install/update | yes (unless `noupdate`) | real config: sequences, params, mail templates, ACL data |
| `demo` | only if DB created **with demo data** | yes, demo DBs only | sample records for dev/tests — never relied on in prod logic |

Demo never loads on a `--without-demo=all` database; production code must not depend on a demo record existing.

## noupdate — the silent reverter

`noupdate` controls what `-u` does to an already-created record. Get it wrong and edits disappear or your XML changes never apply.

| `noupdate` | On `-i` | On `-u` | Means |
|---|---|---|---|
| `0` (default) | created | **fields re-written from XML** | XML is source of truth; UI edits to it **revert on -u** |
| `1` | created | **untouched** | user-editable after install; your later XML edits **won't apply** either |

- Set per file/block: `<odoo noupdate="1">` or a scoped `<data noupdate="1">…</data>` inside a default `<odoo>`.
- Ship config you expect customers to change (sequences, `ir.config_parameter` defaults, templates) as `noupdate="1"`.
- To change a record that is already `noupdate="1"` on installed DBs, a `-u` won't do it — write a migration (see `odoo-migration`).
- `metadata.py` → `seeded_data.noupdate_records` tells you which records are protected before you try to patch them.

## Record & relation syntax

Full XML/CSV syntax, x2many command tuples, and the `Command.*` equivalents are in `references/data-and-sequences.md`. The traps:

- **Load order matters** — `ref('module.xmlid')` resolves only if that record's file is listed **earlier** in the manifest `data` list. Forward references raise `External ID not found`.
- **A file not in the `data` list silently does nothing** — no error, just no records.
- **CSV** — filename = model (`res.partner.csv`); header `id` = external id; many2one by external id uses the `field/id` column (e.g. `parent_id/id`).

## Sequences & configuration — pick the built-in

| Need | Use | Not |
|---|---|---|
| Document number (SO/INV/ref) | `ir.sequence` + `next_by_code('my.code')` | string formatting / `max(id)+1` |
| Guaranteed gap-free numbering | sequence `implementation='no_gap'` | `standard` (Postgres seq, may gap on rollback) |
| Admin-tunable setting | `res.config.settings` field w/ `config_parameter=` | hard-coded constant |
| Read a setting in code | `ir.config_parameter` `get_param/set_param` (sudo) | a custom singleton model |
| Per-company numbering/config | `company_id` on the sequence / param | one global value |

## Gotchas that fail silently

- **Editing a `noupdate="0"` record in the UI** → reverted on the next module `-u`. Mark it `noupdate="1"` or stop hand-editing shipped data.
- **`eval` vs `ref`** — `<field name="x" ref="m.id"/>` sets one m2o; multi/computed values need `eval="[Command.set([ref('m.id')])]"`. Mixing them silently writes the wrong thing.
- **`get_param` returns a string** — `'False'`, `'0'`, `'5'` are all truthy; cast before testing.
- **Translatable field values in XML** become the source term; translate via `.po`, never by shipping per-language records. Brief i18n notes in the reference.

## References & scripts

- `references/data-and-sequences.md` — full XML/CSV syntax, x2many command tuples + `Command.*`, `ir.sequence` fields & interpolation, `res.config.settings`/`ir.config_parameter` patterns, safe updates to shipped records, i18n note.
- Uses `odoo-introspect` scripts: `metadata.py` (Layer D — seeded ir.model.data + noupdate), `model_brief.py` (Layer A — field inventory).
- Changing already-installed protected data → `odoo-migration`. Proving `-i`/`-u` both load cleanly → `odoo-testing`.
