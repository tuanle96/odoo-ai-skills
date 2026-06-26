# Playbook: Sales (`sale`)

**Map, not truth — Odoo 17/18.** Confirm every name against the instance first:

```bash
odoo-ai --db <DB> all sale.order --methods action_confirm,_action_confirm,_create_invoices,_prepare_invoice
odoo-ai --db <DB> all sale.order.line --methods _prepare_invoice_line,_prepare_procurement_values,_action_launch_stock_rule
odoo-ai --db <DB> trace sale.order <id> action_confirm   # see the real sale→stock→account graph
```

## 1. Key models

| Model | Role | Needs module |
|-------|------|--------------|
| `sale.order` | Quotation/order header; state draft→sent→sale→done/cancel | `sale` |
| `sale.order.line` | Order lines; qty, price, `qty_delivered`, `qty_invoiced` | `sale` |
| Delivery side (`_action_launch_stock_rule`, route fields) | Procurement → `stock.move` | **`sale_stock`** |
| Templates / optional & down-payment lines | quote tooling | `sale_management` |

The single most important fact: **`sale` alone never makes a delivery.** Procurement/delivery methods exist only when `sale_stock` is installed — depend on `sale_stock` (not `sale`) if you touch them, or your override lands at the wrong MRO layer (or the method isn't there at all).

## 2. Read-first methods (the workhorses, not the buttons)

| Method | Model | What it really does |
|--------|-------|---------------------|
| `action_confirm` | `sale.order` | Thin button shell: state→`sale`, then calls `_action_confirm` |
| `_action_confirm` | `sale.order` | The real work; `sale_stock` extends it to launch stock rules |
| `_action_launch_stock_rule` | `sale.order.line` | Creates procurement → stock moves *(verify: `sale_stock` only)* |
| `_prepare_procurement_values` | `sale.order.line` | Values fed to the stock rule (route, date, group) |
| `_create_invoices(grouped, final, date)` | `sale.order` | Builds `account.move`; invoked by the `sale.advance.payment.inv` wizard, not a plain button |
| `_prepare_invoice` | `sale.order` | Invoice **header** vals dict |
| `_prepare_invoice_line` | `sale.order.line` | Invoice **line** vals dict |
| `_get_invoiceable_lines` | `sale.order` | Which lines are billable right now |

## 3. Right extension hook

| You want to… | Hook (prefer) | Not |
|--------------|---------------|-----|
| Add fields to the created invoice | `_prepare_invoice` (header) / `_prepare_invoice_line` (line) | override `_create_invoices` |
| Change delivery/procurement values | `_prepare_procurement_values` | `_action_launch_stock_rule` |
| React to confirmation | extend `_action_confirm` (call `super()`) | `action_confirm` (button shell) |
| Derive a value on the line | computed field / `related=` | code in `write` |
| Block confirmation | `@api.constrains` or early-return in `_action_confirm` after `super()` | UI-only `invisible` |

## 4. Famous gotchas

- **`sale` vs `sale_stock` layering** — delivery fields and `_prepare_procurement_values` simply don't exist without `sale_stock`. Wrong `depends` = wrong MRO layer = your `super()` chains to the wrong method. Confirm with `model_brief`'s MRO/`manifest_depends`.
- **Invoice policy decides "nothing to invoice"** — `invoice_policy` on `product.template` (`order` vs `delivery`) controls what `_get_invoiceable_lines` returns. Delivery-policy products aren't invoiceable until `qty_delivered` > 0.
- **Invoicing runs through a wizard** — `_create_invoices` is called by `sale.advance.payment.inv`, not a direct button. Trace from the wizard, not the order.
- **Don't write amount fields** — `amount_total`/`amount_tax` are stored computes (`_compute_amounts`, *verify exact name*). Touch the lines, let it recompute.
- **Down payments are special lines** — `is_downpayment` lines have no product delivery; filter them in custom line logic.
- **Multi-step delivery** (pick-pack-ship) changes the entire stock side — one SO can spawn several pickings. See `references/stock.md` and trace it.
