---
name: odoo-debug
description: >-
  Diagnosing a failing Odoo instance — reading tracebacks and server logs,
  decoding errors (KeyError / "Field does not exist", MissingError, AccessError,
  ValidationError / UserError, CacheMiss, registry/loading failures, "Invalid
  view definition" XML errors), turning on dev mode (--dev=all/xml/qweb/reload),
  dropping into pdb / odoo-bin shell, scoping log output (--log-handler), logging
  SQL, and tracing what actually runs at runtime. Use whenever an Odoo stack
  trace, failed -i/-u, blank or 500 page, or "works locally not in prod" shows
  up, or before you guess which addon or layer caused it. Read the running
  instance instead of guessing. Targets Odoo 17/18/19.
---

# Odoo debugging

Most Odoo errors are about the **composed runtime**, not a typo: a field/method/view that exists only after the addon graph loads, an override that ran (or didn't) at the wrong MRO layer, a record rule, or a stale cache. The top traceback frame is often not the cause. **Decode the error class first, then read ground truth** before editing — delegate discovery to the **`odoo-introspect`** skill (`odoo-ai all <model>`).

**Version floor: Odoo 17/18, through Odoo 19 (current LTS).** `--dev` sub-flags and `--log-handler` names target 17/18+; `breakpoint()` needs Python 3.7+. Older → `skills/odoo-introspect/references/version-matrix.md`.

## Symptom → first tool

| Symptom | First tool |
|---------|-----------|
| `KeyError: 'x'` / "Field x does not exist" | `model_brief` field inventory — is it real, which addon, did the dep load? |
| `AccessError` / "not allowed to" | `odoo-security` → dump ACL + record rules (admin-works ⇒ record rule) |
| `MissingError` / record does not exist | delete/rollback ordering, or company record rule hiding it; `trace_flow` |
| Wrong / stale computed value | `model_brief` `depends` — incomplete `@api.depends` (see `odoo-perf`) |
| "My override never runs" | `model_brief` MRO + `trace_flow` — real call order, not assumed |
| Big flow does the wrong thing | `trace_flow` (Layer D) — actual cross-addon call sequence |
| Slow endpoint / suspected N+1 | `trace_flow` SQL counts → `odoo-perf` |
| "Invalid view definition" / blank form | `entrypoints` (resolved arch) + `--dev=xml` |
| Crash only at `-i` / `-u` | the **first** traceback in the boot log; load-order / data error |
| "My change/edit didn't apply at all" | `preflight` (via `odoo-introspect`) — installed? `-u` run? shadow `addons_path`? right file? |

## Decode the error class

| Error | Usually means | Look at |
|-------|---------------|---------|
| `KeyError` / "Field does not exist" | field not in the registry — missing manifest `depends`, typo, or addon not installed | `model_brief.fields`, `__manifest__.depends` |
| `MissingError` | record deleted / rolled back / hidden by a company record rule by the time you touched it | rule (company), flow ordering |
| `AccessError` | ACL or record rule denied — **not** a missing field | `odoo-security` dossier |
| `ValidationError` | an `@api.constrains` or `_sql_constraints` rejected the value | which constraint (brief decorators) |
| `UserError` | a deliberate business guard raised in code | the raising method (grep the message) |
| `CacheMiss` | reading a field invalidated / never computed | flush/recompute order; raw SQL without invalidate |
| "Invalid view definition" | an xpath missed, or an `invisible=` / `groups` expression broke after another addon changed the arch | `entrypoints` resolved arch + `--dev=xml` |
| Registry / loading failure at boot | a module's Python or XML import error, bad `depends`, circular import | the **first** traceback in the log |

`AccessError` vs record rule: a bare ACL denial reads "not allowed to {op} ... (Operation ...)"; a record-rule denial mentions the document/rule. `record_rules` in the brief tells you which fired.

## Dev tooling

| Flag / call | Does |
|-------------|------|
| `--dev=all` | reload Python on save, richer tracebacks, unminified assets |
| `--dev=xml` | serve views from XML files, skipping the DB copy — iterate views without `-u` |
| `--dev=qweb` | QWeb template debug comments / `t-debug` |
| `--dev=reload` | auto-restart the server on `.py` change |
| `breakpoint()` in code | drops to **pdb** in the server console (remove before commit) |
| `odoo-bin shell -d <DB>` | live ORM REPL — reproduce in 3 lines instead of clicking the UI |
| `--log-level=debug` | everything (noisy) |
| `--log-handler=odoo.addons.<mod>:DEBUG` | scope DEBUG to one addon — the targeted move |
| `--log-handler=odoo.sql_db:DEBUG` | log **every SQL** with params (find N+1; see `odoo-perf`) |

## What actually runs — trace it

When the bug is "the flow does X but should do Y", MRO and grep can't tell you the real sequence. `trace_flow` (Layer D, via `odoo-introspect`) executes the method on a throwaway record and records the cross-addon call order **+ SQL per call** (rolls back by default):

```bash
odoo-ai --db <DB> trace <model> <record_id> <method>
```

Read `distinct_steps` for the call order and `total_sql` / per-call `sql_count` for where queries explode (hand off to `odoo-perf`).

## Gotchas that fail silently

- **The top frame lies.** Odoo wraps and re-raises; the real cause is often the *first* traceback in the log (especially at boot), not the last printed one.
- **`--dev=xml` hides DB-only bugs.** It serves views from files, so a bug that only reproduces from the **DB** arch (a customized / Studio view) vanishes under it. Use `entrypoints` to see the *resolved* arch the DB actually serves.
- **Swallowed exceptions.** `except Exception: pass` in a server action / automation hides the real error; grep the addon for bare excepts when "nothing happens".
- **`-u` didn't recompute.** Changing a stored compute's logic doesn't recompute existing rows automatically; values look wrong until an upgrade marks the field for recompute (see `odoo-perf`).
- **CacheMiss after raw SQL.** A `cr.execute` UPDATE without `invalidate_recordset` leaves the ORM serving stale values, then missing (see `odoo-perf`).
- **The edit that "did nothing" was never loaded.** Before assuming a code bug when a change has *no* effect, run `odoo-ai preflight <module>`: the module may be uninstalled, un-`-u`'d, loaded from a **shadow copy** on a duplicate/auto-injected `addons_path`, or the file isn't imported in `__init__`. All four fail silently and look like "my code is wrong".

## References & related skills

**This skill's reference**
- `references/tracebacks.md` — annotated real tracebacks per error class, the log-reading recipe, `--log-handler` patterns, pdb / shell session examples, the "first vs last frame" rule, and install/upgrade failure diagnosis.

**Other skills in the loop**
- `odoo-introspect` — Tier 0 engine; `odoo-ai all <model>`, `trace_flow` (Layer D) for real call order + SQL.
- `odoo-security` — `AccessError` / record-rule decoding. `odoo-perf` — SQL-count / cache / N+1.
- `odoo-dev` — once the cause is known, the smallest safe patch.
