# Website pages, snippets & the portal

Authoring the public website and extending `/my`, plus the v17/18 frontend-JS
migration. Targets Odoo 17/18, through Odoo 19 (current LTS).

## Website page anatomy

A website page is a QWeb `ir.ui.view` (optionally with a `website.page` record
that gives it a URL without a controller).

```xml
<template id="books_page" name="Library Books">
    <t t-call="website.layout">                 <!-- header / footer / assets -->
        <div id="wrap" class="oe_structure">     <!-- oe_structure = drag-drop snippets area -->
            <div class="container py-4">
                <h1>Books</h1>
                <t t-foreach="books" t-as="book">
                    <article class="card mb-2" t-att-data-id="book.id">
                        <h3 t-field="book.name"/>
                        <p t-out="book.summary"/>
                    </article>
                </t>
            </div>
        </div>
    </t>
</template>
```

- **`website.layout`** is the shared shell — never edit it; `t-call` it.
- **`oe_structure`** marks an editable region where website editors can drop
  snippets; give each a unique id so edits persist per page.
- Serve it either from a controller (`request.render('module.books_page', vals)`)
  or as a routeless `website.page`:

```xml
<record id="books_page_record" model="website.page">
    <field name="name">Books</field>
    <field name="url">/books</field>
    <field name="view_id" ref="books_page"/>
    <field name="is_published" eval="True"/>
</record>
```

## Customize an existing website template

Inherit, don't fork (same mechanics as `odoo-views`):

```xml
<template id="custom_footer" inherit_id="website.footer_custom">
    <xpath expr="//footer" position="inside">
        <small>Library Co.</small>
    </xpath>
</template>
```

Confirm the parent `key`/id via introspect (Layer C lists website views) — a
wrong xpath silently no-ops, exactly like backend views.

## Snippets (drag-drop blocks)

A snippet = a template + a registration into a snippet group + optional
`options`/`editor` JS in `web.assets_frontend`:

```xml
<template id="s_book_promo" name="Book Promo">
    <section class="s_book_promo">...</section>
</template>

<!-- make it appear in the editor's "Inner content" group -->
<template id="book_promo_snippet" inherit_id="website.snippets">
    <xpath expr="//snippets[@id='snippet_structure']" position="inside">
        <t t-snippet="my_module.s_book_promo" t-thumbnail="/my_module/static/img/promo.svg"/>
    </xpath>
</template>
```

Interactive behaviour attaches by CSS class via `publicWidget`/Interactions
(below). Snippet options (the editor side panel) register under
`web_editor`/`website` editor registries — read a core snippet's options file
before writing one.

## Multi-website & i18n

- `website=True` on the route binds `request.website` and the active language;
  use `request.website.get_current_website()` / `request.lang`.
- Per-website content: filter on `website_id` (records inherit `website.multi.mixin`)
  or use `website_id`-aware domains. Don't hardcode a website id.
- Translate website templates via the website editor / `.po` terms, not
  per-language template copies (→ `odoo-data` i18n note).

## Portal — the `/my` extension contract

The `portal` addon defines a fixed extension surface. Extend it; don't rebuild.

```python
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.http import request

class LibraryPortal(CustomerPortal):

    # 1. counter on the /my home cards
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'book_count' in counters:
            values['book_count'] = request.env['library.loan'].search_count(
                [('partner_id', '=', request.env.user.partner_id.id)])
        return values

    # 2. a paginated list page
    @http.route(['/my/loans', '/my/loans/page/<int:page>'], type='http',
                auth='user', website=True)
    def my_loans(self, page=1, **kw):
        Loan = request.env['library.loan']
        domain = [('partner_id', '=', request.env.user.partner_id.id)]
        total = Loan.search_count(domain)
        pager = request.website.pager(url='/my/loans', total=total, page=page, step=20)
        loans = Loan.search(domain, limit=20, offset=pager['offset'])
        return request.render('my_module.portal_my_loans',
                              {'loans': loans, 'pager': pager, 'page_name': 'loan'})
```

### Signed-token document access (email links)

A customer opens a document from an email without logging in via a signed
`access_token`:

```python
@http.route(['/my/loan/<int:loan_id>'], type='http', auth='public', website=True)
def portal_loan(self, loan_id, access_token=None, **kw):
    try:
        loan_sudo = self._document_check_access('library.loan', loan_id, access_token)
    except (AccessError, MissingError):
        return request.redirect('/my')
    return request.render('my_module.portal_loan_page', {'loan': loan_sudo})
```

- `_document_check_access` (from `CustomerPortal`) validates the token and
  returns a sudo'd record only if the token matches — use it, don't reimplement.
- The model must inherit `portal.mixin` to get `access_url` / `_portal_ensure_token()`
  (check `capabilities.portal` in the Layer A brief).
- **Never** build a `/my/<doc>` page that skips the token check — it either 403s
  legitimate links or leaks another partner's document.

## Frontend JS: `publicWidget` → Interactions

Public-page JS is **not** OWL. Know which framework the target uses.

### `publicWidget` (the long-standing way)

```js
/** @odoo-module **/
import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.BookPromo = publicWidget.Widget.extend({
    selector: ".s_book_promo",
    events: { "click .borrow": "_onBorrow" },
    start() { return this._super(...arguments); },
    _onBorrow(ev) {
        ev.preventDefault();
        // this.$el is jQuery-ish; rpc via the json route
    },
});
```

- Binds to a CSS `selector`, auto-started on every matching element.
- Note the `@web/legacy/js/public/public_widget` import path — the bare
  `web.public.widget` name is older and may differ by version; read the source.

### Interactions (the newer framework Odoo is migrating to)

```js
/** @odoo-module **/
import { Interaction } from "@web/public/interaction";
import { registry } from "@web/core/registry";

class BookPromo extends Interaction {
    static selector = ".s_book_promo";
    dynamicContent = { ".borrow": { "t-on-click": "onBorrow" } };
    onBorrow(ev) { /* ... */ }
}
registry.category("public.interactions").add("my_module.book_promo", BookPromo);
```

- Class-based, declarative `dynamicContent`, lifecycle closer to OWL but **not**
  an OWL component.
- Availability and exact API depend on the version/addon — **read the addon's
  own interactions before writing**; don't assume from memory (the migration is
  in progress across 17→18+).

### Bundle wiring

Both load via `__manifest__['assets']` → **`web.assets_frontend`** (public pages),
not `web.assets_backend` (the OWL client). JS *and* the template XML must be
globbed in. Wrong bundle ⇒ the script silently never runs on the page.

## Gotchas (frontend-specific)

- Mixing `publicWidget` and Interactions / OWL in one file — pick the framework
  the target version+addon actually uses.
- Assuming `request.env.user` is meaningful under `auth='public'` — it's the
  public user; under `auth='none'` it isn't set up at all.
- Editing `website.layout` / `portal.portal_layout` instead of inheriting the
  specific template — breaks every page.
- Forgetting `website=True` — page renders but loses lang/multi-site/sitemap.
- jQuery assumptions — v17/18 frontend is moving off jQuery; check what's
  actually loaded before using `$`.
