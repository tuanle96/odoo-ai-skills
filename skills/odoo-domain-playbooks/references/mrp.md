# Playbook: Manufacturing (`mrp`)

**Map, not truth — Odoo 17/18.** Move generation names vary by version — verify before overriding:

```bash
odoo-ai --db <DB> all mrp.production --methods _action_confirm,button_mark_done,_get_moves_raw_values,_get_moves_finished_values
odoo-ai --db <DB> all mrp.bom --methods explode,_bom_find
odoo-ai --db <DB> trace mrp.production <id> button_mark_done   # component consumption + finished production
```

## 1. Key models

| Model | Role |
|-------|------|
| `mrp.production` | Manufacturing order (MO); state draft→confirmed→progress→done |
| `mrp.bom` / `mrp.bom.line` | Bill of materials; components & quantities |
| `stock.move` via `move_raw_ids` | Component (consumed) moves |
| `stock.move` via `move_finished_ids` | Finished product (produced) moves; byproducts in `move_byproduct_ids` |
| `mrp.workorder` | Per-operation steps — **only with `mrp_workorder`**; different entry points |

## 2. Read-first methods (the workhorses, not the buttons)

| Method | Model | What it really does |
|--------|-------|---------------------|
| `_action_confirm` | `mrp.production` | Confirm MO; generate raw/finished moves from the BoM |
| `_get_moves_raw_values` / `_get_moves_finished_values` | `mrp.production` | Build component / finished `stock.move` vals *(verify exact names)* |
| `_generate_moves` | `mrp.production` | Orchestrates the two above *(verify; version-variant)* |
| `explode(product, qty)` | `mrp.bom` | Recursive BoM explosion → `(boms, lines)`; handles phantom/kit nesting |
| `_bom_find(products, ...)` | `mrp.bom` | Picks the applicable BoM |
| `button_mark_done` → `_post_inventory` | `mrp.production` | Consume components, produce finished, update quants *(internal name `_button_mark_done` in some versions — verify)* |
| `_split_productions` / backorder wizard | `mrp.production` | Partial production → backorder MO |

## 3. Right extension hook

| You want to… | Hook (prefer) | Not |
|--------------|---------------|-----|
| Change component move values | `_get_moves_raw_values` | `_generate_moves` wholesale |
| Change finished move values | `_get_moves_finished_values` | post-hoc `write` |
| Custom BoM selection | `_bom_find` | hardcode a `bom_id` |
| React to completion | extend `_post_inventory` / `button_mark_done` (call `super()`) | the button shell alone |
| Component qty / consumption rule | BoM config (`consumption` flexible/strict) + `_get_moves_raw_values` | procedural `write` |
| Block "mark done" | `@api.constrains` or early-return after `super()` | UI `invisible` |

## 4. Famous gotchas

- **BoM explosion is recursive** — phantom/kit BoMs explode into sub-components; `explode` returns the fully-nested line set. Per-line assumptions that ignore nesting break on kits.
- **Backorders run your code twice** — marking done with a partial `qty_producing` spawns a backorder MO (via the backorder wizard); post-done logic may fire per-MO, including the backorder.
- **Component reservation = stock `_action_assign`** — `move_raw_ids` reserve exactly like any `stock.move`; the same lot/serial rules and the v17 `quantity`/`picked` field rename apply (see `references/stock.md`).
- **`qty_producing` drives the done qty** at mark-done; consumption is proportional unless the BoM uses strict consumption. Don't assume full-quantity completion.
- **Byproducts are separate** — `move_byproduct_ids` ≠ `move_finished_ids`; handle both if relevant.
- **`mrp_workorder` changes everything** — if installed, work is driven through workorders (`button_start`, `record_production`), not just `button_mark_done`. Introspect to see which entry points exist.
