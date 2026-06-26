# Odoo override surface

**Targets Odoo 18** (v17 shares these names; for v16 and older, confirm against `skills/odoo-introspect/references/version-matrix.md` — several of the renames below landed in v17). Read before overriding a method you haven't overridden before. The MRO chain from the `model_brief` (Layer A) tells you *where* a method lives; this tells you *whether you should touch it* and *how its signature behaves in v18*.

## Table of contents
1. CRUD
2. Search & read
3. Reactive (compute / onchange / constrains)
4. Display & view hooks (v17/v18 renames live here)
5. Things to override rarely / never
6. Decorators quick reference

---

## 1. CRUD

| Method | Signature (v18) | When to override |
|--------|-----------------|------------------|
| `create` | `@api.model_create_multi` → `def create(self, vals_list)` — **vals is a LIST** | Side effects on record creation; set defaults that `default_get` can't. Always loop `vals_list`, call `super().create(vals_list)` once. |
| `write` | `def write(self, vals)` | Side effects on update. Read old values *before* `super()`, react *after*. Don't compute derived values here — use a computed field. |
| `unlink` | `def unlink(self)` | Guard deletion, cascade cleanup not handled by `ondelete`. |
| `copy` | `def copy(self, default=None)` | Adjust duplicated data. Prefer overriding `copy_data` if you only need to change field values. |
| `copy_data` | `def copy_data(self, default=None)` → returns list of dicts (v17+) | Cleaner than `copy` for "don't duplicate field X" / "rename on copy". |
| `default_get` | `@api.model` → `def default_get(self, fields_list)` | Dynamic defaults that depend on context. |

## 2. Search & read

| Method | Signature | When to override |
|--------|-----------|------------------|
| `_search` | `def _search(self, domain, offset=0, limit=None, order=None)` (v17+ takes `domain`, not `args`) | Inject hard security/visibility filters. Low-level — prefer record rules first. |
| `search_read` | `def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None)` | Rarely; usually shape data in the client or a computed field instead. |
| `_read_group` | `def _read_group(self, domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None)` (v17+ shape) | Custom aggregation / virtual group rows. |
| `name_search` | `def name_search(self, name='', args=None, operator='ilike', limit=100)` | Make a model findable by more than its display name (e.g. by code/ref). |
| `_search_<field>` | `def _search_xxx(self, operator, value)` → return a domain | Make a **non-stored computed field** searchable. |

## 3. Reactive

| Hook | Form | When |
|------|------|------|
| Computed field | `@api.depends('a.b', 'c')` `def _compute_x(self):` set `self.x = ...` for each record | Derived value. Add `store=True` only if you need to search/group/report on it (then `depends` MUST be exhaustive). |
| Inverse | `def _inverse_x(self):` | Make a computed field writable. |
| Onchange | `@api.onchange('field')` `def _onchange_x(self):` | UI-only assistance before save. Never your sole validation (API writes bypass it). |
| Constraint (Python) | `@api.constrains('a', 'b')` `def _check_x(self):` raise `ValidationError` | Enforce a rule that needs Python. Runs on create/write touching those fields. |
| Constraint (SQL) | `_sql_constraints = [('name', 'unique(col)', 'msg')]` | Cheap DB-level guarantees (unique, check). Prefer over Python when expressible. |

## 4. Display & view hooks — **the renames agents get wrong**

| v18 | Old name (don't use) | Notes |
|-----|----------------------|-------|
| `_compute_display_name` (`@api.depends(...)`) | `name_get` | v17+ — set `self.display_name`. `name_get` is gone. |
| `get_view(view_id=None, view_type='form', **options)` | `fields_view_get` | v18 — postprocess arch/architecture here. |
| `get_views(views, options=None)` | — | Batch multi-view loader; rarely overridden. |
| `fields_get(allfields=None, attributes=None)` | (same) | Adjust field metadata exposed to the client. |

## 5. Override rarely / never

- `browse`, `exists`, `ensure_one`, `mapped`, `filtered`, `sorted` — recordset plumbing. Don't.
- `_compute_display_name` is fine; `display_name` field itself — don't redefine.
- `init()` — only for raw DDL/migration on `_auto = False` or manual SQL views.
- Don't override `read()` directly; shape data via computed fields or `search_read`.

## 6. Decorators quick reference

- `@api.model` — method doesn't depend on `self`'s records (class-level), e.g. `default_get`.
- `@api.model_create_multi` — required on `create`; `vals` is a list of dicts.
- `@api.depends(*fields)` — recompute trigger for a computed field. Must be exhaustive for `store=True`.
- `@api.depends_context(*keys)` — recompute when a context key changes (e.g. `'company'`, `'lang'`).
- `@api.onchange(*fields)` — UI assist, form view only.
- `@api.constrains(*fields)` — validation on create/write.
- `@api.returns(model, downgrade, upgrade)` — rare; for methods returning recordsets across RPC.
