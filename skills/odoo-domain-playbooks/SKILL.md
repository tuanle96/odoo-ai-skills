---
name: odoo-domain-playbooks
description: >-
  Customizing or extending a STANDARD Odoo business app — sale (quotation →
  order → delivery → invoice), stock / inventory (pickings, moves, reservation,
  lots), account / accounting (invoices, bills, taxes, posting), mrp /
  manufacturing (BoM, production, components), purchase (RFQ → PO → receipt →
  bill), or hr (employees, time-off). Use whenever the work touches one of these
  domains, even if the user doesn't say the word "skill" — any time you'd
  otherwise GUESS which method to override, which hook builds the next document,
  or how the sale→stock→account chain fires. Each app here is a STARTING MAP of
  the key models, the methods to read first, and the right extension hook — NOT
  ground truth. Confirm every name against the running instance with the
  `odoo-introspect` skill before you write a line.
---

# Odoo domain playbooks

**Version floor: Odoo 17 / 18.** Method and field names below are the v17/18 standard; v16 and earlier differ (notably the v17 `stock.move.line` field rename — see `references/stock.md`).

A playbook is a **map, not the territory.** These "standard" apps are not one fixed thing — what exists in *this* database depends entirely on which modules are installed:

- `sale` alone has no delivery; `sale_stock` adds the stock chain; `sale_management` adds templates/optional products. The procurement methods you want to hook **only exist with `sale_stock`**.
- `account` localizations (`l10n_*`) heavily extend `_post` and add country-specific constraints — your override lands *among* them, at an MRO layer you can't guess.
- `mrp` vs `mrp_workorder`, `purchase` vs `purchase_stock` — same story: different methods, different entry points.

So the playbook tells you **where to look and what to read** — never what to type from memory. The field list, the MRO, the real `super()` chain, and the cross-model flow exist only in the running registry.

## The universal move (before customizing ANY app)

Run these two via the `odoo-introspect` skill's `odoo-ai` CLI, feed the JSON to the agent, *then* plan the patch:

```bash
# 1. model_brief (Layer A+B): fields, MRO + super-analysis, buttons/views, security — for the
#    key model and the methods this app's playbook tells you to read first.
odoo-ai --db <DB> all <key.model> --methods <m1>,<m2>,<m3>

# 2. trace_flow (Layer C): execute the entry method on a throwaway record and record the REAL
#    cross-model call sequence (sale→stock→account, mrp→stock, purchase→stock→account).
odoo-ai --db <DB> trace <key.model> <record_id> <entry_method>
```

MRO alone is never enough for these flows — they are graphs across many models that fire procurement, stock moves, and invoice hooks. **Always trace before touching a business flow.** No shell (SaaS)? Use the RPC fallback in the `odoo-introspect` skill.

## App → playbook map

| App / domain | Key models | Playbook | Entry method to trace first |
|--------------|-----------|----------|------------------------------|
| Sales | `sale.order`, `sale.order.line` | `references/sale.md` | `action_confirm` |
| Inventory | `stock.picking`, `stock.move`, `stock.move.line`, `stock.quant` | `references/stock.md` | `button_validate` / `_action_assign` |
| Accounting | `account.move`, `account.move.line` | `references/account.md` | `action_post` |
| Manufacturing | `mrp.production`, `mrp.bom`, `stock.move` | `references/mrp.md` | `button_mark_done` |
| Purchase + HR | `purchase.order(.line)`, `hr.employee`, `hr.leave` | `references/purchase-hr.md` | `button_confirm` |

## How to use a playbook (every file, same shape)

1. **Key models** — what you're really touching (and which module adds it).
2. **Read-first methods** — feed these to `odoo-ai ... all --methods`; these are the workhorses, not the button shells.
3. **Right extension hook** — prefer a `_prepare_*` / `_action_*` / `_get_*` hook over overriding the public `action_*` / `button_*`. A button method is a thin shell; the value-building hook is where your data belongs and where `super()` stays intact.
4. **Famous gotchas** — the layering/state/field traps that make a patch "work on my machine" and break in production.

Then confirm exact names against the brief (half the "new" fields already exist), pick the smallest hook, and prove the flow with a trace. Anything below marked **"verify via introspection"** is version- or module-variant — do not type it from memory.

## References

- `references/sale.md` — sale.order(.line): confirm → procurement → invoice; `_prepare_invoice`, `_prepare_procurement_values`.
- `references/stock.md` — pickings/moves/quants: reservation, `_action_assign`/`_action_done`, the v17 field rename, lots, multi-step routes.
- `references/account.md` — account.move: `action_post`/`_post`, taxes, posted-move immutability, lock dates, journals.
- `references/mrp.md` — mrp.production/bom: move generation, BoM explosion, backorders, component reservation.
- `references/purchase-hr.md` — purchase.order: `button_confirm` → receipt → bill; plus a brief hr.employee / hr.leave note.
