# Scenario playbooks — three business flows, end to end

Concrete `introspect → plan → patch → test` passes for the flows people most
often customize after `sale.order` (see the runnable
[`examples/sale_confirm_guard`](../../../examples/sale-order-walkthrough.md) for
the full worked module). Each scenario below is a **guard/extension** on a
posting-style entry method — the riskiest place to get the MRO, the state
literal, or the hook wrong.

The static maps in `account.md` / `stock.md` / `mrp.md` tell you *which* methods
exist; these tell you *how to approach a change* on them. Every name is still
**verify-via-introspection** — confirm against the brief before you type it.

---

## A. `invoice_post` — block posting an invoice that fails a business rule

**Requirement:** a customer invoice must carry a customer reference
(`ref`) before it can be posted.

### 1. Introspect

```bash
odoo-ai --db <DB> all account.move --methods action_post,_post
odoo-ai --db <DB> trace account.move <id> action_post          # localizations + balance/lock checks
odoo-ai --db <DB> security account.move --user <accountant-id>  # can they even post? which rows?
```

Read from the brief:

- **The chain on `_post`.** `action_post` is a thin shell that calls
  `_post(soft=True)`; the real work (sequence assignment, `_check_balanced`,
  lock-date checks, `state='posted'`) is in `_post`, and `l10n_*` modules stack
  overrides there. The `summary.top_self_sql` from the trace shows which
  localization frames actually run.
- **The state literal is `'posted'`** (not `'validated'`/`'done'`), and
  `move_type` distinguishes invoice from bill/entry — your guard must scope to
  `out_invoice` so it doesn't block journal entries.

### 2. Plan

- Hook: override **`_post`** (the workhorse), guard **before** `super()`, scope
  to `move_type in self.env['account.move'].get_sale_types()` (or
  `('out_invoice', 'out_refund')`). Guarding before `super()` avoids a
  half-assigned sequence number.
- Recordset-safe: `_post` runs on batches — loop `self`.
- `depends = ['account']` — the owning core module.

### 3. Patch (sketch)

```python
class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        for move in self:
            if move.move_type in ("out_invoice", "out_refund") and not move.ref:
                raise UserError(_("Set a customer reference before posting %(n)s.",
                                  n=move.display_name))
        return super()._post(soft=soft)
```

### 4. Test (fail-before / pass-after)

- `post_install` tagged; create an `out_invoice` with balanced lines.
- Assert posting without `ref` raises `UserError` and `state` stays `'draft'`.
- Assert posting with `ref` set reaches `state == 'posted'`.
- Assert a `move_type='entry'` journal entry is **not** blocked (scope check).

### Gotchas specific to this scenario

- Don't `write()` the guard onto a **posted** move — posted is immutable; the
  guard must run *at* post.
- A backdated invoice may fail in `_post` on the lock date, not your guard —
  the trace's `exception_origin` tells you which.
- `sudo()` paths (e-invoicing, EDI) can call `_post` outside the UI — your guard
  still fires there, which is usually correct; confirm it's intended.

---

## B. `picking_validate` — enforce a rule when a delivery is validated

**Requirement:** a delivery (`outgoing` picking) can't be validated unless every
move line has a lot/serial when the product is tracked.  *(Odoo already enforces
tracking; this scenario shows the hook + the v17 field trap — adapt the rule to
your real need, e.g. a custom carrier/packaging check.)*

### 1. Introspect

```bash
odoo-ai --db <DB> all stock.picking --methods button_validate,_action_done
odoo-ai --db <DB> all stock.move.line --methods create,write     # confirm the v17 field names
odoo-ai --db <DB> trace stock.picking <id> button_validate        # reservation → quant updates
```

Read from the brief:

- **`button_validate` is a shell** that may pop a backorder / immediate-transfer
  **wizard** and then calls `_action_done` on the moves. Put real logic in
  `_action_done` (or guard `button_validate` before `super()` for a pure
  pre-check), never assume `button_validate` runs straight through.
- **The v17 field rename (critical):** `stock.move.line.qty_done` is **gone** —
  it's `quantity` + `picked` + `quantity_product_uom` on 17/18/19. The brief's
  `fields` block is the only safe source; code using `qty_done` no-ops silently.
- **`picking_type_code`** distinguishes `incoming`/`outgoing`/`internal` — scope
  the guard so it doesn't fire on receipts.

### 2. Plan

- Hook: override **`button_validate`**, run the pre-check, then `super()`. Keep
  it before the wizard so a bad picking never reaches `_action_done`.
- Read move-line state through `quantity`/`picked`, not `qty_done`.
- `depends = ['stock']`.

### 3. Patch (sketch)

```python
class StockPicking(models.Model):
    _inherit = "stock.picking"

    def button_validate(self):
        for picking in self.filtered(lambda p: p.picking_type_code == "outgoing"):
            for line in picking.move_line_ids:
                if line.product_id.tracking != "none" and not (line.lot_id or line.lot_name):
                    raise UserError(_("Lot/Serial required on %(p)s.",
                                      p=line.product_id.display_name))
        return super().button_validate()
```

### 4. Test

- `post_install`; build an `outgoing` picking for a `serial`/`lot`-tracked product.
- Assert validating without a lot raises `UserError` and the picking state is
  unchanged (not `'done'`).
- Assert it validates once `lot_id` is set.
- Multi-step route check: if the DB uses pick-pack-ship, `trace_flow` shows the
  several chained pickings — make sure the guard targets the right type.

### Gotchas specific to this scenario

- **Moves merge** in `_action_confirm` (`_merge_moves`) — per-move custom data
  can be merged away before validation; verify with the trace.
- The **backorder wizard** means `button_validate` can return an action dict, not
  just `True` — preserve `super()`'s return value.
- Serial = qty 1 per line; a single line with qty > 1 on a serial product is
  itself an error before yours.

---

## C. `mrp_produce` — react when a manufacturing order is marked done

**Requirement:** when an MO completes, stamp a quality-check reference on the
finished move's lot. *(Illustrative — swap for your post-production side effect.)*

### 1. Introspect

```bash
odoo-ai --db <DB> all mrp.production --methods button_mark_done,_post_inventory
odoo-ai --db <DB> all mrp.production --methods _get_moves_finished_values  # if you touch finished moves
odoo-ai --db <DB> trace mrp.production <id> button_mark_done                # consumption + production
```

Read from the brief:

- **Where completion really happens.** `button_mark_done` orchestrates the
  backorder wizard and calls `_post_inventory` (consume `move_raw_ids`, produce
  `move_finished_ids`, update quants). The exact internal name varies by version
  (`_button_mark_done` / `_post_inventory`) — **read the MRO**, don't assume.
- **`mrp_workorder` changes the entry point.** If it's installed, work is driven
  through workorders (`record_production`), not just `button_mark_done`. The
  brief's `overridden_methods` / the entrypoints buttons tell you which exist.
- **`qty_producing`** drives the done quantity; completion may be partial.

### 2. Plan

- Hook: extend **`button_mark_done`** and act **after** `super()` (the finished
  move + its lot only exist once production is posted). Read, don't re-run, the
  flow.
- Handle **backorders**: marking done partially spawns a backorder MO, so your
  post-done logic can fire more than once — make it idempotent.
- `depends = ['mrp']` (+ `quality` only if you actually use its models).

### 3. Patch (sketch)

```python
class MrpProduction(models.Model):
    _inherit = "mrp.production"

    def button_mark_done(self):
        res = super().button_mark_done()
        for mo in self.filtered(lambda m: m.state == "done"):
            for move in mo.move_finished_ids.filtered(lambda mv: mv.state == "done"):
                for line in move.move_line_ids:
                    if line.lot_id and not line.lot_id.x_qc_ref:
                        line.lot_id.x_qc_ref = mo.name
        return res
```

### 4. Test

- `post_install`; build a small BoM + MO (component qty available), set
  `qty_producing`, mark done.
- Assert the finished lot carries the QC ref after completion.
- Backorder case: produce a partial qty, assert the post-done logic ran for the
  completed MO and again (idempotently) for the backorder.
- Component reservation uses stock `_action_assign` — the v17 `quantity`/`picked`
  rename and lot rules from `stock.md` apply to `move_raw_ids` too.

### Gotchas specific to this scenario

- **Acting after `super()`** is right here (the data must exist), unlike a guard
  which acts before — don't cargo-cult the position from scenario A/B.
- **BoM explosion is recursive** (phantom/kit) — finished/byproduct moves may be
  more than you expect; `move_byproduct_ids` ≠ `move_finished_ids`.
- Marking done can run your code **twice** via the backorder — idempotency is not
  optional.

---

## The shape every scenario shares

1. **Trace the entry method first** — these are graphs (sale→stock→account,
   mrp→stock), and `summary.exception_origin` / `top_self_sql` from Layer D tell
   you where it really runs and where it would break.
2. **Guard *before* `super()`; react *after* `super()`.** A precondition belongs
   before (no half-done document); a side effect that needs the result belongs
   after.
3. **Scope by type** — `move_type`, `picking_type_code`, MO state — so the hook
   doesn't fire on the wrong document.
4. **Prove it fail-before / pass-after**, as a **non-admin** user (run
   `odoo-ai security <model> --user <id>` first), and check batch + multi-company.
