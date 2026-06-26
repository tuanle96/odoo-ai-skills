# Controllers & routing — the full surface

Everything an Odoo controller needs that the SKILL.md summarizes. Targets Odoo 17/18.

## `@http.route` options

```python
@http.route(
    '/path/<int:rec_id>',     # one or more URL patterns (str or list)
    type='http',              # 'http' | 'json'
    auth='user',              # 'user' | 'public' | 'none'
    methods=['GET', 'POST'],  # allowed HTTP verbs (default: all)
    website=True,             # wire into website (request.website, lang, multi-site)
    sitemap=True,             # list in /sitemap.xml (or a callable)
    csrf=True,                # CSRF check on http POST (default True)
    save_session=True,        # set False for stateless/token endpoints to skip session cookie churn
    cors='*',                 # set CORS header for cross-origin JSON APIs
    readonly=True,            # v17.4+: route uses a read-only cursor (perf; no writes allowed)
)
def handler(self, rec_id, **kw):
    ...
```

| Option | Decides | Failure if wrong |
|---|---|---|
| `type` | request/response encoding (`http`=form/query+Response, `json`=JSON body+JSON) | JSON args arrive empty if you set `type='http'` for a JSON caller |
| `auth` | who reaches it + which user runs (see SKILL table) | data exposed (`public`) or 403 (`user`) |
| `methods` | allowed verbs | 405 for the right URL with the wrong verb |
| `csrf` | CSRF token check on `http` POST | session route open to forgery if `False` |
| `website` | multi-website + lang + `request.website` | page works but loses i18n/multi-site routing |
| `sitemap` | SEO listing | page invisible to crawlers |
| `save_session` | session cookie writes | needless session contention on high-traffic APIs |

## URL converters

`<int:id>`, `<string:slug>`, `<model("sale.order"):order>` (auto-browses & 404s on miss),
`<int(min=1):page>`. The `model()` converter respects record rules — a record the
current (possibly public) user can't see is a 404, which is usually what you want.

## HTTP vs JSON request handling

```python
# type='http' — query string / form fields land in **kw; return a Response
@http.route('/library/search', type='http', auth='public', website=True)
def search(self, q='', **kw):
    res = request.env['library.book'].search([('name', 'ilike', q)])
    return request.render('library.results', {'res': res})

# type='json' — args come from the JSON body; return a plain dict/list
@http.route('/library/api/borrow', type='json', auth='user', methods=['POST'])
def borrow(self, book_id, **kw):
    book = request.env['library.book'].browse(int(book_id)).exists()
    if not book:
        return {'error': 'not_found'}
    book.action_borrow()
    return {'ok': True, 'state': book.state}
```

Responses for `type='http'`: `request.render(template, values)`,
`request.redirect(url)`, `request.make_response(body, headers)`,
`request.not_found()`, or `werkzeug` responses. `type='json'` just returns the
Python object (Odoo serializes it); raising is turned into a JSON error envelope.

## Reading the live route table (introspect, don't grep)

```python
# inside odoo-bin shell -d <DB>
rmap = env['ir.http']._routing_map()
for rule in rmap.iter_rules():
    print(rule.rule, rule.endpoint.routing.get('auth'), rule.methods)
```

This is ground truth: every registered path, its `auth`, its methods, after the
whole addon graph composed. Use it to spot **collisions** (two modules claiming
the same path — load order decides the winner, silently) and to confirm a core
route's real signature before you subclass it.

## Extending a core controller

```python
from odoo.addons.website_sale.controllers.main import WebsiteSale

class WebsiteSaleCustom(WebsiteSale):
    @http.route()                       # re-use the parent's route definition
    def shop(self, **kw):
        res = super().shop(**kw)        # keep the original behaviour
        # ... augment res / the rendering values ...
        return res
```

- Subclass the controller class and redefine the method with a bare
  `@http.route()` (inherits the parent's path/auth) — Odoo re-registers by path.
- Always `super()` unless you deliberately replace the response.
- The file **must** be imported in `controllers/__init__.py`, which must be
  imported from the module `__init__.py` — an unwired controller is dead code
  with no error (→ `odoo-module-scaffold`).

## Security on the public web

- `auth='public'` runs as the **public user** (`base.public_user`), a real,
  low-privilege user bound by ACL + record rules. Reading anything it can't see
  needs a **narrow, commented** `sudo()` on that one query — never a
  controller-wide `sudo()` (→ `odoo-security`).
- `auth='none'` does **no** user setup; `request.env` has no meaningful user.
  Use it for webhooks/health checks and authenticate by signature/token yourself.
- Validate every external input: cast/`int()`, `.exists()` after `browse`,
  `request.validate_csrf(...)` semantics for forms, and never interpolate user
  input into a domain or SQL (→ `odoo-perf` raw-SQL parameterization rule).

## Testing controllers

Use `HttpCase` (→ `odoo-testing`): `self.url_open('/path')` for a plain request,
`self.start_tour(...)` for a click-through in headless Chrome. Tag `post_install`
so all assets/routes are loaded.
