# Odoo review checklist

The `odoo-review` checklist, dimension by dimension, with the *why-in-Odoo-terms* and the exact confirming command behind each line. Read alongside the `model_brief` dossier (`odoo-ai all <model>`) — every correctness finding is confirmed against the running instance or flagged as an assumption, never asserted from memory. Severity-ordered: a security miss fails silently, a style miss is cosmetic. Targets Odoo 17/18, through Odoo 19 (current LTS).

> **Mechanical first.** `odoo-ai validate <path…>` (local, no DB) statically clears the blunt defects — `attrs`/`states`, `<tree>`, `name_get`, `type='json'`, batch-unsafe `create()`, `search`/`browse` in a loop, f-string `cr.execute`, uncommented `sudo()`, `self._cr`/`_uid`/`_context`, fragile xpath, leftover `print`/`breakpoint`. Run it, then spend judgement on what a linter can't see (below): MRO layer, security intent, data-loss, the right hook. Before sharing any introspection JSON externally, `odoo-ai redact <file>`.

## 1 — Security (fails open, not loud)

The defining property: a security defect doesn't raise for the author — admin bypasses ACL **and** every record rule, so admin-green proves nothing. Confirm as the real user.

- **`sudo()` is the narrowest sub-call, with a written reason.** A `sudo()` bolted on to silence an `AccessError` is almost always hiding a *correct* record rule. Read the rule it bypassed: `odoo-ai security <model> --user <id>` shows the `effective_domain` the user actually has. If that domain is the intended restriction, the `sudo()` is the bug, not the rule. Prefer `with_user` / `with_company`.
- **New model ⇒ `ir.model.access.csv`.** Zero ACL lines = silent 403 for every non-superuser while admin "works". Confirm the grant exists and all four `perm_*` are set deliberately: `odoo-ai security <model>` → `access_rights`.
- **Row isolation is a record rule, not Python `filtered()`.** "Own records" / multi-company scoping belongs in `ir.rule.domain_force`; Python filtering leaks via RPC and direct ORM. Confirm: `odoo-ai security <model>` → `record_rules` (global vs group).
- **Sensitive field gated with Python `groups=`** (ACL-grade, enforced on read **and** write), not just `<field groups=…/>` in the view (cosmetic hiding). The brief's `fields[].groups` is the real truth.
- **`check_access` / `has_access`, not the old pair** (v18→19). `check_access_rights(op)` + `check_access_rule(op)` are superseded by **`check_access(op)`** (raises) / **`has_access(op)`** (bool) / **`_filtered_access(op)`** (allowed subset). Memory-era code still emits the old two-call form.
- **Public model method that should be private** (v18.2+). Public methods are RPC-callable by default; an internal helper left public and unmarked is a new remote surface — mark `@api.private`. An exposure check, not style.
- **Public route auth.** `@http.route(auth=…)` matches the exposure; no `csrf=False` on a session POST; no controller-wide `sudo()` (→ `odoo-web`).

## 2 — Data loss & upgrade safety

A fresh-install test proves *nothing* about `-u` on a populated DB — the two run different code paths. This tier is invisible until the production upgrade.

- **Field/model rename needs a pre-migration.** A bare Python rename (`old` → `new`) makes Odoo **drop** the old column and create an empty `new` on `-u`. Confirm rename-vs-drop and scaffold the migration: `odoo-ai upgrade-check <model> --against old_brief.json` (capture `old_brief.json` from production *before* the change with `odoo-ai brief <model>`).
- **Changed stored-compute logic must schedule a recompute** — existing rows keep stale values; the new formula only fires on the next write to a dependency. Needs a `post-` migration that forces the recompute.
- **New required field on a populated model is backfilled in `pre-`** — `post-` is too late, the `NOT NULL` already failed during the schema update.
- **Edited shipped data respects `noupdate`.** UI-editable config is `noupdate="1"` and won't be re-imported on `-u`; a protected record needs a migration, not a `-u`. `upgrade-check` flags `noupdate`-protected records the diff would otherwise expect to change.

## 3 — Silent correctness (the linter's blind spot)

Syntactically clean, runs for admin, wrong. This is where review earns its keep.

- **Field & method names exist** — against the brief, not memory. `account.move` not `account.invoice`; a real `partner_id`, not an invented `customer_id`. `odoo-ai all <model>` lists the real fields/methods; a name not in it is a hallucination until the brief proves otherwise.
- **Override lands at the intended MRO layer.** To extend a hook owned by `sale_stock`, `__manifest__.depends` must include `sale_stock`, not just `sale` — otherwise the override sits below the method it means to wrap and silently never runs. Confirm the chain: `odoo-ai all <model>` → MRO, against the depends the brief recommends.
- **`super()` is called, in the right place.** A layer that drops `super()` cuts the chain for everyone above it; an early `return` before `super()` skips it. Read the MRO; for big cross-app flows, `odoo-ai trace <model> <id> <method>` shows which frames actually fire.
- **Right hook, not the public shell.** Value-building belongs in `_prepare_*` / `_get_*` / `_action_*`, not an override of `create` / `write` / `action_confirm`. Overriding the shell to inject one value re-runs on every call and fights other modules (→ `odoo-dev`, `odoo-domain-playbooks`).
- **Built-in, not hand-rolled.** Derived value ⇒ computed/related field; invariant ⇒ `@api.constrains`; numbering ⇒ `ir.sequence`. Procedural logic in `write()` to compute or validate is a smell.
- **`create` override is batch-safe** — `@api.model_create_multi`, `vals_list` is a **list**, no `ensure_one()` inside. The linter flags the missing decorator; *you* confirm the body actually loops.
- **Wired in.** Every new `.py` imported in its `__init__.py`; every new XML in `__manifest__['data']` in dependency order; new OWL/JS in the right assets bundle. Unwired = dead code, no error.

## 4 — Performance (N+1 is the dominant bug)

- **No query in a loop.** `search` / `browse` per record → 1+N queries. Search once with an `in` domain, then `filtered` / `mapped`. The linter flags the shape; confirm the real cost with `odoo-ai trace <model> <id> <method>` → `total_sql` / per-call `sql_count` (→ `odoo-perf`).
- **`@api.depends` is exhaustive** on every stored compute, dotted paths included. The linter can't know the real registry deps — *you* compare the decorator against `model_brief.fields[<name>].depends`. A missing path = silent staleness.
- **`store=True` is justified** (searched / grouped / reported), not "just in case" — every stored compute adds an `UPDATE` to each write touching a dependency.
- **Batch `write` / `create`**, not field-by-field in a loop; **index only where searched/grouped**, not everywhere.

## 5 — Multi-company (the leak nobody tests)

Single-company tests pass; the leak appears for a user with two companies toggled on.

- **Isolation rule scopes to the allowed set, not the default company.** `[('company_id','=',user.company_id.id)]` leaks for a multi-company user — the correct shape is the **global** rule `['|',('company_id','=',False),('company_id','in',company_ids)]`. Evaluate the domain for a two-company user: `odoo-ai security <model> --user <id> --allowed-companies 1,2` → compare `effective_domain` (→ `ai-failure-modes.md` §9).
- **No hardcoded company.** Reads use `env.company` / `env.companies`, writes use `with_company(…)` — never `company_id=1` or `user.company_id` assumed.

## 6 — Version & frontend currency

Mostly mechanical — let the linter carry it (`odoo-ai validate`), then confirm the **target version** for the renames it can't date:

- `attrs=` / `states=` removed (v17) → direct `invisible=`/`readonly=` exprs; `<tree>` → `<list>` (v18); `<chatter/>` (v18); `name_get` → `_compute_display_name` (v17).
- `res.users.groups_id` → **`group_ids`** (v19); `ir.rule.groups` **unchanged**. Old `user.groups_id` raises `AttributeError` on 19 — test-setup code (`'groups_id': [(6,0,[…])]`) is the usual offender.
- `read_group` → `_read_group` (v18.2); `group_operator` → `aggregator` (v17.2); `type='json'` → `type='jsonrpc'` (v18.1); `self._cr/_uid/_context` → `self.env.*` (v19); `from odoo.osv import expression` → `odoo.Domain` (v18.1→19). Full table: `skills/odoo-introspect/references/version-matrix.md`.
- **OWL** reads `props.record.data[props.name]`, writes via `.update({…})`; correct import paths; template in the bundle (→ `odoo-owl`). Public JS: `publicWidget` (v17) vs **Interactions** (v18+) — pick by target version (→ `odoo-web`).

## Checklist

- [ ] Ran `odoo-ai validate <path…>` first; mechanical findings cleared.
- [ ] Every `sudo()` has a reason and isn't hiding a record rule (`security --user`).
- [ ] New model has ACL lines; row isolation is an `ir.rule`, not Python `filtered()`.
- [ ] Renames / new-required / changed-compute carry a migration (`upgrade-check`).
- [ ] Field & method names exist in the brief; override lands at the right MRO layer with `super()`.
- [ ] Logic is in a `_prepare_*` / compute / constraint hook, not a `create`/`write`/`action_*` shell.
- [ ] No query in a loop; `@api.depends` exhaustive vs the brief; `store=True` justified.
- [ ] Multi-company isolation evaluated for a two-company user (`security --allowed-companies`).
- [ ] Version renames confirmed against the target (`group_ids`, `check_access`, `_read_group`, …).
