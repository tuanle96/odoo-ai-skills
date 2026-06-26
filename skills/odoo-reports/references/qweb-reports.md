# QWeb reports — full reference

Verified against Odoo 18.0. Version floor 17/18.

## ir.actions.report fields

| Field | Meaning |
|-------|---------|
| `name` | human label; the PDF file name **if** `print_report_name` is unset |
| `model` | model the report runs over (its records become `docs`) |
| `report_type` | `qweb-pdf` (via wkhtmltopdf) · `qweb-html` (in-browser) · `qweb-text` (plain text) |
| `report_name` | **external id of the QWeb template** — the join key to everything |
| `report_file` | template ref used to derive the file base (usually = `report_name`) |
| `print_report_name` | Python expr for the file name; record is `object`, e.g. `'INV-%s' % object.name` |
| `paperformat_id` | `report.paperformat` (falls back to the company default) |
| `binding_model_id` / `binding_type` | put the report in that model's **Print** menu (`binding_type="report"`) |
| `groups_id` | restrict who can run it |
| `attachment` | Python expr → store the PDF as an attachment under this name |
| `attachment_use` | `True` = generate once, then always serve the stored copy (immutable docs) |

## The parser contract

You only need a parser AbstractModel when the template references variables **beyond** what the default context provides.

Default context passed to every report: `docs` (recordset), `doc_ids`, `doc_model`, `company`, `user`, plus any `data` dict.

```python
from odoo import api, models

class CommissionReport(models.AbstractModel):
    _name = "report.my_module.report_commission"     # MUST be "report." + report_name
    _description = "Commission Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["sale.order"].browse(docids)
        # to inspect the action: self.env["ir.actions.report"]._get_report_from_name("my_module.report_commission")
        return {
            "doc_ids": docids,
            "doc_model": "sale.order",
            "docs": docs,
            "rates": {o.id: o.amount_total * 0.1 for o in docs},   # the extra data
        }
```

Render programmatically: `self.env.ref("my_module.action_report_commission").report_action(records)` returns the client action; the low-level `self.env["ir.actions.report"]._render_qweb_pdf(report_ref, res_ids)` returns `(pdf_bytes, "pdf")` (first arg is the report ref/xmlid in 17/18).

## Template anatomy

```
web.html_container          ← outer <html>, pulls in report CSS assets
└─ t-foreach="docs"         ← one iteration per record
   └─ web.external_layout   ← company header (logo/address) + footer (page #, company info)
      └─ <div class="page"> ← YOUR content; one such div ≈ one logical page start
```

- `web.external_layout` dispatches on `company.external_report_layout_id` to a variant: `external_layout_standard | _boxed | _bold | _striped | _folder | _wave | _bubble`. Configure via Settings → Companies → *Configure Document Layout*; don't hardcode a variant.
- `web.internal_layout` = minimal header (date, company, page numbers), no branding — for internal printouts.
- **Never inherit/modify `external_layout*` or `html_container`** to change one report; they are global. Scope changes to your `*_document` template.

## report.paperformat

`format` (`A4`, `Letter`, … or `custom` with `page_width`/`page_height` in mm) · `orientation` (`Portrait`/`Landscape`) · `margin_top/bottom/left/right` (mm) · `header_line` (bool) · `header_spacing` (mm — gap reserved for the wkhtmltopdf header band) · `dpi` (default 90).

```xml
<record id="paperformat_label" model="report.paperformat">
    <field name="name">Label 60x40</field>
    <field name="format">custom</field>
    <field name="page_width">60</field>
    <field name="page_height">40</field>
    <field name="orientation">Portrait</field>
    <field name="margin_top">2</field><field name="margin_bottom">2</field>
    <field name="margin_left">2</field><field name="margin_right">2</field>
    <field name="header_spacing">0</field>
    <field name="dpi">300</field>
</record>
```

## Outputting values

| Want | Use |
|------|-----|
| A formatted field (currency, date, lang-aware) | `<span t-field="o.amount_total" t-options='{"widget":"monetary","display_currency":o.currency_id}'/>` |
| Plain escaped value | `<t t-out="o.name"/>` (replaces removed `t-raw`; `t-esc` still valid) |
| Computed expression | `<t t-out="rates[o.id]"/>` (from the parser) |
| Date / datetime widget | `t-options='{"widget":"date"}'` / `"datetime"` / `"float_time"` |
| Barcode / QR | `<img t-att-src="'/report/barcode/Code128/%s' % o.name"/>` (or `QR`) |

`t-field` requires a real `browse` record (not a dict) and auto-formats per the field type and the active `lang` — prefer it for money/dates.

## Translation (per-recipient language)

```xml
<template id="report_commission">
    <t t-call="web.html_container">
        <t t-foreach="docs" t-as="o">
            <t t-set="lang" t-value="o.partner_id.lang or o.company_id.partner_id.lang"/>
            <t t-call="my_module.report_commission_document" t-lang="lang"/>
        </t>
    </t>
</template>

<template id="report_commission_document">
    <t t-set="o" t-value="o.with_context(lang=lang)"/>   <!-- re-browse so t-field formats in lang -->
    <t t-call="web.external_layout">
        <div class="page">…</div>
    </t>
</template>
```

`t-lang` on the `t-call` translates the **template terms**; `with_context(lang=…)` makes `t-field` values (dates, selection labels, monetary) localize too. Need both.

## Page breaks, headers, wkhtmltopdf

- Force a break: `<div style="page-break-before: always;"/>` (or `page-break-after`). A new `<div class="page">` does **not** itself force a page.
- Long tables: wkhtmltopdf repeats `<thead>` on each page automatically; add `tr { page-break-inside: avoid; }` to stop rows splitting.
- The wkhtmltopdf **header/footer are rendered in a separate pass** from the body — variables/CSS available to the body may be missing in header/footer; page numbers come from `<span class="page"/>` / `<span class="topage"/>` (provided by the layout, substituted by wkhtmltopdf).
- Report CSS belongs in the `web.report_assets_common` (HTML+PDF) or `web.report_assets_pdf` (PDF-only) bundles — **not** `web.assets_backend`. Inline `style=""` always works.

## Debugging

- View the HTML (with a real traceback on errors, unlike the silent PDF): browse to `/report/html/<report_name>/<record_ids>`, e.g. `/report/html/my_module.report_commission/42`. The PDF route is `/report/pdf/<report_name>/<ids>`.
- Empty value in the PDF → the variable isn't in the context: add/extend the parser (see above).
- "QWeb … cannot find template" → `report_name` ≠ template id, or the template's XML file isn't in `__manifest__['data']`.
- Wrong layout / branding → you edited a global layout, or the company's *Document Layout* differs from what you assumed.
- Check the engine: `wkhtmltopdf --version` should report `(with patched qt)`; an unpatched build breaks headers/footers and page numbers.
