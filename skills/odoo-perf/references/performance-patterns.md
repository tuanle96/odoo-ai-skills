# Odoo performance patterns

Read alongside `trace_flow`'s `total_sql` / per-call `sql_count`. Optimize the measured hot path, not a guess.

## N+1: the dominant Odoo perf bug

The shape: a query (or compute that queries) runs once per record in a loop.

```python
# BAD — 1 + N queries (one search per order)
def _compute_delivered(self):
    for order in self:
        moves = self.env['stock.move'].search([('order_id', '=', order.id)])
        order.delivered = sum(moves.mapped('qty_done'))
```

```python
# GOOD — 2 queries total, regardless of N
def _compute_delivered(self):
    moves = self.env['stock.move'].search([('order_id', 'in', self.ids)])
    by_order = {}
    for m in moves:                       # moves already prefetched
        by_order.setdefault(m.order_id.id, 0.0)
        by_order[m.order_id.id] += m.qty_done
    for order in self:
        order.delivered = by_order.get(order.id, 0.0)
```

Better still when you only need an aggregate — push it to SQL via `_read_group` (next section).

## `_read_group` (v17 tuple API)

`read_group` is replaced by `_read_group(domain, groupby, aggregates, ...)` returning a **list of tuples**, one per group, in `groupby + aggregates` order. `'__count'` is the row count aggregate; others read `'field:agg'` (e.g. `'amount_total:sum'`). On **v18.2+** the public `read_group` is deprecated — use `_read_group` (tuples) internally, or `formatted_read_group` for the formatted public output. The field-level aggregate attribute is `aggregator=` since **v17.2** (`group_operator=` is the old name).

```python
# Count children per parent in ONE query:
data = self.env['stock.move']._read_group(
    [('order_id', 'in', self.ids)],
    groupby=['order_id'],
    aggregates=['__count', 'qty_done:sum'],
)
# data == [(order_rec, count, qty_sum), ...]   — group keys are real recordsets for m2o
counts = {order.id: cnt for order, cnt, _ in data}
sums   = {order.id: s   for order, _, s in data}
```

Pitfall: pre-17 code did `res[0]['order_id_count']` / `res[0]['__count']` on dicts. That silently breaks on v17 — the result is tuples now.

## Prefetch mechanics

- A recordset shares a **prefetch set**. Touching `recs[0].partner_id` loads `partner_id` for *all* of `recs` in one query, cached for the rest of the loop.
- `browse(id)` makes a singleton with its own prefetch set — looping `browse` per id defeats batching. Build/keep one recordset instead.
- `recs.mapped('partner_id.country_id')` prefetches along the whole dotted path in batches.
- Disable batching only deliberately with `recs.with_prefetch(...)` (rare).
- Don't pre-loop "to warm the cache" — a single `mapped`/dotted access already does it.

## Stored compute write-amplification

Each stored compute adds, to **every** write touching a dependency: re-run the Python + an extra `UPDATE` of the column (and cascades if other computes depend on it). On a hot model (e.g. `sale.order.line` written in bulk) this multiplies write cost.

Decision:

| Field needs to be… | store? |
|--------------------|--------|
| searched in a domain / filter | yes (+ index) |
| grouped or reported on (pivot, `_read_group`) | yes |
| shown in a list/form only | no — non-stored |
| filterable but expensive to store | no — non-stored + `_search_<field>` |

`@api.depends` must be **exhaustive**: list every path, dotted included. Compare your decorator against `model_brief.fields[<name>].depends` (the real registry value) — a missing path = silent staleness.

## Index decision matrix

| Field usage | Index |
|-------------|-------|
| FK used in `search` domains / joins | `index=True` |
| status/state grouped or filtered constantly | `index=True` |
| mostly-NULL optional m2o / boolean you filter on | `index='btree_not_null'` (partial — smaller, skips NULLs) |
| text searched with `ilike` / `like` | `index='trigram'` (needs `pg_trgm` extension) |
| display-only, never in a domain | none |
| non-stored compute | none possible (no column) — store first if required |

Indexes are not free: each adds write/disk cost. Index the columns your slow queries actually filter on (confirm via `EXPLAIN` or the SQL log), not every field.

## Raw SQL checklist

Only after the ORM is proven inadequate **and** measured:

```python
self.env.flush_all()                       # 1. push pending ORM writes to DB first
self.env.cr.execute(
    "SELECT partner_id, SUM(amount_total) FROM sale_order "
    "WHERE state = %s GROUP BY partner_id", ('sale',))   # 2. parameterized, never f-string
rows = self.env.cr.fetchall()
# ... if you also wrote rows directly:
self.invalidate_recordset(['amount_total'])  # 3. drop stale cache (or env.invalidate_all())
```

- Parameterize every value (`%s` + tuple) — string-formatting is SQL injection.
- Raw SQL bypasses ACL, record rules, computes, constraints — re-apply any that matter.
- Prefer `_read_group` / ORM domains until profiling proves they're the bottleneck.

## Reading `trace_flow` for SQL hotspots

`odoo-ai trace <model> <id> <method>` →

- `total_sql` — total queries the whole flow issued. A confirm that fires 400 queries is the smell.
- `calls[].sql_count` — SQL attributed to each call frame; sort desc to find the offending method/addon.
- `distinct_steps` — the cross-addon call order; a step repeated per line is your N+1 origin.

Cross-check with `--log-handler=odoo.sql_db:DEBUG` (see `odoo-debug`) when you need the exact statements and timings.

## Checklist

- [ ] No `search` / `browse` / write inside a loop over a recordset.
- [ ] Aggregations use `_read_group` (tuple API on v17); results not indexed by old dict keys.
- [ ] `store=True` only for searched/grouped/reported fields; `@api.depends` exhaustive vs the brief.
- [ ] Indexes only on columns the slow queries filter; typed index where it fits.
- [ ] Any raw SQL is parameterized, `flush_all()` before, `invalidate_recordset()` after.
- [ ] Measured `total_sql` before vs after — the count actually dropped.
