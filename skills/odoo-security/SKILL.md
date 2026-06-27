---
name: odoo-security
description: >-
  Authoring or debugging Odoo access control — ACL (ir.model.access.csv),
  record rules (ir.rule), groups (res.groups + implied_ids), field-level
  groups, sudo(), with_user / with_company, and multi-company row isolation.
  Use whenever a change "works for admin but fails for a normal user", an
  AccessError or "you are not allowed to" appears, a new model needs
  permissions, you're about to reach for sudo(), or you'd otherwise GUESS which
  groups grant what or why a user sees too many / too few rows. Effective
  access is composed at runtime from every installed addon — read the real ACL
  + rule dossier first, never guess. Targets Odoo 17/18/19.
---

# Odoo security

Effective access on a model is **composed at runtime** from every installed addon: ACL lines union across the user's groups, and record rules from many modules AND/OR together. Neither the source tree nor your memory tells you the effective permission for *this* instance — only the running registry does. Guessing is why "I added a group and it still 403s" and "admin works, user doesn't" keep happening.

**Read the real dossier first.** Delegate discovery to the **`odoo-introspect`** skill — `model_brief` (Layer A) dumps the model's ACL + record rules straight from the DB:

```bash
odoo-ai --db <DB> brief <model>     # security{} = access_rights (ACL) + record_rules
odoo-ai --db <DB> all  <model>      # + entrypoints / metadata / trace
```

Inspect `security.access_rights` (the ACL union) and `security.record_rules` (global vs group, `domain_force`, `perm_*`) before changing anything.

**Version floor: Odoo 17/18, through Odoo 19 (current LTS).** ACL/record-rule *semantics* are stable back to v14, but group external IDs and the introspection tooling assume 17/18+. Two recent security-relevant renames AI gets wrong: the access-check API was unified (**`check_access`** raises / **`has_access`** returns bool / **`_filtered_access`** filters a recordset) in v18 and the old `check_access_rights`/`check_access_rule` pair is superseded → v19; and from **v18.2 public model methods are RPC-callable by default** — see the `@api.private` note below. Details in `skills/odoo-introspect/references/version-matrix.md`.

## How access is actually evaluated — two gates, in order

1. **ACL (`ir.model.access`) — "can this user touch the model at all?"** Per (model, group, operation). **Additive union, no deny**: a user gets `perm_write` if *any* of their groups grants it. A model with **zero** matching ACL lines is fully denied to non-superusers — silent on a new model, easy to miss.
2. **Record rules (`ir.rule`) — "which rows?"** Only ever *narrow* what ACL allowed, via `domain_force`, per operation:
   - **Global rules** (no `groups`) are **AND**-ed — *every* one must pass.
   - **Group rules** (have `groups`) are **OR**-ed across the user's groups, then **AND**-ed with the globals.
   - Net: `globals AND (union of the user's group rules)`. `sudo()` (superuser) bypasses **both** gates.

| Symptom | Almost always | First move |
|---------|---------------|-----------|
| Admin OK, normal user `AccessError` | a **record rule** narrows rows | dump `record_rules` |
| "Not allowed to create/write" | missing/0-perm ACL line for their group | check `access_rights` union |
| New custom model 403s for everyone | no ACL line at all | add `ir.model.access.csv` |
| User sees rows they shouldn't | missing global rule / too-wide group rule | compare global vs group `domain_force` |
| Field missing or not writable for some | field-level `groups=` | brief `fields[].groups` |

## Pick the right tool

| Need | Use | Not |
|------|-----|-----|
| Gate a model by role | `ir.model.access.csv` line per group | `sudo()` |
| Restrict to "own" / company rows | `ir.rule` + `domain_force` | filtering in Python |
| Hide a sensitive field | `groups=` on the field (and/or view) | deleting it |
| Role hierarchy (manager ⊇ user) | group `implied_ids` | duplicate ACL lines |
| Run one privileged sub-step | `sudo()` **+ a written reason** | blanket `sudo()` on the method |
| Act as a specific user | `with_user(user)` (rules still apply) | `sudo()` then set `create_uid` |
| Write into another company | `with_company(c)` + check `env.companies` | hardcoded `company_id` |

## RPC exposure & access checks (v18.2 / v19)

Two recent changes are easy to ship wrong because older code never had to think about them:

- **`@api.private` — public methods are RPC-callable by default (v18.2).** Any public method on a model can be invoked over RPC (`call_kw`) by a user who can reach the model. An internal helper you left public (e.g. `def _do_charge` is fine, but `def do_charge` without the underscore *and* without the decorator) is now a remote-callable surface. Mark methods that must **not** be exposed with `@api.private`; keep genuine endpoints public deliberately. A leading underscore is convention, not enforcement — the decorator is.
- **Use `check_access` / `has_access`, not the old pair.** `check_access(operation)` raises on denial; `has_access(operation)` returns a bool; `_filtered_access(operation)` returns the subset of a recordset the user may touch. The v≤17 `check_access_rights()` + `check_access_rule()` two-call pattern is superseded (v18) and deprecated (v19) — AI emits it constantly from training data. Confirm the version before writing either.

## sudo() discipline

`sudo()` bypasses **all** ACL + record rules. Use it only with an explicit one-line reason in a comment, only for the narrowest sub-call, never to silence an `AccessError` you don't understand — that error is usually a *correct* record rule. Prefer `with_user(user)` when you need a real identity (rules still apply). `sudo()` does **not** change company scope — use `with_company`.

## Multi-company (isolation is a record rule, not code)

Company isolation is enforced by **global record rules** with `company_id in company_ids` domains — not by your code. So: put `company_id` on the model, never hardcode it, read `self.env.company` (active) / `self.env.companies` (allowed set), and `with_company(c)` to write elsewhere. A stored compute whose value varies by company needs `@api.depends_context('company')`. Confirm the rule exists in `record_rules` rather than assuming.

## Gotchas that fail silently

- **New model, no ACL** — invisible to all non-admins; no install error, just 403 at runtime. Always ship `ir.model.access.csv`.
- **ACL is OR, rules are (mostly) AND** — adding a group can only *grant* (ACL); it can't reopen a row a global rule already blocks. People invert this constantly.
- **`groups=` on a field is silent** — a user without the group simply doesn't get the field (reads return nothing, writes are dropped), no error. Check `fields[].groups` in the brief.
- **`perm_create` / `perm_unlink` left 0** — each ACL perm defaults to 0; a line with only read+write silently blocks create. Set all four columns deliberately.
- **`sudo()` keeps the current company** — it widens *records*, not company scope; cross-company still needs `with_company`.
- **Rule `domain_force` context** — evaluated with `user` bound to the **current** user and `company_ids`/`company_id` to their companies. Reference `user.x`, never `self` or `self.env.user`.
- **Public method = RPC endpoint (v18.2+)** — a non-underscore method with no `@api.private` is reachable via `call_kw` by anyone who can call the model. Silent until someone scripts it; mark internal logic `@api.private`.

## References & related skills

**This skill's reference**
- `references/security-authoring.md` — ACL CSV columns + the 1/0 perms, `ir.rule` fields, global-vs-group examples, `res.groups` / `implied_ids`, field-level groups, the exact eval algorithm, the multi-company recipe, and testing as a non-admin user.

**Other skills in the loop**
- `odoo-introspect` — Tier 0 engine; `odoo-ai all <model>` → `security{}` dossier (read first). RPC fallback for SaaS.
- `odoo-debug` — decoding `AccessError` vs record rule from a traceback.
- `odoo-dev` — server-side overrides; `odoo-testing` — the non-admin / multi-company test gate.
