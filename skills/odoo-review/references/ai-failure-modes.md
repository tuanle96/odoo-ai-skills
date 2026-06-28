# Odoo AI failure modes

The confident-but-wrong patterns an LLM ships in Odoo code — syntactically clean, "ran for me as admin", wrong. Each is paired with the `odoo-ai` command that catches it (a mirror of `docs/high-risk-playbooks.md`, scoped to the *authoring* mistakes review must catch). One shared root cause: model training centers on v15–v17 and on the public API, so AI emits memory-era names and bypasses the contracts it can't see. Targets Odoo 17/18, through Odoo 19 (current LTS).

> **Mechanical ones first:** `odoo-ai validate <path…>` (local, no DB) flags #2–#6 and #8 statically. This file explains *why* each is wrong and how to confirm the ones a linter can only suspect.

## 1 — Hallucinated field / method name

```python
inv = self.env['account.invoice'].search([('customer_id', '=', p.id)])
```

`account.invoice` was merged into **`account.move`** back in v13; `customer_id` is a plausible invention (`account.move` uses `partner_id`). The code imports and parses fine — it fails at runtime, for everyone. AI fills gaps with names that *sound* right.

**Catch it.** `odoo-ai all account.move` (or `brief`) lists the real model + fields + methods. A name not in the dossier is a hallucination until the brief proves it. Never confirm a field from memory.

## 2 — Memory-era deprecated syntax

```xml
<field name="state" attrs="{'invisible': [('state','=','draft')]}"/>
<tree><field name="name"/></tree>
```
```python
def name_get(self): ...
self.check_access_rights('write')
```

`attrs`/`states` were **removed in v17**, `<tree>` → `<list>` (v18), `name_get` → `_compute_display_name` (v17), `check_access_rights` + `check_access_rule` → `check_access` / `has_access` (v18→19). Most training predates all four, so AI emits them by default. `attrs`/`<tree>` hard-fail the view parse on 17/18; the API pair raises `AttributeError`.

**Catch it.** `odoo-ai validate` flags `attrs`/`states`/`<tree>`/`name_get`/`type='json'` directly. For the rest, confirm against the target version in `skills/odoo-introspect/references/version-matrix.md`.

## 3 — `sudo()` to silence an AccessError

```python
# user hit AccessError, so:
record.sudo().write({'state': 'done'})
```

The reflex fix. `sudo()` bypasses **both** ACL and every record rule — admin and the affected user both confirm it "works", and three months later every employee can read records they shouldn't. The `AccessError` was almost always a *correct* rule.

**Catch it.** `odoo-ai validate` flags any `sudo()` with no explanatory comment. Then `odoo-ai security <model> --user <affected_id>` shows the `effective_domain` the rule enforces — if that restriction is intended, the `sudo()` is the bug. Replace with `with_user` / `with_company`, or a narrow sub-call with a one-line reason.

## 4 — Batch-unsafe `create()` override

```python
@api.model
def create(self, vals):
    vals['code'] = self._next_code()
    return super().create(vals)
```

Singular `vals` + `@api.model`. Modern Odoo (17/18/19) calls `create` with a **list** of vals dicts under `@api.model_create_multi`; this override silently mangles every record after the first in a batch (web import, `create([…])`, onchange-driven multi-create).

**Catch it.** `odoo-ai validate` flags `create()` in a model class without `@api.model_create_multi` (`create_not_batch`). Fix: `@api.model_create_multi` + `for vals in vals_list:`. Better still, move the value into a `_prepare_*` hook (see #6).

## 5 — N+1 query in a loop / compute

```python
def _compute_total(self):
    for rec in self:
        rec.total = sum(self.env['sale.order.line'].search(
            [('order_id', '=', rec.id)]).mapped('price_subtotal'))
```

One `search` per record → 1+N queries. Admin sees it "work" on three demo records; it melts a 5 000-row list view. AI writes the readable per-record shape.

**Catch it.** `odoo-ai validate` flags `search`/`browse` under a loop (`query_in_loop`). Confirm the real cost with `odoo-ai trace <model> <id> _compute_total` → `total_sql`. Fix: search once with `('order_id','in',self.ids)`, bucket by `order_id` (→ `odoo-perf`).

## 6 — Overriding the public shell instead of the hook

```python
def action_confirm(self):
    self.partner_id.credit_check()      # added before super
    return super().action_confirm()
```

AI reaches for the method it can name. Overriding `action_confirm` / `create` / `write` to inject one step re-runs on every call, fires in the wrong order relative to other apps' hooks, and is one stray early-`return` from cutting `super()`. The framework almost always exposes a `_prepare_*` / `_get_*` / `_action_*` seam for exactly this.

**Catch it.** `odoo-ai all <model>` → MRO shows which layer owns the method and whether a hook exists. `odoo-ai trace <model> <id> action_confirm` → `writes_by_model` reveals the cross-app side-effects the override now sits among (→ `odoo-dev`, high-risk-playbooks §6).

## 7 — Bare rename = silent data loss on `-u`

```python
# was: commitment_date = fields.Date()
promised_date = fields.Date()           # "renamed"
```

No `migrations/`. On `-u` Odoo **drops** `commitment_date` and creates an empty `promised_date` — every value gone. A fresh-install test passes (the old column never existed there), so CI is green.

**Catch it.** `odoo-ai upgrade-check <model> --against old_brief.json` classifies it RENAME vs DROP and scaffolds a `pre-migrate.py` (capture `old_brief.json` from production *before* the change: `odoo-ai brief <model> > old_brief.json`).

## 8 — Hallucinated Python dependency (slopsquatting)

```python
'external_dependencies': {'python': ['python-barcode-pro']},
```

AI invents a plausible package name — or a typo of a real one — that may not exist on PyPI, or worse, that a squatter has registered. Install silently no-ops in dev where it's already vendored, then breaks (or runs hostile code) elsewhere.

**Catch it.** Verify every `external_dependencies['python']` / new `import` resolves to a real, maintained PyPI project and pin the version. No package is confirmed by it "sounding standard".

## 9 — Multi-company rule scoped to the default company

```xml
<field name="domain_force">[('company_id','=',user.company_id.id)]</field>
```

Scopes to the *active* company, not the *allowed* set — a user with two companies toggled on leaks the other's rows. The correct global rule is `['|',('company_id','=',False),('company_id','in',company_ids)]`.

**Catch it.** `odoo-ai security <model> --user <id> --allowed-companies 1,2` evaluates the real `effective_domain`; if it doesn't include `company_id in [1,2]` the rule leaks (→ high-risk-playbooks §1).

## Quick map

| AI ships | Catch with |
|---|---|
| Hallucinated field / model / method | `all` / `brief` (Layer A) |
| Deprecated v≤16 syntax | `validate` + version-matrix |
| `sudo()` silencing AccessError | `validate` flag + `security --user` |
| `create(self, vals)` singular | `validate` (`create_not_batch`) |
| `search` / `browse` in a loop | `validate` + `trace` `total_sql` |
| Override of `action_*` / `create` shell | `all` MRO + `trace` `writes_by_model` |
| Bare field rename | `upgrade-check` / `upgrade-diff` |
| Hallucinated PyPI package | manual PyPI check + pin |
| Default-company record rule | `security --allowed-companies` |
