# N+1 & prefetch — find the hotspot, don't guess

Read alongside `trace`'s `summary.call_counts` + `summary.top_self_sql`. Locate the hot frame from the trace first; the full fix catalog is in `performance-patterns.md`. Optimize the measured path, never a hunch.

## The recordset & prefetch model (why looping is the bug)

A recordset is a set of ids sharing one **prefetch set** and one **cache**. Reading a field on *any* one record loads that field for the *whole* set in a single query, cached for the rest of the loop:

```python
orders = env['sale.order'].search([...])   # 1 query for the ids
orders[0].partner_id                        # 1 query → partner_id for ALL orders, now cached
for o in orders: o.partner_id.name          # 0 extra queries (prefetched)
```

`browse(id)` makes a **singleton** with its own prefetch set — so calling `browse` per id in a loop defeats batching, one query each. Keep records in one recordset; let dotted access / `mapped` prefetch. Every N+1 bug is some variant of *forcing one record's worth of work N times*.

## Find it in the trace before touching code

`odoo-ai --db <DB> trace <model> <id> <method>` runs the real path on a real record (rolls back) and hands you a scan-first digest:

- **`summary.call_counts`** — most-invoked `(model, method, addon)` pairs. A method called once per line/row (`count: 14` on a 14-line order) is the N+1 fingerprint — the loop is *calling* something N times.
- **`summary.top_self_sql`** — frames doing the most SQL **themselves** (`self_sql` = cumulative − children), so a thin parent never masks the real culprit. The top entry's `line` / `addon` point straight at the offending source.
- **`total_sql`** — the whole-flow count. 88 queries to confirm one order is the smell; `top_self_sql` shows which frame owns them.
- `distinct_steps` — the cross-addon call order; a step that recurs per row is the N+1 origin.

`call_counts` tells you *what* repeats; `top_self_sql` tells you *where the cost is*. The fix follows from which one lights up — you never guess the hot method.

## N+1 patterns → what the trace shows → the fix

| Pattern (the anti-code) | In the trace | Fix |
|---|---|---|
| `search` / `read` per record in a loop | a `search` method high in `call_counts` | search once with `('x','in',recs.ids)`, group in Python |
| `browse(id)` per id | singleton reads, high `total_sql` | build one recordset, iterate it |
| compute that `search`es, on a list view | the compute method's `count` ≈ row count | batch the compute (`'in', self.ids`) or store + index |
| per-record counting / summing | many `read` / `search_count` calls | one `_read_group` aggregate |

```python
# BAD — search per order (N+1)
for o in self:
    o.move_count = env['stock.move'].search_count([('order_id', '=', o.id)])
# GOOD — one grouped query
counts = dict(env['stock.move']._read_group(
    [('order_id', 'in', self.ids)], ['order_id'], ['__count']))   # {order_rec: count}, v17 tuples
for o in self:
    o.move_count = counts.get(o, 0)
```

## `.mapped()` vs a Python loop

`recs.mapped('partner_id')` / `recs.mapped('line_ids.price_subtotal')` prefetch along the whole dotted path in batches — almost always fewer queries than a hand loop. But the field type decides whether it's free:

- **stored / related field** → `mapped` avoids queries (prefetched). Prefer it.
- **non-stored compute** → `mapped` still **runs the compute once per record**; it saves no work, just reads cleaner. If that compute itself queries, `mapped` is N+1 in disguise — the trace shows the compute method's `count` spiking.

So `mapped` is a prefetch / readability win, not a magic aggregator. For counts and sums, push to `_read_group` (one SQL) instead of `mapped` + a Python `sum`.

## Aggregates: `_read_group` (real v17/18/19 API)

`read_group` → **`_read_group(domain, groupby, aggregates, having=(), ...)`** returns a **list of value-tuples**, one per group, in `groupby + aggregates` order. `'__count'` is the row-count aggregate; others are `'field:agg'` (e.g. `'amount_total:sum'`). Group keys for an m2o come back as **real recordsets**, so they key a dict directly.

```python
data = env['sale.order.line']._read_group(
    [('order_id', 'in', self.ids)], ['order_id'], ['__count', 'price_subtotal:sum'])
# data == [(order_rec, count, subtotal_sum), ...]
totals = {order.id: s for order, _, s in data}
```

- **v18.2:** public `read_group` is deprecated — use `_read_group` (tuples) internally, or `formatted_read_group` for the formatted public output.
- **v17.2:** the field-level aggregate attribute is **`aggregator='sum'`** (`group_operator=` is the old name).
- Pitfall: pre-17 code did `res[0]['__count']` on dicts — that silently breaks on v17 (it's tuples now).

## Stored vs computed cost & index — the quick decision

- `store=True` adds **write amplification**: every write touching a dependency re-runs the compute *and* writes the column. Store only to **search / group / report** on the value; otherwise non-stored (free on write, recompute on read).
- A stored field you filter or group on wants `index=True`; a mostly-NULL optional FK wants `index='btree_not_null'`; `ilike` text search wants `index='trigram'` (needs `pg_trgm`). A non-stored compute has **no column** — it can't be indexed; store it first if it must be.
- (Full write-amplification math + the index matrix live in `performance-patterns.md`.)

## flush / invalidate — the cache pitfalls

The ORM holds writes in cache and flushes lazily. You only manage it around **out-of-band** access (raw SQL, or rows changed behind the ORM's back):

```python
self.env.flush_all()                  # push pending ORM writes to DB BEFORE a raw SELECT
self.env.cr.execute("SELECT ... WHERE id = %s", (rec.id,))   # parameterized, never an f-string
# ... if you wrote rows directly with SQL:
self.invalidate_recordset(['amount_total'])   # drop stale cache AFTER (or env.invalidate_all())
```

- **Raw SELECT without `flush_all()` first** → you read stale DB rows (pending ORM writes aren't there yet).
- **Raw UPDATE without `invalidate_recordset()` after** → the ORM keeps serving the old cached value, then a later read mismatches (`CacheMiss` / wrong total — see `odoo-debug`).
- **v16/17+** uses `flush_all()` / `flush_recordset()` / `invalidate_all()` / `invalidate_recordset()`; pre-16 had the single `flush()` / `invalidate_cache()`.

## Checklist

- [ ] Read `call_counts` (what repeats) + `top_self_sql` (where the cost is) before editing — hotspot identified, not guessed.
- [ ] No `search` / `browse` / write inside a loop over a recordset.
- [ ] `mapped` only for stored / related; a querying non-stored compute under `mapped` is still N+1.
- [ ] Aggregates use `_read_group` (v17 tuples); results not indexed by old dict keys; `aggregator=` on the field.
- [ ] `store=True` only to search / group / report; index only the columns slow queries filter.
- [ ] Raw SQL: `flush_all()` before, `invalidate_recordset()` after, parameterized.
- [ ] Re-ran `trace` — `total_sql` actually dropped.
