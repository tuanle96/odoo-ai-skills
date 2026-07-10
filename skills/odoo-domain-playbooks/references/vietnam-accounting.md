# Vietnam accounting compliance (VAS) — regime map for Odoo work

> **Verified July 2026 from primary sources (thuvienphapluat, MoF).** Vietnamese
> accounting law moves fast and everything below has an effective date —
> **re-verify anything you rely on after this stamp** (web search the circular
> number) before advising a client. This is the *what/when*; build the reports
> with the **`odoo-statutory-reports`** skill.

## Regime timeline — the one fact that reframes every VN engagement

| Regime | Applies | Status |
|--------|---------|--------|
| TT200/2014/TT-BTC | FY up to 31/12/2025 | superseded |
| **TT99/2025/TT-BTC** | **FY starting 01/01/2026** | current |

Any VN chart-of-accounts or statement work started "per TT200" in 2026 is
building to a dead spec. `l10n_vn`'s CoA is TT200-based — plan a crosswalk.

### TT99 key deltas (chart)

- **611 / 631 dropped** — periodic-inventory method removed entirely.
- **6275, 6415 repurposed** → "Thuế, phí và lệ phí" (were: production-tool
  cost / transport). Existing sub-accounts under old semantics must be
  **re-coded, not reused**.
- **8211 split** → 82111 (current CIT) / 82112 (deferred).
- Various renames; borrowing costs get their own disclosure (see B02 mã 24).

### TT99 key deltas (statements)

- **B01 (balance sheet)**: renamed/restructured, new line-code layout.
- **B02 (P&L)**: new **mã 21** (real-estate business revenue, TK 5117 —
  and mã 01 must *exclude* 5117), mã 22/23 finance income/cost, **mã 24
  "Trong đó: Chi phí đi vay"** (6351), and the new legal formula
  `30 = 20 + 21 + 22 − (23 + 25 + 26)`.
- **B03 (cash flow)**: now **mandatory** (direct or indirect).
- **B09 (notes)**: expanded; any item ≥ **10% of total assets** needs its own
  disclosure detail.
- Filing: annual set within **90 days** of year-end; interim for listed cos.

### Sổ kế toán (books) — the trap and the pattern

TT99 makes the book templates **guidance, not mandate**: enterprises
self-design their books and document the system in an internal **quy chế kế
toán**. The winning Odoo pattern: keep native ledgers/journals as the books,
and ship a **"phụ lục quy chế" mapping appendix** — one table per statutory
sổ (S-series) naming the exact Odoo screen/report that produces it. Books a
grid can't produce (per-MO cost cards, production S36/S37-style, warehouse
in-out-stock S12-style) → xlsxwriter wizards reading `stock_valuation_layer`.

## Tax & compliance calendar (as of 07/2026)

| Topic | Rule | Effective |
|-------|------|-----------|
| E-invoice | NĐ123/2020 → NĐ70/2025 + TT32 → **NĐ254/2026 + TT91/2026** | 01/07/2026 |
| VAT | Law 48/2024; **8% reduced rate extended to 31/12/2026** (NQ204, NĐ174) | — |
| VAT input deduction | non-cash payment proof required for invoices **≥ 5 tr VND** | 01/07/2025 |
| CIT | Luật 67/2025: 20% standard; 17%/15% small-enterprise bands by revenue | FY2026 |
| Tax admin | Luật 108/2025 + NĐ252/2026 (eTax; XML schemas — never hardcode) | 01/07/2026 |
| Mandatory audit | NĐ90/2025: any **2 of 3** — ≥200 employees, ≥300 tỷ revenue, ≥100 tỷ assets | 2026 |

Practical bombs to schedule: **8%→10% VAT flip on 01/01/2027** (mass tax
update across every 8% product); SInvoice/e-invoice provider contract + UAT
before NĐ254 enforcement; borrowing-cost split into its own 6351x sub-account
for B02 mã 24.

## Odoo mapping — what ships, what to build

| Layer | Reality |
|-------|---------|
| `l10n_vn` | CoA (TT200 lineage) + basic taxes. Extend with **tiểu khoản** (sub-accounts), don't fork. |
| `l10n_vn_reports` | some statements; **no TT99 versions**, no B01/B03/B09 in TT99 shape (verify against the instance). |
| `l10n_vn_edi_viettel` | SInvoice e-invoice integration. |
| To build | TT99 B01/B02/B03 (`account.report`, see `odoo-statutory-reports` — incl. the per-partner 131/331 no-offset split), B09 data pack + sổ wizards (xlsxwriter), eTax XML export. |

### Design decisions that survived contact with a real client

- **Khoản mục chi phí (expense categories) = tiểu khoản in the CoA**, not
  analytic tags. Analytic-tag cost taxonomies drift and can't feed statutory
  lines; sub-accounts (`627500`, `641550`, …) feed `domain` engines directly.
- **Dimensions (department, region, product-line) = analytic plans** with
  distribution models + `account.analytic.applicability` mandatory rules —
  but note the validation only fires via the UI post button's
  `validate_analytic=True` context.
- **Costing is perpetual**: 152 → 154 → 155 via `stock_valuation_layer`
  (`raw_material_production_id` / `production_id` on the moves). There are
  **no 621/622/627 postings** in Odoo's perpetual flow — don't design reports
  that expect them; 154 movement *is* the WIP ledger.
- **Crosswalk discipline** (TT200→TT99): classify every account
  KEEP / RE-CODE / NEW / RETIRE; retire = `deprecated=True`, never delete;
  migration scripts only for DBs where the module is actually deployed.
- **131/331 must not offset**: statutory B01 shows receivable-side and
  payable-side separately *per partner* — a custom engine, not a domain sum.

## Checklist for a VN accounting engagement

1. Which regime? FY2026+ ⇒ TT99 — confirm the client's quy chế status.
2. Introspect the instance's CoA + installed `l10n_vn*` (never assume).
3. Crosswalk the live CoA to TT99; freeze 611/631; re-code 6275x/6415x heirs.
4. Build/verify statements with `odoo-statutory-reports` (self-verifying:
   catch-alls = 0, 270=440, cash-flow reconciliation line).
5. Books: native ledgers + quy chế mapping appendix + wizard packs.
6. Calendar the tax bombs (e-invoice cutover, VAT flip, audit threshold).
