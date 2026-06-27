# Odoo security authoring

Read alongside the `model_brief` `security{}` dossier. Every claim here is verifiable against the dumped `access_rights` / `record_rules`. Targets Odoo 17/18, through Odoo 19 (current LTS).

> **v18 → 19 API:** check access in code with **`check_access(op)`** (raises), **`has_access(op)`** (bool), or **`_filtered_access(op)`** (returns the allowed subset) — the old `check_access_rights()` + `check_access_rule()` pair is superseded (v18) / deprecated (v19). And from **v18.2** public model methods are RPC-callable by default; mark internal ones **`@api.private`** so they aren't a remote surface (a leading underscore is convention, not enforcement).

## ACL — `ir.model.access.csv`

One file per module, at the module root: `security/ir.model.access.csv`, declared in `__manifest__.py` `data`. One line per (model, group) you want to grant.

```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_estate_property_user,estate.property.user,model_estate_property,base.group_user,1,1,1,0
access_estate_property_mgr,estate.property.manager,model_estate_property,estate.group_manager,1,1,1,1
```

| Column | Meaning |
|--------|---------|
| `id` | XML id of the ACL line (unique) |
| `name` | human label, convention `model.group` |
| `model_id:id` | `model_<dotted_with_underscores>`; for `estate.property` → `model_estate_property`. Another module's model: `other_module.model_x_y` |
| `group_id:id` | external id of a `res.groups`. **Blank = applies to everyone**, including public/portal — rarely what you want |
| `perm_read / write / create / unlink` | `1` or `0`. Omitted/blank = `0` |

Semantics: **additive union, no deny.** A user's effective perm on the model is the OR across every line whose `group_id` they belong to. To *remove* access you delete/avoid granting it — you cannot write a "deny" line. A model with **no** line at all is denied to every non-superuser (Odoo logs a warning at access time).

Transient (`TransientModel` / wizards) still need ACL. `perm_unlink` on wizards is usually `0` (the vacuum cron cleans them).

## Record rules — `ir.rule`

Defined in XML (`security/security.xml`). Narrow *rows* within a model the ACL already permits.

```xml
<record id="property_rule_own" model="ir.rule">
    <field name="name">Property: salesperson sees own</field>
    <field name="model_id" ref="model_estate_property"/>
    <field name="domain_force">[('user_id','=',user.id)]</field>
    <field name="groups" eval="[(4, ref('estate.group_user'))]"/>
    <field name="perm_read" eval="1"/><field name="perm_write" eval="1"/>
    <field name="perm_create" eval="1"/><field name="perm_unlink" eval="1"/>
</record>
```

| Field | Meaning |
|-------|---------|
| `domain_force` | the row filter; a domain string evaluated with `user`, `company_id`, `company_ids`, `time` in scope |
| `groups` | m2m to `res.groups`. **Empty ⇒ global rule** (the `global` field auto-computes True) |
| `perm_read/write/create/unlink` | which operations the rule applies to (default all 1) |
| `active` | toggling off disables it — a common "why is this rule ignored" cause |

### Evaluation algorithm (what the brief encodes)

For an operation (say write), the rows a non-superuser may touch =

```
ALL global rules' domains  AND  ( OR of the domains of the user's matching group rules )
```

- **Global rules AND together** — every global rule must pass. Add one and you *tighten* access for everyone.
- **Group rules OR within the user's groups** — belonging to any group whose rule matches grants those rows; that union is then AND-ed with the globals.
- No group rule for the user on that op ⇒ only the globals apply (effectively unrestricted by group rules).
- `sudo()` skips the whole computation.

Multi-company isolation is just a **global** rule of the form `['|', ('company_id','=',False), ('company_id','in',company_ids)]` — each company-aware module ships one for its own model. That's why a record in another company raises `MissingError`/`AccessError` rather than being visible.

## Groups — `res.groups`

```xml
<record id="group_manager" model="res.groups">
    <field name="name">Estate / Manager</field>
    <field name="category_id" ref="base.module_category_real_estate"/>
    <field name="implied_ids" eval="[(4, ref('estate.group_user'))]"/>
</record>
```

- `category_id` — the app section in Settings → Users; an *exclusive* category renders as a selection (one level), else checkboxes.
- `implied_ids` — **inheritance**: a user in this group is automatically added to the implied groups (manager implies user). This is how you avoid duplicating ACL lines per level — grant the base group, imply it from higher ones.
- Assigning a group via `implied_ids` also grants all *its* implied groups transitively.

## Field-level security

Restrict a field to a group — enforced on read **and** write, silently (no error for the unauthorized user; the field is dropped from views and from `read`):

```python
salary = fields.Monetary(groups="hr.group_hr_manager")
```

or in a view: `<field name="salary" groups="hr.group_hr_manager"/>` (view-only hiding; the Python `groups=` is the real ACL-grade gate). The brief shows the model-level truth in `fields[].groups`.

## sudo / with_user / with_company

| Call | Effect | Use when |
|------|--------|----------|
| `recs.sudo()` | superuser — bypass ACL + all record rules | a narrow privileged step (e.g. write a sequence, read a config) **with a comment** |
| `recs.with_user(user)` | run as `user` — ACL + rules **still apply** | act on behalf of someone; test least-privilege |
| `recs.with_company(company)` | set the active company for the chain | writing/reading another allowed company |
| `recs.with_context(...)` | pass flags (`active_test=False`, defaults) | not a security primitive — context only |

`sudo()` returns a new recordset bound to superuser; it does **not** mutate `self`. It also leaves `env.company` unchanged.

## Testing as a non-admin (mandatory when security changes)

Admin bypasses most rules, so admin-green proves nothing. Drive the real user (see the `odoo-testing` skill for the full gate):

```python
user = self.env['res.users'].create({
    'name': 'Sales', 'login': 'sales',
    'groups_id': [(6, 0, [self.env.ref('estate.group_user').id])],
})
rec = self.env['estate.property'].create({...})
# their own row: allowed
rec.with_user(user).write({'name': 'x'})
# someone else's row: must raise
other = self.env['estate.property'].with_user(other_user).create({...})
with self.assertRaises(AccessError):
    other.with_user(user).write({'name': 'y'})
```

For multi-company, create records in two companies and assert no cross-company read/write leakage with `with_company`.

## Checklist

- [ ] New model ⇒ `ir.model.access.csv` line(s), all four perms set deliberately.
- [ ] "Admin works, user fails" ⇒ inspected `record_rules` (global vs group) before any `sudo()`.
- [ ] Every `sudo()` has a written reason and is the narrowest call.
- [ ] `company_id` on the model; no hardcoded company; reads use `env.company`/`env.companies`.
- [ ] Sensitive fields gated with Python `groups=`, not just view `groups`.
- [ ] Tested with a real non-admin user (and ≥2 companies if `company_id`).
