# Playbook: Inventory (`stock`)

**Map, not truth — Odoo 17/18/19.** Confirm first — and read the v17 field rename below before any move-line code:

```bash
odoo-ai --db <DB> all stock.move --methods _action_confirm,_action_assign,_action_done,_action_cancel,_get_new_picking_values
odoo-ai --db <DB> all stock.picking --methods action_confirm,action_assign,button_validate,_action_done
odoo-ai --db <DB> trace stock.picking <id> button_validate   # reservation + quant updates, end to end
```

## 1. Key models

| Model | Role |
|-------|------|
| `stock.picking` | Transfer (a document grouping moves): receipt / delivery / internal |
| `stock.move` | One product's planned movement; the unit of business logic |
| `stock.move.line` | The *operational* detail: actual qty, lot/serial, source/dest |
| `stock.quant` | On-hand & reserved quantity per (product, location, lot) |
| `stock.rule` / `stock.route` | Config that decides *how* moves chain (MTO, multi-step) — data, not code |

## 2. Read-first methods (the workhorses, not the buttons)

| Method | Model | What it really does |
|--------|-------|---------------------|
| `action_confirm` → `_action_confirm` | `stock.picking`/`stock.move` | Confirm; merge moves; create chained/onward moves; assign to pickings |
| `action_assign` → `_action_assign` | picking → `stock.move` | **Reservation**: writes `stock.move.line`, reserves quants |
| `button_validate` | `stock.picking` | Validate shell → `_action_done` (may pop a backorder/immediate-transfer wizard) |
| `_action_done` | `stock.move` | Mark done; update quants; propagate |
| `_action_cancel` | `stock.move` | Cancel & unwind propagation |
| `_get_new_picking_values` / `_search_picking_for_assignation` / `_assign_picking` | `stock.move` | How loose moves group into a picking |
| `_update_reserved_quantity` / `_get_available_quantity` | `stock.quant` | Low-level reservation math *(verify exact name in v18)* |

## 3. Right extension hook

| You want to… | Hook (prefer) | Not |
|--------------|---------------|-----|
| Control how moves group into pickings | `_get_new_picking_values` / `_search_picking_for_assignation` | `action_confirm` |
| Set values on an auto-created picking | `_get_new_picking_values` | post-hoc `write` |
| React to validation | extend `_action_done` (call `super()`) | `button_validate` shell |
| Add a delivery/receipt step | routes & rules **config** (data) | hand-coded extra moves |
| Custom reservation rule | `_action_assign` (carefully) — first try route/rule config | procedural code in `write` |

## 4. Famous gotchas

- **v17 field rename (CRITICAL, silent breakage).** In Odoo 17+ `stock.move.line.qty_done` and the reserved `product_uom_qty` are **gone**. Now: `quantity` (the processed/done qty) + `picked` (boolean) + `quantity_product_uom`. Code written for ≤16 using `qty_done` fails or no-ops silently. **Always confirm move-line field names via `model_brief` before writing.**
- **Reservation depends on `procure_method`** — a `make_to_order` (MTO) move never reserves from stock; it waits on its upstream move. Don't "fix" reservation that's working as designed.
- **Lots / serials** — `tracking` on `product.template` (`lot`/`serial`) forces `stock.move.line.lot_id`/`lot_name`. Setting `quantity` done without a lot raises. Serial = qty 1 per line.
- **Moves merge** — `_action_confirm` calls `_merge_moves`; identical moves collapse, so per-move custom data can be merged away. Put distinguishing data where it blocks the merge, or hook after.
- **Multi-step routes** — pick-pack-ship / 2-3-step receipt create *several* pickings per order, chained by `move_orig_ids`/`move_dest_ids`. `trace_flow` is the only reliable way to see them all.
- **Unreserve** is `do_unreserve` (picking/move), not editing quants directly.
