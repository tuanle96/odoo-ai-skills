# Playbook: Purchase (`purchase`) + HR note

**Map, not truth — Odoo 17/18.** Confirm first:

```bash
odoo-ai --db <DB> all purchase.order --methods button_confirm,_create_picking,_prepare_picking,_create_invoices,_prepare_invoice
odoo-ai --db <DB> all purchase.order.line --methods _prepare_stock_moves,_prepare_account_move_line
odoo-ai --db <DB> trace purchase.order <id> button_confirm   # PO → receipt (and approval routing)
```

## 1. Key models

| Model | Role | Needs module |
|-------|------|--------------|
| `purchase.order` | RFQ → PO; state draft→sent→to approve→purchase→done/cancel | `purchase` |
| `purchase.order.line` | Lines; `qty_received`, `qty_invoiced` | `purchase` |
| Receipt side (`_create_picking`, `_prepare_picking`) | PO → `stock.picking` | **`purchase_stock`** |

As with sale: **bare `purchase` makes no receipt.** `_create_picking`/`_prepare_picking` exist only with `purchase_stock`. Depend on it, not `purchase`, if you touch receipts.

## 2. Read-first methods (the workhorses, not the buttons)

| Method | Model | What it really does |
|--------|-------|---------------------|
| `button_confirm` | `purchase.order` | Confirm shell: may route to `to approve` (double validation) or `purchase`; triggers receipt creation |
| `_create_picking` / `_prepare_picking` | `purchase.order` | Generate the receipt + its header vals *(verify: `purchase_stock`)* |
| `_prepare_stock_moves(picking)` | `purchase.order.line` | Receipt move vals |
| `_create_invoices` / `_prepare_invoice` | `purchase.order` | Vendor bill (`account.move`, `in_invoice`) header |
| `_prepare_account_move_line(move)` | `purchase.order.line` | Vendor bill line vals *(verify exact name)* |
| `button_cancel` / `button_draft` | `purchase.order` | State transitions with unwinding |

## 3. Right extension hook

| You want to… | Hook (prefer) | Not |
|--------------|---------------|-----|
| Values on the receipt | `_prepare_picking` / `_prepare_stock_moves` | `button_confirm` |
| Values on the vendor bill | `_prepare_invoice` / `_prepare_account_move_line` | `_create_invoices` |
| React to confirmation | extend `button_confirm` (call `super()`) | overwrite it |
| Approval workflow | double-validation **config** (`po_double_validation`, amount threshold) | hardcoded state checks |

## 4. Famous gotchas

- **`button_confirm` may NOT reach `purchase`** — under double validation it goes to `to approve` first. Never assume the post-confirm state; read it back.
- **Bill control policy decides what's billable** — `purchase_method` on the product (`receive` vs `purchase`/order). With `receive`, `qty_received` (driven by the receipt) gates `qty_invoiced`. "Nothing to bill" usually means the receipt isn't done.
- **`purchase_stock` layering** — receipt methods absent without it; wrong `depends` = wrong MRO layer.
- **3-way match** — receipts feed bill quantities; trace `button_confirm` then the receipt validation to see the full purchase→stock→account chain.

---

## HR note (brief — `hr`, `hr_holidays`)

| Model | Role |
|-------|------|
| `hr.employee` | Employee record — **not** `res.users`; an employee may have no login, and is per-company |
| `hr.leave` | A time-off request |
| `hr.leave.allocation` | Granted leave balance |
| `resource.calendar` (+ `.leaves`) | Working schedule & public holidays — drives leave duration |

**Read-first / hooks:**

| Method | Model | Note |
|--------|-------|------|
| `action_confirm` → `action_approve` → `action_validate` | `hr.leave` | Approval chain; steps depend on `leave_validation_type` (`no_validation`/`hr`/`manager`/`both`) |
| `action_refuse` | `hr.leave` | Reject |
| `_compute_number_of_days` / `number_of_days` | `hr.leave` | Duration from the employee's `resource.calendar`, minus public holidays *(verify name)* |

**Gotchas:** leave duration is **not** calendar days — it comes from the employee's working calendar and `resource.calendar.leaves` (holidays). Approval steps are configurable per leave type, so **don't hard-code states**. Employee ≠ user: link is optional and multi-company-sensitive. Introspect `hr.leave` before automating approvals.
