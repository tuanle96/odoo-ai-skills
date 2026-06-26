# Playbook: Accounting (`account`)

**Map, not truth — Odoo 17/18.** Localizations (`l10n_*`) extend posting heavily, so introspect MRO before overriding:

```bash
odoo-ai --db <DB> all account.move --methods action_post,_post,button_draft,_compute_amount,_compute_tax_totals
odoo-ai --db <DB> trace account.move <id> action_post   # sequence assignment, balancing, lock-date checks
```

## 1. Key models

| Model | Role |
|-------|------|
| `account.move` | **One model for all**: customer invoice, vendor bill, journal entry — `move_type` distinguishes (`out_invoice`/`in_invoice`/`out_refund`/`in_refund`/`entry`) |
| `account.move.line` | Journal items (debit/credit) **and** invoice lines — same model |
| `account.journal` | Sales/Purchase/Bank/Misc; owns the numbering sequence |
| `account.tax` | Tax definitions; `compute_all` does the real math |
| `account.payment` | Payments & reconciliation |

## 2. Read-first methods (the workhorses, not the buttons)

| Method | Model | What it really does |
|--------|-------|---------------------|
| `action_post` → `_post(soft=True)` | `account.move` | The posting workhorse: assigns `name` from journal sequence, balances, runs lock-date & balance checks, sets `state=posted` |
| `button_draft` | `account.move` | Reset to draft — allowed only if not locked / no payments |
| `_compute_amount` | `account.move` | Totals (`amount_total`, `amount_tax`, signed variants) *(verify exact name; some versions split into `_compute_tax_totals`)* |
| `_compute_tax_totals` / `tax_totals` | `account.move` | Tax breakdown shown on the form |
| `account.tax.compute_all(...)` | `account.tax` | Canonical tax computation — never reimplement |
| `_reverse_moves` | `account.move` | Credit notes / reversals |
| `_move_autocomplete_invoice_lines_values` | `account.move` | Auto-generated tax & payment-term lines *(verify name)* |

## 3. Right extension hook

| You want to… | Hook (prefer) | Not |
|--------------|---------------|-----|
| Add data when an invoice posts | extend `_post` (call `super()`) — but it's immutable after | `action_post` shell |
| Change how an invoice **line** is built from a source doc | the SOURCE doc's `_prepare_invoice_line` (`sale.order.line` / `purchase.order.line`) | account.move (lines arrive pre-built) |
| Change tax behavior | configure `account.tax` + fiscal positions | override `compute_all` |
| Change numbering | journal sequence / `sequence_prefix` config | string-format `name` in `_post` |
| React to payment | `account.payment` / reconciliation hooks | poke `payment_state` |

## 4. Famous gotchas

- **Posted = immutable.** Once `state='posted'`, almost all fields are readonly; you must `button_draft` (blocked if locked or paid) to edit. Never `write()` business fields to a posted move.
- **Balance constraint** — every move must have debit == credit (`_check_balanced`). Any line edit must keep it balanced or post fails.
- **Lock dates** — `_check_fiscalyear_lock_date` / tax lock date reject posting or editing in closed periods. Backdated entries fail here, not in your code.
- **Taxes are not arithmetic** — `price_include`, fiscal-position mapping, tax groups, and rounding (`round_per_line` vs `round_globally`) all bite. Always go through `account.tax.compute_all`.
- **Sequence gaps are legal data** — `name` is assigned at post from the journal sequence; never renumber or reuse. Many countries forbid gaps.
- **`move_type` sign conventions** — use the `*_signed` fields for direction-correct amounts; raw `amount_total` is unsigned.
- **Localizations stack on `_post`** — your override sits among many `l10n_*` extensions. Read the MRO from `model_brief` and keep `super()` intact.
