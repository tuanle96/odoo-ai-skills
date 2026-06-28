# High-risk playbooks

**What a static index cannot catch, but the running instance reveals.**

A field inventory tells you what exists.  A module manifest tells you what depends on what.  Neither tells you what actually runs for a specific user, on a specific company, through a specific upgrade path, in a specific call graph.  The six scenarios below are the canonical cases where "read from memory / static analysis" produces confident-but-wrong results, and where reading the live instance via `odoo-ai` produces a verifiable answer.

---

## 1 — Record rule that differs by company (multi-company leak)

**Symptom.** User A (assigned to Company 1 only) cannot see Company 2's `sale.order` records — correct.  User B (allowed in Company 1 and Company 2) can see both — also expected.  But User C, who is *assigned* to Company 2 yet has Company 1 toggled on in the company switcher, sees Company 1's records when they shouldn't, because the record rule was written as `company_id = user.company_id` (the default company) rather than `company_id in user.allowed_company_ids`.

**Why a static/metadata index misses it.** The rule exists in `ir.rule` and a `search_read` confirms it.  But whether the domain `[('company_id', '=', user.company_id)]` leaks data for a multi-company user depends on which company is *active* and how `allowed_company_ids` is set — values that exist only at runtime.  A static ACL audit shows the rule; it does not evaluate the domain for a specific user + company combination.

**Commands that catch it.**

```bash
# Effective domain for User C with only Company 2 active:
odoo-ai --db <DB> security sale.order --user <user_c_id> --company 2

# Same user with Company 1 also toggled on:
odoo-ai --db <DB> security sale.order --user <user_c_id> --company 2 --allowed-companies 1,2

# Compare effective_domain between the two runs.
# A correct rule scopes to the allowed set; a broken one lets Company 1 rows through.
```

The Layer G output carries `record_rules.<mode>.effective_domain` (the domain Odoo's own `ir.rule._compute_domain` evaluates under `with_company`) and a `company` block (`acting_company`, `simulated_allowed_company_ids`, `user_allowed_companies`).  A read `effective_domain` that does not include `company_id in [2]` when `simulated_allowed_company_ids=[1,2]` is the bug.

---

## 2 — `sudo()` masking a correct `AccessError`

**Symptom.** A developer adds `sudo()` to silence an `AccessError` in `account.move`.  The code works — admin and the affected user both confirm it.  Three months later an auditor notices that every employee can now read invoices they should not see.

**Why a static/metadata index misses it.** An ACL `search_read` shows the `ir.model.access` rows correctly restricting `account.move` to the Accounting group.  The record rule restricting cross-company access also exists.  Neither check walks the Python call chain to find the `sudo()` that bypasses both.

**Commands that catch it.**

```bash
# 1. Linter flags uncommented sudo() immediately (LOCAL, no DB):
odoo-ai validate path/to/my_module/models/account_move.py

# 2. Confirm what the affected user SHOULD be able to do:
odoo-ai --db <DB> security account.move --user <affected_user_id>
# record_rules.read.effective_domain is non-empty (the restriction exists) and
# access_rights.odoo_check carries Odoo's own verdict — proving the user is NOT
# meant to read these rows. (security simulates the acting user; it does NOT
# detect sudo() itself — step 1's validator / grepping source bodies does that.)

# 3. Trace the actual flow to see where sudo() fires:
odoo-ai --db <DB> trace account.move <invoice_id> action_post
# Look for frames where the env user switches to __odoo__ or uid=1.
```

`validate` flags `sudo()` calls that lack a one-line comment explaining the privilege reason.  `security` shows the rule that was bypassed.  `trace` (Layer D) shows which frame in the call graph the `sudo()` lives in.

---

## 3 — Dev/prod module drift invalidating test confidence

**Symptom.** All tests pass on the dev database (which has `crm`, `sale_crm`, and a bespoke `custom_crm_ext` addon installed).  The same change is deployed to production, which does not have `custom_crm_ext`.  A method override in `sale.order` that depended on a hook added by `custom_crm_ext` silently skips — the MRO resolves differently and the override is effectively dead code.

**Why a static/metadata index misses it.** The `__manifest__.py` `depends` list is correct for the dev environment.  No static check compares the installed module graph of one instance against another.  The MRO difference is only visible when you read the registry of the *specific* instance.

**Commands that catch it.**

```bash
# Capture fingerprints on both sides (each needs a shell):
odoo-ai --db dev_db  env-fingerprint > dev.json
odoo-ai --db prod_db env-fingerprint > prod.json

# Diff them (LOCAL — compares the two JSON files, no DB):
odoo-ai env-diff dev.json prod.json
# Output: modules present in dev but absent in prod, version mismatches,
# Studio fields, ir.config_parameter key-name drift.

# For each missing module, inspect the MRO impact:
odoo-ai --db dev_db all sale.order   # note the MRO chain under dev
odoo-ai --db prod_db all sale.order  # compare — missing a layer?
```

`env-diff` is **local** — it only reads the two JSON files, so it can run in CI or on a laptop.  The fingerprints are captured with `env-fingerprint` (needs a shell on each instance).

---

## 4 — Fresh-install passes but `-u` upgrade drops data

**Symptom.** A developer renames a field: Python `char commitment_date` → `char promised_date`.  Tests on a fresh CI database pass — `promised_date` was never `commitment_date` there.  On the production upgrade, `-u` runs the migration: Odoo drops the `commitment_date` column and creates an empty `promised_date`.  Every delivery commitment date in every open order is gone.

**Why a static/metadata index misses it.** A Layer A field inventory on the new code shows `promised_date` with no anomaly.  There is no column named `commitment_date` to flag in the new source.  The data loss only appears when the new schema is applied to a database that *already has* `commitment_date` rows.

**Commands that catch it.**

```bash
# 1. Capture a brief of the CURRENT production model before the change:
odoo-ai --db prod_db brief sale.order > old_brief.json

# 2. On the dev instance (after the rename), compare:
odoo-ai --db dev_db upgrade-check sale.order --against old_brief.json
# Output: `commitment_date` classified as RENAME (if a same-type field appeared)
# or DROP (data loss).  Also flags new-required-without-default and
# noupdate-protected records that would need a migration.
# Scaffolds a pre-migrate.py stub.

# 3. Or diff two briefs locally (no DB needed):
odoo-ai upgrade-diff old_brief.json new_brief.json
```

`upgrade-check` distinguishes a **rename** (keep data, write a column-rename migration) from a **drop** (permanent loss) and outputs a `data_loss_risk` field with the affected rows count.  Fresh-install success is never reported as upgrade safety — that distinction is explicit in the output.

---

## 5 — View xpath / field visible to admin but broken for a normal user

**Symptom.** A form view override adds a field `margin` gated with `groups="sale.group_show_margin"`.  QA as admin: visible and correct.  A sales representative logs in: the field is invisible (expected), but a second xpath they added — referencing `margin` in an `attrs` expression — causes the view to fail with a `ValueError` for any user not in `sale.group_show_margin` because the v17/18 `attrs` removal means the expression is evaluated against fields the user cannot see.

**Why a static/metadata index misses it.** The XML is syntactically valid.  A Layer B `entrypoints` read as admin shows the view renders.  The group `sale.group_show_margin` exists.  The failure only appears when the view is rendered in the context of a user who lacks the group.

**Commands that catch it.**

```bash
# 1. Linter catches deprecated attrs= usage immediately (LOCAL, no DB):
odoo-ai validate addons/my_module/views/sale_order_views.xml

# 2. Layer B shows the view inheritance chain and which fields carry groups=:
odoo-ai --db <DB> all sale.order   # includes entrypoints (Layer B)
# Look for fields with a `groups` modifier in view_modifiers.

# 3. Effective field access for a normal sales rep:
odoo-ai --db <DB> security sale.order --user <sales_rep_id>
# group_restricted_fields shows which fields are hidden for this user.
# Cross-check against the xpaths in your view diff.

# 4. Confirm the rendered arch as that user (Layer B, targeting a specific view):
odoo-ai --db <DB> brief sale.order --methods get_view
# Or use VIEW_XMLID to render the specific override and inspect the arch.
```

`validate` flags deprecated `attrs=` / `states=` immediately.  `security` shows `group_restricted_fields` for the specific user.  Comparing the field list to the view's xpath targets reveals the mismatch before any user sees the error.

---

## 6 — Runtime trace reveals an unexpected write side-effect across apps

**Symptom.** A developer overrides `sale.order.action_confirm` to add a custom check before confirmation.  Review looks fine — the override calls `super()`, returns the result.  After deployment, an integration test for the purchase app starts failing: a `purchase.order.line` is being written with stale data because the custom check runs *after* the procurement hook has already created the purchase line, but *before* a related `stock.move` write that the procurement engine expected to see first.

**Why a static/metadata index misses it.** A Layer A MRO read shows the `action_confirm` call chain for the `sale` and `sale_stock` modules.  It does not show the procurement engine (`procurement.group.run`) that fires inside `sale_stock._action_confirm`, nor the `stock.move` and `purchase.order.line` writes that follow.  Static analysis cannot reconstruct a call graph that spans three apps and fires through rules + automations.

**Commands that catch it.**

```bash
# Full runtime trace of action_confirm on a real order (rolls back by default):
odoo-ai --db <DB> trace sale.order <order_id> action_confirm

# The trace summary includes:
#   writes_by_model  — every model written + the field names touched (addon-scoped)
#   top_self_sql     — SQL hotspots by self-cost (where time is actually spent)
#   call_counts      — most-invoked (model, method) pairs → N+1 smell
#   exception_origin — if it raised, the innermost addon frame
#
# In this case writes_by_model will show:
#   {"model": "stock.move", "creates": 1, "fields": ["product_id", "product_uom_qty", ...]}
#   {"model": "purchase.order.line", "creates": 1, "fields": [...]}
# making the cross-app side-effect visible before the test suite catches it.

# If you need the VALUES (not just field names) at the moment of the write:
odoo-ai --db <DB> state sale.order <order_id> action_confirm \
    --break purchase.order.line.create \
    --fields product_qty,price_unit
```

Layer D (`trace`) is the definitive tool for cross-app side-effect discovery.  It runs the real code path against a real record, captures every frame in every `odoo.addons.*` module, and rolls back — safe to run on a dev or staging database.

---

## The category these define

A static index built from source or metadata answers "what exists?"  It does not answer "what runs, for whom, in which env, through which upgrade path."

These six scenarios are the canonical failures in that gap:

| What failed | Layer that catches it |
|---|---|
| Multi-company rule leak | G — `security` with `--allowed-companies` |
| sudo() bypass of correct ACL | I `validate` (flag) + G `security` (confirm) |
| Dev/prod module drift | I `env-fingerprint` + `env-diff` |
| Rename-vs-drop data loss on `-u` | I `upgrade-check` / `upgrade-diff` |
| Group-gated view broken for non-admin | I `validate` + G `security` + B `all` |
| Cross-app write side-effect | D `trace` (+ F `state` for values) |

Static indexes suggest.  odoo-ai-skills verifies against the running instance.
