---
name: odoo-perf
description: >-
  Making Odoo fast — recordset hygiene (no query-in-loop, search-once-with-in,
  mapped/filtered/sorted), the ORM cache & prefetching, stored vs non-stored
  computed fields (write amplification, exhaustive @api.depends), database
  indexes (index=True and typed indexes), _read_group aggregation, batch
  create/write, and justified raw SQL with correct cache invalidation. Use
  whenever an Odoo page/list/report/cron is slow, an N+1 query pattern appears,
  you're about to add store=True or an index, or you're writing a loop that
  search()es or browses one record at a time. Measure real SQL counts from the
  running instance — don't guess. Targets Odoo 17/18.
---

# Odoo performance

In Odoo, wall-clock is dominated by **SQL query count**, not Python. The ORM already batches writes, prefetches relations, and caches reads across a whole recordset — almost all slowness is *fighting* it: a `search` or `browse` inside a loop turns one query into N. **Measure the real count first**, then fix the hot path. Delegate measurement to the **`odoo-introspect`** skill — `trace_flow` (Layer D) reports `total_sql` and per-call `sql_count`:

```bash
odoo-ai --db <DB> trace <model> <record_id> <method>   # total_sql + sql_count per call
```

**Version floor: Odoo 17/18.** The `_read_group` tuple API and the `flush_*` / `invalidate_*` cache methods are v16/17+; pre-16 uses `read_group` (dicts) and `flush()` / `invalidate_cache()` — see `skills/odoo-introspect/references/version-matrix.md`.

## Use the built-in, don't hand-roll it

| Need | Use | Not |
|------|-----|-----|
| Count / sum / group per key | `_read_group(domain, groupby, aggregates)` | `search` + count in a loop |
| Load N records' relations | one recordset + `mapped` / prefetch | `browse(id)` per row |
| Filter / sort already-loaded recs | `recs.filtered(...)`, `recs.sorted(...)` | re-`search` with a tweaked domain |
| Update many rows the same way | `recs.write({...})` (one UPDATE) | `for r in recs: r.write(...)` |
| Insert many rows | `create([vals, vals, ...])` | `for v in vals: create(v)` |
| Searchable / groupable derived value | stored compute **+ index** | recompute on every read |
| Display-only derived value | non-stored compute | `store=True` (write amplification) |

## Recordset hygiene (where performance dies)

- **Never query in a loop.** `for r in recs: env['x'].search([('y','=',r.id)])` is N queries. Search once — `env['x'].search([('y','in',recs.ids)])` — then group in Python with `filtered` / `mapped`.
- Set ops stay in cache: `recs.filtered(lambda r: r.state=='done')`, `recs.mapped('partner_id')`, `a | b`, `a - b`, `a & b`.
- Aggregate with `_read_group`, not per-record counting. **v17 returns a list of tuples** — e.g. `dict(model._read_group(domain, ['partner_id'], ['__count']))` — not the old list-of-dicts; don't index results by key name.
- `recs.write({...})` once batches a single UPDATE; field-by-field in a loop is N×fields writes.
- `create([...])` a list once (`@api.model_create_multi`) instead of looping `create`.

## The ORM cache & prefetch — why looping is slow

Reading a field on **one** record of a recordset triggers a prefetch of that field for the **whole** recordset in one query — so iterating a recordset is cheap, while `browse(id)` one-at-a-time defeats it (a query each). Keep records in a single recordset and let `mapped` / dotted access prefetch. Manage the cache explicitly only around out-of-band writes:

- `self.env.flush_all()` (or `self.flush_recordset([...])`) — push pending ORM writes to the DB, e.g. **before** raw SQL reads.
- `self.invalidate_recordset([...])` / `self.env.invalidate_all()` — drop cached values **after** changing rows behind the ORM's back.

## Stored vs non-stored computes

- `store=True` **only if you must search, group, or report** on the field. Stored computes add **write amplification**: every write touching a dependency re-runs the compute and writes the column.
- When stored, `@api.depends(...)` must name **every** path that can change the value, including dotted (`'line_ids.price_subtotal'`). Miss one ⇒ the stored value **silently goes stale** (no error). Audit against `model_brief.fields[].depends`.
- Non-stored computes cost nothing on write and recompute on read; add a `_search_<field>` if it must be filterable without storing.
- `related=` is a thin stored/non-stored compute — same rules.

## Indexes

- `index=True` → a btree index. Add it to fields you **search or group by often** (foreign keys used in domains, status fields). Indexes cost write time and disk — don't index everything.
- `index=` also accepts typed values: `'btree_not_null'` (partial index skipping NULLs — ideal for a mostly-empty optional m2o/bool), and `'trigram'` (for `like` / `ilike` text search; requires the PostgreSQL `pg_trgm` extension). `index=True` is `'btree'`.
- A non-stored computed field has **no column**, so it can't be indexed — store it first if it must be.

## Raw SQL — only when justified

Reach for `self.env.cr.execute` only when the ORM genuinely can't express it (heavy aggregation / window functions) and you've measured the win. Then:

1. **Parameterize** — `cr.execute("... WHERE id = %s", (rec.id,))`, never an f-string (SQL injection).
2. `self.env.flush_all()` **before** reading, so pending ORM writes are in the DB.
3. `self.invalidate_recordset([...])` (or `self.env.invalidate_all()`) **after** writing, so the ORM doesn't serve stale values.

Raw SQL bypasses ACL, record rules, computes, and constraints — you now own all of them.

## Gotchas that fail silently

- **Incomplete `@api.depends` on a stored compute** — the value silently rots; surfaces months later as "wrong total". The brief lists the real `depends` to audit.
- **`store=True` "just in case"** — every dependency write now recomputes; on a hot model this is a real regression with no error. Store only to search/group/report.
- **N+1 hidden behind a compute** — a *non-stored* compute that `search`es makes every row in a list view its own query. Catch it with `--log-handler=odoo.sql_db:DEBUG` or `trace_flow`.
- **`mapped()` on a non-stored field still computes it** — `mapped` avoids *queries* for stored/related fields but runs a non-stored compute per record; know which you have.
- **Raw SQL without `invalidate_recordset`** — ORM serves stale cached values, then `CacheMiss` (see `odoo-debug`).
- **`_read_group` return shape (v17)** — tuples, not dicts; old `[d['field'] for d in res]` code silently breaks after upgrade.

## References & related skills

**This skill's reference**
- `references/performance-patterns.md` — N+1 before/after, prefetch mechanics, `_read_group` v17 recipes, stored-compute write-amplification math, the index decision matrix, the raw-SQL checklist, and reading `trace_flow` SQL counts.

**Other skills in the loop**
- `odoo-introspect` — Tier 0 engine; `trace_flow` for real `total_sql` / `sql_count`, `model_brief` for `depends` / `store` / index facts.
- `odoo-debug` — SQL logging (`--log-handler=odoo.sql_db:DEBUG`), `CacheMiss` decoding.
- `odoo-dev` — choosing the right hook so you don't hand-roll what the ORM batches.
