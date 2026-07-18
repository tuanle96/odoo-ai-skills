---
name: odoo-valuation-repair
description: >-
  Diagnose and repair broken inventory valuation on AVCO/FIFO products —
  negative or absurd standard_price, a stock.valuation.layer book that drifted
  from physical stock, per-lot (lot_valuated) books torn by mid-stream config
  flips. Use when a product shows cost ≤ 0 (or a nonsense huge /
  repeating-decimal cost) while stock is on hand, when Σ SVL quantity ≠ valued
  on-hand quantity, when COGS posts at garbage unit costs, before/after
  changing a category's cost method or valuation, or when enabling per-lot
  valuation on a product that already has history. Covers the read-only RCA
  query ladder, a fail-closed per-lot repair runbook (shell / server action /
  RPC-driven, SaaS-safe), and a daily drift check that also catches the silent
  victims whose cost is wrong but still positive. Also covers the purchase-side
  variant — a receipt booked at a wrong unit cost (a UoM / decimal slip,
  classically ×1000) that leaves book quantity perfectly matching physical (so
  the drift check passes) yet an absurd AVCO, which a full goods return cannot
  un-mix, repaired against the goods-received/invoice interim with zero P&L
  impact.
---

# Broken inventory valuation — diagnose, repair, prevent

**The invariant:** for every storable product, `Σ stock.valuation.layer.quantity`
(the *book*) must equal physical stock in **valued** locations (`internal` +
company-owned `transit`, mirroring `stock.location._should_be_valued()`). The
displayed AVCO cost is just `value_svl ÷ quantity_svl` — when the book drifts,
the denominator hovers near zero and the "cost" becomes noise: negative,
absurdly large, and swinging on every move.

**A negative cost is never the disease — it is the visible symptom of a broken
book.** Products whose garbage cost happens to still be *positive* are the
silent majority (a repeating decimal like `505352.312312…` betrays a tiny
denominator). Any monitor that only alerts on `standard_price <= 0` misses
them; the drift check below does not.

Targets Odoo 17/18 (18 specifics flagged). SaaS-safe: everything here runs
through XML-RPC + a server action — no shell or SQL console needed on the
target.

## Diagnose (read-only, ~15 min)

Run the ladder in order — each answer narrows the next question:

1. **Formula check.** Read `quantity_svl`, `value_svl`, `qty_available`,
   `standard_price` on the product. `standard_price == value_svl /
   quantity_svl` with a tiny (or negative) `quantity_svl` while
   `qty_available` is large ⇒ drift confirmed. The number changing hour to
   hour is the same symptom (denominator near 0), not "the cost moving".
2. **Counterpart mass.** `read_group` quants by `location_id` over ALL
   usages: the double-entry sums to 0 and shows real production/sales volume
   vs what the book ever saw.
3. **When did the book start?** `read_group` SVL by `create_date` month vs
   done `stock.move` in/out by month. Months with physical flow but **zero
   layers** = the hole (valuation started mid-life). Probe one early done
   move: `stock_valuation_layer_ids == []` is the smoking gun.
4. **Config-flip archaeology.** Search SVL with `stock_move_id = False` —
   the descriptions record cost-method/valuation changes ("manual_periodic →
   real_time") and per-lot redistribution events, with exact timestamps and
   the book state at flip time (the ± pair quantities).
5. **Per-lot tears** (if `lot_valuated`, 18+). `read_group` SVL by `lot_id`:
   lots with negative book qty (outs recorded, ins never valued) and lots
   with qty but zero value are the repair list. The enable-flip redistributes
   ONLY the book value existing at that moment across ONLY the lots then on
   hand — every other lot starts life torn.
6. **Fleet scan.** One pass over all storable products comparing book qty vs
   valued quants (SQL in *Prevent*) — the full victim list, including the
   positive-but-wrong ones.

Root causes to expect: valuation started mid-stream (product not tracked at
first, or layers deleted in a past "reset"); category cost-method /
valuation-mode flips with stock on hand; `lot_valuated` enabled on a product
with history; negative-stock episodes later "fixed" by the FIFO vacuum at
mismatched costs; direct SVL tampering; a **purchase receipt booked at a wrong
unit cost** (a UoM / decimal slip) — value-only, book quantity stays correct,
so this one hides from the drift check (see *Purchase-side cost error* below).

## Repair — per product, per lot, fail-closed

**Goal state per lot:** book qty = physical qty; book value = physical ×
a defensible cost; dead lots (book ≠ 0, physical = 0) zeroed; one journal
entry carries the total delta (Debit stock-valuation account / Credit the
P&L counterpart your accountant chose — or reversed when the delta is
negative).

Cost ladder per lot (get the rule signed off by accounting **before** the
first commit): the lot's own genuine inbound layers' average (`quantity > 0`,
has `stock_move_id`, `unit_cost > 0`) → the product's latest genuine inbound
layer → an explicit override parameter.

**Clamp the ladder — a lot's own inbound average can itself be poisoned.**
One production order consuming mis-valued components can inject an inflated
inbound layer worth orders of magnitude more than reality (observed: a single
MO receipt at ~166× the real unit cost). From then on that lot's "genuine
average" is garbage, and deliveries from it can post **negative unit costs**
(positive-value outgoing layers → journal entries flipped to Dr inventory /
Cr COGS on a *delivery*). The P&L tell: a COGS sub-account driven deeply
negative. So: if a lot's inbound average deviates more than **3× either way**
from the reference cost, use the reference instead and mark the plan line —
the code below does this. Sanity-check the reference itself against sibling
products / nearby healthy lots before trusting it.

| Where you are | Delivery vehicle |
|---|---|
| Self-hosted, shell access | `odoo-bin shell` script implementing the same algorithm; `env.cr.commit()` only after the verify block passes |
| SaaS / no shell | **Server action** (code below): safe_eval-compatible, parameters via context, DRY RUN by default, run from the UI or by RPC |
| Agent / RPC-driven | Create the server action once via RPC, then `action.with_context(...).run()` per product. Note: creating SVL rows via plain RPC `create` is often refused by client-side validators because SVL fields are `readonly` in `fields_get` metadata (they remain ORM-writable) — the server action sidesteps that cleanly |

```python
# SERVER ACTION — repair per-lot valuation for ONE product (SaaS-safe).
# Model: product.product, type: Execute Code.
# Run:  action.with_context(vr_product_id=<ID>, vr_counterpart='<ACC>').run()          -> DRY RUN (plan popup, writes nothing)
#       action.with_context(vr_product_id=<ID>, vr_counterpart='<ACC>', vr_commit=1).run() -> COMMIT
# Optional context: vr_journal (code, default: category stock journal), vr_cost (forced cost, 0 = auto)
PRODUCT_ID = env.context.get('vr_product_id')
COGS_CODE = env.context.get('vr_counterpart')
if not PRODUCT_ID or not COGS_CODE:
    raise UserError('Missing context: vr_product_id and vr_counterpart (P&L account code chosen by accounting).')
JOURNAL_CODE = env.context.get('vr_journal')
FALLBACK_COST = env.context.get('vr_cost') or 0.0
DRY_RUN = not env.context.get('vr_commit')

company = env.company
SVL = env['stock.valuation.layer'].sudo()
P = env['product.product'].sudo().browse(PRODUCT_ID)
val_acc = P.categ_id.property_stock_valuation_account_id
journal = P.categ_id.property_stock_journal or env['account.journal'].sudo().search([('code', '=', JOURNAL_CODE or ''), ('company_id', '=', company.id)], limit=1)
cogs = env['account.account'].sudo().search([('code', '=', COGS_CODE)], limit=1)
if not val_acc or not journal or not cogs:
    raise UserError('Missing config: valuation account %s | journal %s | counterpart %s' % (val_acc, journal, cogs))

# Physical stock per lot in VALUED scope. Company comes from the LOCATION:
# quants created via the low-level API can carry a NULL company.
phys = {}
for g in env['stock.quant'].sudo().read_group([('product_id', '=', PRODUCT_ID), ('location_id.usage', 'in', ['internal', 'transit']), ('location_id.company_id', '=', company.id)], ['quantity:sum'], ['lot_id']):
    lid = g['lot_id'][0] if g['lot_id'] else False
    if abs(g['quantity'] or 0.0) > 0.0001:
        phys[lid] = g['quantity']

book = {}
for g in SVL.read_group([('product_id', '=', PRODUCT_ID), ('company_id', '=', company.id)], ['quantity:sum', 'value:sum'], ['lot_id']):
    lid = g['lot_id'][0] if g['lot_id'] else False
    book[lid] = (g['quantity'] or 0.0, g['value'] or 0.0)

ref_layer = SVL.search([('product_id', '=', PRODUCT_ID), ('quantity', '>', 0), ('stock_move_id', '!=', False), ('unit_cost', '>', 0)], order='id desc', limit=1)
ref_cost = FALLBACK_COST or (ref_layer.unit_cost if ref_layer else 0.0)
if not ref_cost:
    raise UserError('No reference cost found — pass vr_cost and rerun.')

adj = []
total_dv = 0.0
lines = ['Product: %s | on hand %s | ref cost %s' % (P.display_name, sum(phys.values()), round(ref_cost, 2))]
for lid in set(list(phys.keys()) + list(book.keys())):
    pq = phys.get(lid, 0.0)
    bq = book.get(lid, (0.0, 0.0))[0]
    bv = book.get(lid, (0.0, 0.0))[1]
    c = 0.0
    clamped = ''
    if pq > 0:
        c = ref_cost
        if lid:
            ins = SVL.read_group([('product_id', '=', PRODUCT_ID), ('lot_id', '=', lid), ('quantity', '>', 0), ('stock_move_id', '!=', False), ('unit_cost', '>', 0)], ['quantity:sum', 'value:sum'], [])
            if ins and ins[0].get('quantity'):
                c = ins[0]['value'] / ins[0]['quantity']
                if c > ref_cost * 3 or c * 3 < ref_cost:
                    c = ref_cost
                    clamped = ' [LOT COST ABNORMAL -> USING REFERENCE]'
    target_v = round(pq * c)
    dq = pq - bq
    dv = target_v - bv
    if abs(dq) < 0.0001 and abs(dv) < 1:
        continue
    adj.append({'lot': lid, 'pq': pq, 'dq': dq, 'dv': dv, 'c': c, 'tv': target_v})
    total_dv += dv
    lname = env['stock.lot'].sudo().browse(lid).name if lid else '(no lot)'
    lines.append('lot %s: on hand %s | book %s / %s -> dq %+g, dv %+d, cost %s%s' % (lname, pq, bq, round(bv), dq, round(dv), round(c, 2), clamped))
lines.append('TOTAL VALUE DELTA: %s (Dr %s / Cr %s, journal %s)' % (round(total_dv), val_acc.code, cogs.code, journal.code))
if not adj:
    raise UserError('Book already matches physical — nothing to repair.')
if DRY_RUN:
    raise UserError('PLAN (DRY RUN — NOTHING WRITTEN):\n' + '\n'.join(lines))

je = env['account.move'].sudo().create({
    'journal_id': journal.id,
    'date': datetime.date.today(),
    'ref': 'Per-lot valuation repair — %s' % P.display_name,
    'line_ids': [
        (0, 0, {'account_id': val_acc.id, 'name': 'Inventory valuation adjustment (per lot)', 'debit': total_dv if total_dv > 0 else 0.0, 'credit': -total_dv if total_dv < 0 else 0.0, 'tax_ids': [(5, 0, 0)]}),
        (0, 0, {'account_id': cogs.id, 'name': 'Valuation adjustment counterpart', 'debit': -total_dv if total_dv < 0 else 0.0, 'credit': total_dv if total_dv > 0 else 0.0, 'tax_ids': [(5, 0, 0)]}),
    ],
})
je.action_post()

new_layers = SVL.create([{'product_id': PRODUCT_ID, 'company_id': company.id, 'quantity': a['dq'], 'unit_cost': a['c'], 'value': a['dv'], 'remaining_qty': 0, 'remaining_value': 0, 'lot_id': a['lot'] or False, 'description': 'Valuation repair: book -> physical x real cost', 'account_move_id': je.id} for a in adj])

# remaining hygiene: zero the touched lots' old layers, park the position on
# the adjustment layer — otherwise _run_fifo_vacuum re-corrupts on the next in.
lot_ids = [a['lot'] for a in adj if a['lot']]
if lot_ids:
    SVL.search([('product_id', '=', PRODUCT_ID), ('lot_id', 'in', lot_ids), ('id', 'not in', new_layers.ids)]).write({'remaining_qty': 0, 'remaining_value': 0})
if any(not a['lot'] for a in adj):
    SVL.search([('product_id', '=', PRODUCT_ID), ('lot_id', '=', False), ('id', 'not in', new_layers.ids)]).write({'remaining_qty': 0, 'remaining_value': 0})
idx = 0
for a in adj:
    nl = new_layers[idx]
    idx += 1
    if a['pq'] > 0:
        nl.write({'remaining_qty': a['pq'], 'remaining_value': a['tv']})

fq = 0.0
fv = 0.0
for g in SVL.read_group([('product_id', '=', PRODUCT_ID), ('company_id', '=', company.id)], ['quantity:sum', 'value:sum'], []):
    fq = g['quantity'] or 0.0
    fv = g['value'] or 0.0
final_book = {}
for g in SVL.read_group([('product_id', '=', PRODUCT_ID), ('company_id', '=', company.id)], ['quantity:sum'], ['lot_id']):
    lid = g['lot_id'][0] if g['lot_id'] else False
    final_book[lid] = g['quantity'] or 0.0
for lid in phys:
    if abs(final_book.get(lid, 0.0) - phys[lid]) > 0.001:
        raise UserError('VERIFY FAIL lot %s: book %s vs physical %s — EVERYTHING ROLLED BACK' % (lid, final_book.get(lid, 0.0), phys[lid]))
if abs(fq - sum(phys.values())) > 0.001:
    raise UserError('VERIFY FAIL total qty: book %s vs physical %s — EVERYTHING ROLLED BACK' % (fq, sum(phys.values())))

# Sync STORED costs via SQL: standard_price is company-dependent jsonb (17/18)
# and an ORM write would trigger _change_standard_price -> a second layer.
new_std = fv / fq if fq else 0.0
env.cr.execute("UPDATE product_product SET standard_price = jsonb_build_object(%s, %s::float) WHERE id = %s", (str(company.id), new_std, PRODUCT_ID))
for a in adj:
    if a['lot']:
        env.cr.execute("UPDATE stock_lot SET standard_price = jsonb_build_object(%s, %s::float) WHERE id = %s", (str(company.id), a['c'] if a['pq'] > 0 else 0.0, a['lot']))
log('REPAIR OK %s: JE %s | qty %s | value %s | new cost %s' % (P.display_name, je.name, fq, round(fv), round(new_std, 2)), level='info')
```

### Run ritual

1. **DRY RUN** → read the plan popup line by line (every lot, every delta,
   the JE total). A weird reference cost or an unknown lot = stop and
   investigate; force `vr_cost` only with accounting's number.
2. Owner/accountant approves the plan (paste it into the ticket/chat).
3. **Commit** (`vr_commit=1`) — the action verifies itself and rolls back
   everything on any per-lot mismatch.
4. **Verify with three reads:** product (`quantity_svl == qty_available`,
   sane `standard_price`), the JE (posted, expected total, **zero tax
   lines**), SVL by lot (every on-hand lot: book = physical; dead lots: 0/0).
5. Pilot ONE product end-to-end, then batch 10–20 per approval round.

### Rules while repairing

- **Never touch quants** — physical stock is the one thing that is right;
  an inventory-adjustment "0 then restore" cycle writes layers at the garbage
  cost and destroys lot history.
- **Never write `standard_price` via UI/ORM** on a broken product (each write
  creates ANOTHER layer); stored product/lot costs are synced by SQL above.
- The native revaluation wizard cannot fix this class of damage. It fails two
  ways: it refuses `quantity_svl <= 0` (the mid-stream case); and even for a
  healthy-quantity value-only write-down it spreads the delta across the
  remaining layers **proportionally by quantity**, so when the garbage sits in
  one layer but the spread reaches a small healthy layer it drives that layer's
  `remaining_value` negative (*"the value of a stock valuation layer cannot be
  negative"*). The server action sets each layer's value directly and sidesteps
  both.
- JE date = run date. Backdating needs the period-lock discussion first.
- The fix is **prospective**: historical COGS posted at garbage costs stays —
  say so to accounting explicitly.
- Rollback of a committed product = reverse the JE + unlink its layers
  (matched by description + product) as one unit; each product is independent.

## Purchase-side cost error — the return that won't clean

Not every broken cost is mid-stream drift. A **purchase receipt posted at a
wrong unit cost** (a UoM / decimal slip — a price entered orders of magnitude
off, classically ×1000) inflates the AVCO while **book quantity still exactly
matches physical**. The drift check passes; only the *cost* is absurd. So
whenever a cost looks wrong but the book ties out, run a second read: compare
`unit_cost` on the product's recent inbound layers against the vendor bill / PO
price and sibling receipts — the ×1000 layer stands out immediately.

**The AVCO-return trap — why sending the goods back does not fix it.** Average
costing removes a return at the *current* average — already diluted by every
receipt and consumption since the bad one — not at the cost the receipt came in
at. A wrong-cost receipt returned **in full** therefore still leaves a value
residual stuck: value-in (qty × wrong cost) ≠ value-out (qty × current
average). Quantity nets to zero; value does not. Under FIFO the return would
unwind that specific layer at its own cost and self-clean; **under AVCO only a
revaluation fixes a cost error** — you cannot un-mix an average by moving
quantity. (Intuition: pour boiling water into a bucket, add cold + scoop some
out, then "return" the boiling volume — you scoop at the *current* temperature,
so the excess heat stays.)

**The counterpart is the goods-received/invoice interim, not P&L.** Read the
receipt's valuation entry (`stock.valuation.layer.account_move_id`): a purchase
receipt posts **Dr stock-valuation / Cr GR-IR interim** — a non-reconcilable
current-liability clearing account — so the whole error lives on the balance
sheet and **never reached P&L**. Repair with **that interim as `vr_counterpart`**:
a write-down posts Dr interim / Cr valuation, **zero P&L impact**, and it clears
the mirror residual the receipt+return stranded in the interim (receipt credit −
return debit). This is the opposite of mid-stream drift, where the garbage flowed
out through deliveries and the counterpart is a P&L (COGS) account. **Confirm the
account is the interim, not the vendor payable** — the payable is
reconcilable/per-partner and is already correct from the real invoice; never
touch it. Nothing else changes: same server action, same DRY-RUN ritual, same
fail-closed verify; the delta is a pure value write-down (`dq = 0`).

## Period split — when the ledger is management-only

If the Odoo ledger is **not** the statutory books, statutory error-correction
machinery (retained-earnings restatement etc.) does not bind — the useful goal
becomes *each fiscal year carries its own share of the correction* so
per-year P&L / inventory analytics read true. Split each product's repair:

- `Δprior = (physical qty at prior FY end × milestone cost) − (Σ SVL value at
  prior FY end)` — physical-at-date via `qty_available` with a `to_date`
  context; book-at-date via a `create_date <=` aggregate. Book one JE dated
  the last day of the prior FY (Dr valuation / Cr adjustment sub-account, or
  reversed) — check `fiscalyear_lock_date`/`hard_lock_date` first.
- `Δcurrent = Δtotal − Δprior` — the repair JE stays in the current year at
  this amount. Products the current year *sold down* get a flipped sign here;
  that is correct, not a bug.
- **Milestone cost can be garbage too** (inbound layers near the FY cutoff may
  carry the same disease) — clamp it against the trusted current reference the
  same way as lot costs.
- The split applies to the **general ledger** only; the valuation-layer
  register stays dated at repair time, so "valuation at prior FY end" reports
  keep showing pre-repair numbers unless you also backdate layers (heavy,
  usually not worth it).

## Prevent — the drift check

The negative-cost alert (`standard_price <= 0 and qty_available > 0`) catches
one victim in dozens. The real monitor is the invariant itself — run daily:

```sql
WITH book AS (
    SELECT product_id, SUM(quantity) AS q
    FROM stock_valuation_layer WHERE company_id = %s GROUP BY product_id
), phys AS (
    SELECT sq.product_id, SUM(sq.quantity) AS q
    FROM stock_quant sq
    JOIN stock_location sl ON sl.id = sq.location_id
    WHERE sl.usage IN ('internal', 'transit') AND sl.company_id = %s
    GROUP BY sq.product_id
)
SELECT p.id, COALESCE(b.q, 0) AS book_q, COALESCE(ph.q, 0) AS phys_q
FROM product_product p
JOIN product_template pt ON pt.id = p.product_tmpl_id
LEFT JOIN book b ON b.product_id = p.id
LEFT JOIN phys ph ON ph.product_id = p.id
WHERE p.active AND pt.is_storable
  AND ABS(COALESCE(b.q, 0) - COALESCE(ph.q, 0))
      > GREATEST(1.0, 0.01 * ABS(COALESCE(ph.q, 0)))
```

- Threshold `max(1 unit, 1%)` absorbs float dust; genuine victims drift by
  hundreds of units. Including company-owned `transit` prevents every
  in-flight transfer from false-positiving overnight.
- Wire it into an `ir.cron` that raises whatever your ops stack uses (alert
  record, activity, mail) and **auto-resolves** when a product stops
  drifting; batch the first run's findings into one digest — a sick database
  flags dozens at once.
- Process rules that stop new victims: **reconcile book = physical before
  any cost-method / valuation-mode / `lot_valuated` change**; when valuation
  starts mid-life (product switched to tracked, category switched to
  real_time), create an **opening layer** for the stock already on hand.

## Gotchas

| Trap | Reality |
|---|---|
| `standard_price` looks stable/positive | Stored value = last recompute; with a near-zero book qty it is still garbage — trust `value_svl / quantity_svl`, not the sign |
| Writing `lot.standard_price` to "fix" a lot | Triggers a lot revaluation layer (18+) — double count; sync stored costs by SQL after truing the book |
| `account.account.code` searches (18) | `code` is computed from company-dependent `code_store` jsonb — ORM `search` on `code` works, raw SQL needs `code_store->>'<company_id>'` |
| `lot_valuated` flip as a repair tool | Redistributes only the CURRENT book value across CURRENT quants — it fixes the split, never the total |
| Quants with NULL `company_id` | Low-level `stock.quant._update_available_quantity` doesn't set it — always scope by `location.company_id` |
| `qty_available` as the physical side | Excludes transit → every in-flight transfer looks like drift; use the valued-scope query above |
| Server-action `safe_eval` | No `def` / `import`; `datetime`, `log`, `UserError` are provided; single-underscore method calls are allowed |
| JE lines on expense accounts | May carry default taxes — always `('tax_ids', [(5, 0, 0)])` on both lines and verify zero tax lines after posting |
| Old layers' `remaining_qty` | Negative remainders left behind get "vacuumed" against the next real receipt and re-corrupt values — zero them as part of the repair |
| Deleted-layer vs never-created | Both look identical in data; done moves with empty `stock_valuation_layer_ids` prove the hole either way — logs (if any) date the reset |
| One poisoned MO receipt | A production order consuming mis-valued components can inflate a finished lot by orders of magnitude in a single layer; subsequent deliveries post **negative unit costs** → journal entries flip to Dr inventory / Cr COGS on deliveries → a COGS sub-account goes deeply negative. Trace the biggest layers of the sick lot to find it |
| XML-RPC integers > 2³¹ | Writing a debit/credit above 2,147,483,647 over XML-RPC fails to marshal ("int exceeds XML-RPC limits") — send the amount as a float, or leave the big JE untouched and post a small compensating JE instead of editing giant lines |
| Server action vs the 2³¹ cap | The marshalling cap only bites values crossing the wire. A JE **created inside the server action** posts server-side, so any amount goes in one shot — no chunking. Prefer the action over RPC-driven line writes for large deltas |
| Editing posted-JE lines across direction | Reset to draft first; when flipping a line from debit to credit you must also pass `amount_currency = debit − credit`, or the sign-consistency constraint blocks the write |
| Returning goods to "fix" a bad receipt cost | AVCO removes the return at the *current* average, not the receipt cost — a wrong-cost receipt returned in full still leaves a stuck value residual (quantity nets to 0, value does not). A cost error under AVCO is fixed by revaluation, never by a return; FIFO would self-clean via layer identity |
| Purchase-error counterpart | The bad receipt booked Dr inventory / Cr GR-IR interim (balance sheet only, never P&L) — repair against **that interim** (zero P&L), not a COGS account; and never against the **vendor payable** (reconcilable/per-partner, already correct from the real invoice) |
| Cost wrong but book ties out | A bad receipt cost is value-only: `Σ SVL qty == physical`, so the drift check is silent. Add a cost-sanity read (inbound `unit_cost` vs PO/bill + siblings) — the ×1000 layer is obvious |

## Related skills

- **odoo-introspect** — dump the model/flow ground truth before touching
  valuation (`odoo-ai brief stock.valuation.layer`, `trace` a move's flow).
- **odoo-data** — data-file / config hygiene for anything you decide to ship.
- **odoo-domain-playbooks** — chart-of-accounts mapping for the counterpart
  account choice (e.g. `references/vietnam-accounting.md` for 15x/632 pairs).
