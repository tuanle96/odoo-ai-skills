# Odoo patterns & anti-patterns

Read alongside the `model_brief` (Layer A from the `odoo-introspect` skill) when deciding *how* to structure a customization. Targets Odoo 17/18, through Odoo 19 (current LTS).

## Inheritance: pick the right mode

| Goal | Mode | Shape |
|------|------|-------|
| Add fields/methods to an existing model, same table | **Extension** | `_inherit = 'sale.order'` (no `_name`) |
| New model that copies an existing one's structure | **Prototype/copy** | `_name = 'x'`, `_inherit = 'sale.order'` |
| New model that *embeds* another and exposes its fields | **Delegation** | `_inherits = {'res.partner': 'partner_id'}` + a required `partner_id` Many2one |
| Reusable behavior across many models | **Mixin** | `models.AbstractModel`, then `_inherit = ['my.mixin', ...]` on consumers |

Rule: reach for **extension** by default. Use `_inherits` (delegation) only when you genuinely want one record to *be* another (e.g. a "user" that is also a "partner"). Delegation is not "has-a relation" — for that, just add a Many2one.

## Recordset hygiene (where performance dies)

- **Never query inside a loop.** `for r in records: env['x'].search(...)` is N queries. Search once with an `in` domain, then `mapped` / `filtered` / group in Python.
- Use set operations on recordsets: `recs.filtered(lambda r: r.state == 'done')`, `recs.mapped('partner_id')`, `a | b`, `a - b`.
- Aggregate with `_read_group`, don't `search` + count per record.
- `self.ensure_one()` at the top of methods that assume a single record — fail loud, not silently on `self[0]`.
- Write once with a dict, not field-by-field in a loop: `records.write({...})` batches.
- `create` a list, not a loop: `env['x'].create([vals1, vals2, ...])` is one INSERT path.

## Computed fields: the two failure modes

1. **Incomplete `@api.depends`.** If `store=True`, the depends list must name *every* path that can change the value, including dotted (`'line_ids.price_subtotal'`). Miss one and the stored value silently goes stale. The brief's `depends` column lets you compare against reality.
2. **Storing what you shouldn't.** `store=True` only if you need to **search, group, or report** on it. Otherwise leave it non-stored (computed on read) and add a `_search_<field>` if it must be filterable. Stored computes cost write amplification.

`related=` is just a thin stored/non-stored compute — same rules apply.

## write() / create(): the ordering trap

- In `write`, the *old* values are still on `self` **before** `super().write(vals)`. Capture what you need first, call super, then act on new state.
- In `create` (with `@api.model_create_multi`), `vals_list` is a **list of dicts**. Loop to adjust each, call `super().create(vals_list)` **once**, then post-process the returned recordset.
- Don't trigger recomputes by hand. Setting a field that other computes `@api.depends` on will recompute them automatically.

## super() correctness

- Always call `super()` for framework methods unless you deliberately replace behavior (rare, and you'd better know the whole chain from the brief).
- Your override's position in the MRO matters: the brief shows it. If your addon depends on `sale_stock`, your class sits *above* `sale` — so your `super()` reaches `sale_stock`'s version, then `sale`'s. Extending a method that a *higher* addon already wraps is how "my override never runs" happens.
- Reminder from SKILL.md: MRO lists where overrides resolve, not what runs. A layer that doesn't call `super()` ends the chain — read the source, don't assume.

## Security: how access is actually evaluated

`sudo()` bypasses all of this — use it only with an explicit, written reason, never to make an error go away.

- **ACL (`ir.model.access`) is additive.** A user gets a permission if *any* of their groups grants it. There's no "deny" — only the union of grants.
- **Record rules (`ir.rule`) are evaluated *after* ACL**, and only narrow what ACL allowed:
  - **Global rules** (no group) are **AND**-ed together — every global rule must pass.
  - **Group rules** are **OR**-ed among themselves (belonging to a group with a matching rule grants access), then the result is **AND**-ed with the global rules.
- Net effect: ACL says *can you touch this model at all*; record rules say *which rows*. A change that "works for admin" but fails for a user is almost always a record rule — check it before reaching for `sudo()`. Prove it with a `with_user` test (see the `odoo-testing` skill).

## Gotchas that fail silently

- **Field name == method name.** Declaring a field with the same name as a method (or vice versa) makes the later definition **silently overwrite** the earlier — no error, no warning. Easy to hit when you add a field named like an existing `_get_*`/helper. The brief's field list + MRO let you spot the collision.
- **onchange only fires in form views.** `@api.onchange` runs in the web client form (and in `Form()` tests) — **not** on a direct Python attribute set, and **not** on RPC/import `create`/`write`. Never rely on onchange for data integrity; use `@api.constrains` (validation) or a computed field (derived values). Onchange is UX sugar only.

## Context & company

- Multi-company: filter by `company_id` and respect `self.env.company` / `self.env.companies`. Don't hardcode. Scope writes with `with_company(...)`.
- Use `with_context(...)` to pass flags down (e.g. `default_*`, `active_test=False`), and `@api.depends_context('company')` on computes whose value varies by company.
