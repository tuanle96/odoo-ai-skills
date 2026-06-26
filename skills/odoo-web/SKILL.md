---
name: odoo-web
description: >-
  Building Odoo 17/18 PUBLIC web — HTTP controllers (http.route), the website
  (pages, QWeb website templates, snippets), the portal (/my, document access),
  and frontend interactivity. Use whenever writing anything under controllers/,
  a route an external system or browser hits, a website page/template, a portal
  document view, or public JS in web.assets_frontend — even if the user never
  says "skill". This is the PUBLIC frontend (controllers + website + portal +
  the v17/18 publicWidget→Interactions shift), distinct from the backend web
  client (that's odoo-owl). Routes run with real auth and CSRF and against real
  record rules — read the wiring and security from the running instance first,
  never guess the route, the template id, or who can reach it. Targets Odoo
  17/18.
---

# Odoo public web (controllers · website · portal)

The backend web client is OWL (→ `odoo-owl`). **Everything a browser or external
system hits without the backend client is this skill**: an `http.route`, a
website page, a portal document, a snippet, public JS. It fails the same way the
rest of Odoo does — the route, the template, and *who can reach it* are composed
at runtime from the installed addon graph and the acting user's groups, not
knowable from one file. And the public surface adds two stakes the backend
doesn't: **auth** (a wrong `auth=` exposes data to the world) and **the v17/18
frontend-JS rewrite** (`publicWidget` is being replaced by Interactions).

**The rule: read the real route table, template ids, and record-rule reach from
the running instance first, then write the smallest controller/template — never
guess the URL, the QWeb id, or the auth level.**

**Version floor: Odoo 17/18.** Frontend JS is mid-migration — see the
`publicWidget` vs Interactions note below and `skills/odoo-introspect/references/version-matrix.md`.

## Discover before you code (introspect-first)

Public wiring is ground truth too — don't grep for it, read it:

- **Routes** — list what's actually registered (and at which auth) with the
  introspect engine's route scan, or in `odoo-bin shell`:
  `env['ir.http']._routing_map()` enumerates every live `http.route`. A route you
  "add" that collides with an existing path silently loses to load order.
- **Website templates / pages** — `metadata.py` (Layer C) lists `ir.ui.view`
  pages and `website.page`. The `key` (`module.template_name`) is what you
  `t-call` and inherit — never hand-guess it (→ same trap as `odoo-reports`).
- **Portal reach** — portal documents are gated by **record rules**, not by your
  controller code. Dump them with `odoo-security` (`record_rules`) before
  assuming `/my/orders` shows the right rows for a portal user.

## The three auth levels — get this exactly right

`auth=` on `@http.route` is the single highest-stakes decision on the public web:

| `auth=` | Who reaches it | `request.env.user` | Use for |
|---|---|---|---|
| `'user'` | logged-in users only | the real user | backend-ish JSON endpoints, anything per-user |
| `'public'` | **everyone**, logged in or not | the **public** user (a real low-priv user) | website pages, public forms |
| `'none'` | everyone; no env user setup | — | webhooks, health checks, OAuth callbacks |

- `auth='public'` does **not** mean "no security" — it runs as the *public user*,
  who is constrained by ACL + record rules. To read a record for an anonymous
  visitor you usually `sudo()` **a single narrow query** with a written reason
  (→ `odoo-security` discipline), never the whole controller.
- Switching a route from `user` to `public` to "make it work" is the web
  equivalent of blanket `sudo()` — it exposes the endpoint to the internet.

## Controllers — the shape that works

```python
from odoo import http
from odoo.http import request

class Library(http.Controller):

    @http.route('/library/books', type='http', auth='public', website=True, sitemap=True)
    def books(self, **kw):
        books = request.env['library.book'].search([('public', '=', True)])
        return request.render('library.books_page', {'books': books})

    @http.route('/library/api/book/<int:book_id>', type='json', auth='user', methods=['POST'])
    def book_json(self, book_id, **kw):
        book = request.env['library.book'].browse(book_id).exists()
        return {'name': book.name, 'available': book.available}
```

- **`type='http'`** returns a `Response` / `request.render(...)` / `request.redirect(...)`.
  **`type='json'`** takes & returns JSON (args come from the JSON body, not the
  query string) — used by OWL's `rpc`/`orm` and by external callers.
- **`website=True`** wires the page into the website (multi-website, language,
  the `website` record on `request`); **`sitemap=True`** lists it in `/sitemap.xml`.
- **`csrf`**: `type='http'` **POST** needs a CSRF token (forms get it from
  `<form>` rendering). External POSTs that can't carry the token must set
  `csrf=False` **and** authenticate another way (`type='json'`, a token, or
  `auth='none'` + signature) — don't disable CSRF on a session-auth route.
- **`browse(id).exists()`** before using it — a guessed/stale id is a
  `MissingError` waiting to 500 a public page.
- **Controller inheritance**: to extend a core controller, subclass it and call
  `super()`; the route is re-registered by path. Wire the file in
  `controllers/__init__.py` (→ `odoo-module-scaffold`; an unimported controller
  is dead, no error).

## Website pages & templates

```xml
<!-- a standalone website page -->
<template id="books_page" name="Library Books">
    <t t-call="website.layout">
        <div class="container">
            <h1>Books</h1>
            <t t-foreach="books" t-as="book">
                <div class="card" t-att-data-id="book.id">
                    <span t-field="book.name"/>
                </div>
            </t>
        </div>
    </t>
</template>
```

- `t-call="website.layout"` gives header/footer/assets — the website equivalent
  of `web.external_layout` for reports. **Never edit `website.layout` itself.**
- **Customize an existing website template** by *inheriting* it with `inherit_id`
  + xpath (same mechanics as `odoo-views`), not by forking. Confirm the `key`/id
  via introspect first — a wrong xpath silently no-ops.
- **Snippets** (drag-drop website blocks) are templates registered into a snippet
  group + an `options` JS/XML; they live in `web.assets_frontend`.
- `t-field` on website QWeb gives inline editing in edit mode; `t-out`/`t-esc`
  for plain values (`t-raw` is removed → `odoo-owl` note).

## Portal — extend `/my`, don't rebuild it

Portal documents follow a fixed contract (the `portal` / `portal.mixin` addons):

- Add a counter to the `/my` home by overriding `_prepare_home_portal_values`.
- Add a list page by overriding `_prepare_portal_layout_values` / a `/my/<docs>`
  route, paginated with `portal.pager`.
- A document detail page (`/my/order/<id>`) is reached via a **signed access
  token** (`access_token`) so a customer can open it from an email without
  logging in — validate it with the model's `_portal_ensure_token` /
  `access_url` helpers; don't roll your own.
- The model must inherit `portal.mixin` to get `access_url` / `get_portal_url()`
  (check `capabilities.portal` in the Layer A brief).

## Frontend JS — `publicWidget` vs Interactions (the v17/18 trap)

Public-page JS is **not** OWL by default. Historically it's `publicWidget`
(`@web/legacy/js/public/public_widget`); Odoo is migrating this to the new
**Interactions** framework (`@web/public/interaction`). Don't blend them.

```js
/** @odoo-module **/
import publicWidget from "@web/legacy/js/public/public_widget";

publicWidget.registry.LibraryCard = publicWidget.Widget.extend({
    selector: ".library_card",
    events: { "click": "_onClick" },
    _onClick(ev) { /* ... */ },
});
```

- `publicWidget` binds to a CSS `selector` and is started on every matching
  element on page load — it is **not** an OWL component; OWL hooks/`useState`
  don't apply here.
- **Interactions** (newer) replace it with a class extending `Interaction`
  (`@web/public/interaction`) registered via `registry.category("public.interactions")`.
  Check which the target version/addon uses by reading the addon source before
  writing (→ the OWL skill's "read the canonical source" rule applies here too).
- Both load via `web.assets_frontend` in `__manifest__['assets']` — **not**
  `web.assets_backend` (that's the OWL client). Wrong bundle ⇒ script never runs,
  no error.

## Gotchas that fail silently

- **`auth='public'` ≠ unsecured.** It runs as the public user; ACL + record
  rules still apply, so `/my`-style data needs the right rules or a *narrow*
  `sudo()`, not a blanket one. And `auth='none'` does no user setup at all —
  don't touch `request.env` expecting a user.
- **Route collision** — two routes on the same path resolve by module load
  order; the loser never runs and nothing is logged. Dump `_routing_map()`.
- **CSRF disabled on a session route** — `csrf=False` on an `auth='user'` POST
  opens it to cross-site forgery; only disable CSRF for token/`json`/signed
  endpoints.
- **Wrong assets bundle** — frontend JS/SCSS dropped into `web.assets_backend`
  (or vice-versa) silently never loads on the page that needs it.
- **Editing `website.layout` / `portal.portal_layout`** — shared by every page;
  inherit the specific template instead (same trap as the report `external_layout`).
- **`website=True` forgotten** — the page renders but loses multi-website /
  language routing and never appears in the sitemap.
- **No `.exists()` after `browse`** — a public URL with a guessed id 500s instead
  of 404ing; guard it and `request.not_found()` deliberately.
- **Portal page bypassing the access token** — building `/my/<doc>` without
  validating `access_token` either 403s legitimate email links or, worse, leaks
  another customer's document.

## References & related skills

**This skill's references**
- `references/controllers-routing.md` — full `http.route` option matrix
  (`type`/`auth`/`methods`/`csrf`/`website`/`sitemap`/`save_session`), JSON vs
  HTTP request handling, controller inheritance + `super()`, redirects/responses,
  reading the live route map.
- `references/website-portal.md` — website page/template/snippet authoring,
  `website.layout`, multi-website & i18n routing, the portal `/my` extension
  contract (home values, list pagination, signed access tokens), and the
  `publicWidget`→Interactions migration in detail.

**Other skills in the loop**
- `odoo-introspect` — route map (`_routing_map()`), website pages (Layer C),
  portal record-rule reach (security dossier). Read first.
- `odoo-owl` — the **backend** web client (OWL components, field widgets). A
  client action or backend widget is OWL, not this skill.
- `odoo-security` — `auth=` + record rules decide who reaches a route/document;
  `sudo()` discipline for public reads.
- `odoo-reports` — same "find the real template id, inherit, don't edit shared
  layout" discipline for QWeb PDFs.
- `odoo-testing` — `HttpCase` + JS tours to prove a controller/page/portal flow.
