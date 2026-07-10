---
name: odoo-statutory-reports
description: >-
  Building or customizing DYNAMIC financial statements with Odoo's
  account.report engine (Enterprise `account_reports`) — P&L, balance sheet,
  cash-flow, tax reports, and country statutory formats (VAS, HGB, PCG, …).
  Use when defining account.report / account.report.line /
  account.report.expression records, choosing an engine (domain / aggregation
  / account_codes / custom handler), fixing wrong totals or period semantics
  (date_scope), writing a `_report_custom_engine_*` method, versioning a
  report for a new accounting regime, verifying a statutory report is
  complete/balanced, or exporting multi-sheet data packs a grid can't hold.
  NOT for QWeb PDF documents (invoices, pickings) — that's `odoo-reports`.
---

# Odoo statutory / financial reports — the `account.report` engine

The grids under **Accounting → Reporting** are not QWeb. They are data-defined
reports evaluated by the `account_reports` engine (Enterprise):

| Model | Role |
|-------|------|
| `account.report` | root: name, `country_id`, `root_report_id` (variant of), `filter_*` toggles, columns |
| `account.report.line` | one row: `code`, `name`, `hierarchy_level`, children via `parent_id`, `foldable`, `groupby` |
| `account.report.expression` | one cell per column: `label` (must match a column's `expression_label`, default `balance`), `engine`, `formula`, `subformula`, `date_scope` |

**The rule: a statutory report is a spec with legal line codes — design it to
prove its own correctness (see "Self-verifying design"), and never guess what
an existing report contains: read it from the registry first
(`odoo-introspect`; the records live in `account.report*`).**

## Engines

| Engine | `formula` | Use for |
|--------|-----------|---------|
| `domain` | a domain string over `account.move.line` | 90% of lines: `[('account_id.code', '=like', '511%')]` |
| `aggregation` | arithmetic over `LINECODE.expression_label` | totals & legal formulas: `REV01.balance - COGS11.balance` |
| `account_codes` | prefix arithmetic `511 - 5117` | quick prefix sums (less control than domain) |
| `tax_tags` | tax grid tag | tax returns |
| `external` | manual/carryover value | figures the ledger can't produce |
| `custom` | **full method name** `_report_custom_engine_<x>` | anything needing SQL (per-partner splits, …) |

### Sign convention (`domain` subformula)

`balance` = debit − credit. So **credit-natured lines (revenue, liabilities,
equity) need `-sum`** to display positive; debit-natured (assets, expenses)
use `sum`. `sum_if_pos` / `sum_if_neg` exist for one-sided lines. Getting
this wrong doesn't error — it renders negatives everywhere.

### `date_scope` — the semantics that silently ruin totals

| Value | Means | Use for |
|-------|-------|---------|
| `strict_range` | movement inside the selected period | P&L lines, cash-flow movements |
| `from_beginning` | cumulative from the beginning of time | **every balance-sheet line** |
| `to_beginning_of_period` | balance at period start | opening rows (cash-flow "beginning cash") |

Others exist (`previous_tax_period`, …) — read the source before using.
A balance sheet accidentally on `strict_range` shows period *movement*, which
looks plausible and is wrong.

## Custom engine contract (every part of this is enforced)

```python
class MyHandler(models.AbstractModel):
    _name = "l10n_xx.myreport.handler"
    _inherit = "account.report.custom.handler"

    def _report_custom_engine_my_thing(self, expressions, options, date_scope,
                                       current_groupby, next_groupby,
                                       offset=0, limit=None, warnings=None):
        report = self.env["account.report"].browse(options["report_id"])
        query = report._get_report_query(options, date_scope,
                                         domain=[("account_id.code", "=like", "131%")])
        # query is pre-filtered by the report's options (dates, journals,
        # posted-only, multi-company) — add GROUP BY and run your SQL.
        ...
        return {"result": value, "has_sublines": False}
```

- Link the handler on the report: `custom_handler_model_id`.
- The expression's `formula` is the **full method name** and it **must start
  with `_report_custom_engine_`** — anything else raises at evaluation.
- No-groupby return is a dict whose key is **literally `'result'`** (not the
  expression label, not `'balance'`) — the wrong key surfaces as the cryptic
  *"invalid subformula … False"*. With `current_groupby`, return a list of
  `(grouping_key, {…'result'…})` tuples instead.

Classic use: statutory **no-offset receivable/payable** — split one account's
balance into debit-side and credit-side *per partner* (SQL `GROUP BY
partner_id`, then sum positives vs negatives), impossible with `domain`.

## Self-verifying design — make the report prove itself

Statutory formats are legal specs; a line that silently drops an account is a
compliance bug you won't see. Build the proof into the report:

1. **Catch-all lines that must equal 0** — for each account class, one line
   whose domain is the complement of everything mapped
   (`['&', ('account_id.code','=like','1%'), '!', …every mapped prefix…]`).
   Non-zero render = an account fell through the mapping.
2. **Identity line** — assets − (liabilities+equity) as an `aggregation`
   expression that must render 0.
3. **Reconciliation pair** — compute one figure two independent ways (e.g.
   indirect cash-flow: top-down net-change line vs Δcash from the ledger
   `from_beginning` minus `to_beginning_of_period`) and show the delta line.
   It must be 0 to the cent; when it isn't, the delta *is* your bug size.
4. **Suspense visibility** — class-0 / migration / suspense accounts get an
   explicit visible line, never a silent exclusion. (Real case: an opening
   -balance conversion account holding 12 bn silently broke the identity.)
5. **Prefix-overlap audit** — `'15%'` ⊃ `'153%'` double-counts; exclude with
   `'!', ('account_id.code','=like','153%')`. Then run one SQL that assigns
   every account to exactly one leaf line: 0 uncovered, 0 double-mapped.

## Versioning by accounting regime

When the law changes (new chart, new line codes): **never edit the old report
in place.** Rename it with a suffix ("… (old regime, until 2025)"), ship the
new report side-by-side. Prior-year comparatives and restatements need the
old one alive. Same for accounts: deprecate (`deprecated=True`), never delete.

New root reports surface via an `ir.actions.client` with
`tag="account_report"` and `context="{'report_id': ref(...)}"` (+ menuitem),
or as a variant of an existing menu via `root_report_id`.

## Render-test ritual (odoo shell — do this after every change)

```python
report = env.ref("my_module.my_report")
opts = report.get_options({"date": {"date_from": "2026-01-01", "date_to": "2026-06-30",
                                    "mode": "range", "filter": "custom"}})
for l in report._get_lines(opts):
    print(l["name"], [c.get("no_format") for c in l["columns"]])
```

- The date dict goes **into** `get_options()`. Mutating `opts["date"]`
  afterwards does **not** recompute (columns/comparisons are already
  expanded) — you'll render last month's numbers and not know it.
- Cross-check at least the top lines against raw SQL on `account_move_line`.

## Gotchas

- The comparison toggle field is **`filter_period_comparison`** — there is no
  `filter_comparison`; the wrong name fails at record load.
- Report lines are ordinary XML data: removing a line from the file does
  **not** delete it from an installed DB — clean up explicitly.
- If the DB books year-end closing entries (911-style), P&L `strict_range`
  domains must exclude the closing journal or they zero out at year end.
- After `-u`, a *new* wizard's transient table can lag one restart — check
  `SELECT to_regclass('table')` before blaming your code.
- Mandatory-analytic validation (`account.analytic.applicability`) only fires
  with the UI post button's `validate_analytic=True` context — a shell
  `action_post()` bypasses it; test through the context, not just the ORM.

## When `account.report` is the WRONG tool

Multi-sheet data packs, roll-forwards (fixed-asset movement schedules),
per-record cost cards, disclosure notes: a flat grid can't hold them. Use a
transient wizard + `xlsxwriter` in-memory + attachment download:

```python
buf = io.BytesIO(); wb = xlsxwriter.Workbook(buf, {"in_memory": True})
# … one worksheet per schedule, SQL straight from the ledger/SVL …
wb.close()
att = self.env["ir.attachment"].create({"name": fname, "datas": base64.b64encode(buf.getvalue())})
return {"type": "ir.actions.act_url", "url": f"/web/content/{att.id}?download=true", "target": "self"}
```

Narrative disclosures stay human-written — ship the numbers pack, not prose.

## Cross-references

- `odoo-introspect` — read the existing report's lines/expressions from the
  registry before touching anything.
- `odoo-reports` — printable QWeb PDF/HTML documents (different world).
- `odoo-domain-playbooks/references/vietnam-accounting.md` — a full country
  regime map (VAS/TT99) that this skill's techniques implement.
- `odoo-worktree` — packaging these data-heavy addons on an isolated branch.
