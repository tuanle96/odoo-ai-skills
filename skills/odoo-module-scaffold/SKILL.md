---
name: odoo-module-scaffold
description: >-
  Creating a new Odoo module/addon from scratch, or fixing a module that won't
  install — __manifest__.py (depends, the data load order, assets bundles),
  __init__ wiring, directory layout (models/views/security/data/static/wizard/
  report/controllers), and the model + ir.model.access.csv + menu/action/view
  skeleton that makes a module actually appear. Use whenever scaffolding an Odoo
  addon, adding the first model to a module, wiring a new .py into __init__, or
  deciding which addons to `depends` on — even if the user never says "skill".
  `depends` is not cosmetic: it fixes which MRO layer your overrides land at, so
  read the recommended depends from the `odoo-introspect` skill (model_brief)
  before writing it. Never guess the structure.
---

# Odoo module scaffold

A module installs only when its declared structure matches what the registry expects: every Python file imported, every data file listed in `__manifest__.py`, and listed **in dependency order**. A model file you forgot to import, or a security file loaded after the view that needs its group, fails — sometimes loudly, often silently (the element just never appears).

**Version floor: Odoo 17/18.** Layout/manifest below is current; pre-17 deltas → `skills/odoo-introspect/references/version-matrix.md`.

## `depends` decides your MRO layer — introspect first

`depends` is the one manifest key with weight beyond "make it install": it sets module load order, which sets where your class lands in the model's MRO. Override `sale.order` while depending only on `sale` (not `sale_stock`) and your method sits at a *different layer* than the stock hooks — `super()` then reaches a different chain than you expect.

Before writing `depends`, run the `odoo-introspect` skill's **model_brief (Layer A)** on the model you extend: it reports the **recommended manifest depends** for the fields/methods you touch. Add only what you use. (→ `odoo-dev` for the override itself.)

## Standard layout

| Path | Holds | Notes |
|---|---|---|
| `__manifest__.py` | module metadata | required; defines load order |
| `__init__.py` | `from . import models, wizard, …` | top-level subpackages |
| `models/__init__.py` | `from . import <file>` per model file | one line per file |
| `models/x_y.py` | model classes | filename **underscore**, `_name` **dotted** |
| `security/ir.model.access.csv` | CRUD ACLs | load **before** views using its groups |
| `security/*.xml` | groups, record rules | |
| `views/*.xml` | views, actions, menus (all data) | load after security |
| `data/*.xml` | master data (`noupdate="1"` opt) | `data` key |
| `demo/*.xml` | demo data | `demo` key, not `data` |
| `static/src/` | JS/SCSS/OWL `.xml` | declared via `assets` dict |
| `wizard/ report/ controllers/ tests/ i18n/` | as named | each wired in its `__init__` |

## `__manifest__.py` — keys + the order that bites

```python
{
    'name': 'Library',                      # human title
    'version': '18.0.1.0.0',                # <odoo_series>.<major>.<minor>.<patch>
    'category': 'Services/Library',
    'summary': 'Manage books and loans',
    'depends': ['base'],                    # ← sets MRO layer; see above
    'data': [                               # ORDER MATTERS, top → bottom:
        'security/library_groups.xml',      #   1. groups
        'security/ir.model.access.csv',     #   2. ACLs (reference those groups)
        'views/library_book_views.xml',     #   3. views / actions / menus
        'data/library_sequence.xml',        #   4. data
    ],
    'demo': ['demo/library_demo.xml'],
    'assets': {                             # modern (15+); NOT legacy <template> XML
        'web.assets_backend': ['library/static/src/**/*.js'],
        # 'web.assets_frontend': [...],     # website / portal
    },
    'application': True,                    # shows as an installable app tile
    'installable': True,                    # default True
    'auto_install': False,                  # True → installs once its depends are present
    'license': 'LGPL-3',                    # default if omitted
    # 'external_dependencies': {'python': ['requests']},
}
```

Load-order rules that fail silently: groups → ACL csv → views → menus/actions that reference them → data. A `ref=` to a not-yet-loaded XML id raises at install; a view referencing a group defined *later* renders without that access logic.

## `__init__` wiring — the #1 "my model doesn't exist" cause

A model class that is never imported is invisible to the registry — **no error**, the model simply isn't there.

```python
# library/__init__.py
from . import models
# library/models/__init__.py
from . import library_book          # add one line per new model file
```

## Naming

| Thing | Convention | Example |
|---|---|---|
| Module dir / technical name | `snake_case`, lowercase | `library_management` |
| Model `_name` | dotted, singular | `library.book` |
| Model file | `_name` with `_` | `library_book.py` |
| DB table (auto) | dots → `_` | `library_book` |
| `_description` | required (warns if absent) | `'Library Book'` |
| View/action/menu xml id | `view_/action_/menu_<model>_<type>` | `view_library_book_form` |

## Gotchas that fail silently

- New `models/foo.py` not added to `models/__init__.py` → model absent, **no error**.
- `ir.model.access.csv` missing a row for a new model → non-admin gets AccessError / empty views; admin sees it fine (looks like it works).
- New data/view file created but not listed in `data` → never loads, no warning.
- Editing `data`/view XML then re-running `-i` on an already-installed DB → not reapplied; you need `-u <module>`.
- `auto_install: True` with heavy `depends` → module silently self-installs across DBs on upgrade.

## References

- `references/module-skeleton.md` — full copy-pasteable minimal module (model + ACL csv + menu/action/form+list) that installs clean on v17/18.
- `odoo-introspect` model_brief (Layer A) — recommended manifest depends before you write `depends`.
- `odoo-dev` — the override/extension once the module exists.
- `odoo-views` — authoring the view XML this skeleton stubs.
