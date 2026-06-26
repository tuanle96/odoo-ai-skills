---
name: odoo-reports
description: >-
  Authoring or customizing Odoo 17/18 QWeb reports — the printable PDF/HTML
  documents (invoices, sale orders, pickings, custom certificates). Use when
  creating an ir.actions.report, writing or inheriting a QWeb report template,
  adding a report.<module>.<name> parser to feed extra data, fixing paperformat
  / margins / page breaks, translating a report per partner language, or
  debugging "my changes don't show / wrong template / values undefined in the
  PDF". Don't guess the template id or whether a parser exists — find the
  existing report with the `odoo-introspect` skill first, then inherit.
---

# Odoo QWeb reports (PDF / HTML)

An Odoo report is **three layers**, and most bugs come from touching the wrong one:

| Layer | What it is | Lives in |
|-------|-----------|----------|
| **Action** | `ir.actions.report` — `model`, `report_name`→template, `report_type`, `paperformat_id`, Print-menu binding | data XML |
| **Template** | QWeb: `web.html_container` → `web.external_layout` → your `<div class="page">` | data XML (loaded via `__manifest__['data']`) |
| **Data** | *optional* `models.AbstractModel` named `report.<module>.<report_name>` with `_get_report_values()` | Python |

PDFs are rendered from the HTML by **wkhtmltopdf** (0.12.6, qt-patched). Version floor: Odoo 17/18.

**The rule: there is almost always an existing report — find its action, template, and parser, then *inherit*. Don't rebuild and don't guess ids.**

## Discover before you customize (introspect-first)

Use the `odoo-introspect` skill:

- `metadata.py` enumerates `ir.actions.report` records with their QWeb template + paperformat; `entrypoints.py` (reads the resolved `get_view`) surfaces the reports bound to a model (the **Print** menu). Get `report_name` → the template external id → whether a `report.<module>.<name>` AbstractModel parser already exists. Never hand-guess the template id.
- **Two-template trap:** real reports split into a *main* template (loops `docs`, sets `lang`) and a `*_document` *sub-template* (the actual layout). Customize the **sub-template**. **Never** edit `web.external_layout` / `web.html_container` — they are shared by every report in the database.

## Declare a report

```xml
<record id="action_report_commission" model="ir.actions.report">
    <field name="name">Commission</field>                         <!-- fallback file name -->
    <field name="model">sale.order</field>
    <field name="report_type">qweb-pdf</field>                    <!-- qweb-pdf | qweb-html | qweb-text -->
    <field name="report_name">my_module.report_commission</field> <!-- = template external id -->
    <field name="report_file">my_module.report_commission</field>
    <field name="print_report_name">'Commission - %s' % (object.name)</field>
    <field name="binding_model_id" ref="sale.model_sale_order"/>  <!-- shows it in Print menu -->
    <field name="binding_type">report</field>
    <field name="paperformat_id" ref="base.paperformat_euro"/>
</record>
```

`<report id="…" string="…" model="…" report_type="qweb-pdf" name="…" file="…"/>` is shorthand that **auto-creates the Print-menu binding**.

## Minimal template

```xml
<template id="report_commission">
    <t t-call="web.html_container">
        <t t-foreach="docs" t-as="o">
            <t t-set="lang" t-value="o.partner_id.lang"/>
            <t t-call="web.external_layout" t-lang="lang">
                <div class="page">
                    <h2>Commission <span t-field="o.name"/></h2>
                    <p>Total: <span t-field="o.amount_total"
                                    t-options='{"widget": "monetary", "display_currency": o.currency_id}'/></p>
                </div>
            </t>
        </t>
    </t>
</template>
```

## Customize an existing report

- **Layout** → inherit the `*_document` template with xpath:
  ```xml
  <template id="custom_so" inherit_id="sale.report_saleorder_document">
      <xpath expr="//div[@class='page']" position="inside">
          <p>Sales rep: <span t-field="doc.user_id"/></p>
      </xpath>
  </template>
  ```
- **Extra data** → add/extend the parser (only needed for values not already on the records):
  ```python
  class CommissionReport(models.AbstractModel):
      _name = "report.my_module.report_commission"
      def _get_report_values(self, docids, data=None):
          docs = self.env["sale.order"].browse(docids)
          return {"doc_ids": docids, "doc_model": "sale.order",
                  "docs": docs, "rates": self._compute_rates(docs)}
  ```

See `references/qweb-reports.md` for the parser contract, paperformat fields, translation, t-field/t-out, wkhtmltopdf header/footer, and the full gotcha list.

## Gotchas that fail silently

- **Editing the wrong template** — your xpath targets the *main* wrapper, but the layout is in the `*_document` sub-template; nothing visibly changes. (Confirm the id via introspect.)
- **Default context only gives you `docs`, `doc_ids`, `doc_model`.** Reference any other variable and it's blank in the PDF — you need a `report.<module>.<name>` parser returning it. No parser model = silent `undefined`/empty, not an error.
- **`report_name` must equal the template external id** (`module.tmpl`), and the parser `_name` must be `report.` + that id. A mismatch = "report not found" or empty render.
- **Paperformat mismatch** (A4 vs Letter, too-small margins) clips content or collides with the wkhtmltopdf header/footer band — set `paperformat_id` and `header_spacing`.
- **PDF ≠ on-screen HTML.** wkhtmltopdf is an old WebKit: flexbox/grid are unreliable (use Bootstrap grid/tables), and report CSS must be in `web.report_assets_common` / `web.report_assets_pdf`, not the backend bundle.
- **Translation needs `t-lang`** on the `t-call` *and* the document re-browsed with `with_context(lang=…)`; otherwise everything prints in the user's language, not the customer's.

## References

- `references/qweb-reports.md` — full anatomy: parser contract, paperformat, layouts, translation, t-field options, page breaks, header/footer, debugging (`?report.html`).
