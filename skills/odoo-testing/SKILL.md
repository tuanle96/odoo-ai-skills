---
name: odoo-testing
description: >-
  Proving an Odoo customization works before merge — writing and running Python
  tests for models, method overrides, computed fields, onchange, constraints,
  security, and cross-addon flows. Use after any server-side change (new field,
  overridden create/write, compute/constrains, ACL or record-rule edit) and when
  deciding the base class, at_install vs post_install tags, or how to test
  non-admin / multi-company / batch / onchange behavior. A patch isn't done until
  a test fails before it and passes after. The test gate for the odoo-dev skill.
  Targets Odoo 17/18.
---

# Odoo testing — the gate

The `odoo-introspect` skill stops the agent guessing; this stops the patch silently breaking something else. **A customization isn't done until it's proven.** Require all of this before submitting or merging.

**Version floor: Odoo 17/18.** The class/decorator names below are v17/18; for v16 and older see `skills/odoo-introspect/references/version-matrix.md`.

## Where tests live

Python tests go in a `tests/` subpackage, **imported from `tests/__init__.py`** — Odoo only collects tests that are imported. The package loads only when the module is installed/updated with `--test-enable`.

```
my_module/
├── __init__.py
└── tests/
    ├── __init__.py          # from . import test_sale_confirm
    └── test_sale_confirm.py
```

## Base classes — pick by what you drive

| Class | Import | Use for |
|-------|--------|---------|
| `TransactionCase` | `odoo.tests.common` | The default. Each test method runs in its own savepoint, rolled back after. Shared data in `setUpClass`. |
| `Form` | `odoo.tests.common` | Drive a record **through the UI's onchange engine** — the only way to exercise `@api.onchange` from Python. |
| `HttpCase` | `odoo.tests.common` | Controllers, and JS **tours** via `self.start_tour(...)`. Runs headless Chrome. |

> `SavepointCase` is **gone** (merged into `TransactionCase` since v15). `TransactionCase` now does savepoints and supports `setUpClass` — don't import `SavepointCase`. (`odoo.tests` re-exports all of the above, so `from odoo.tests import TransactionCase, Form, HttpCase, tagged` works too.)

## Tags: `at_install` vs `post_install` — get this right

```python
from odoo.tests.common import TransactionCase
from odoo.tests import tagged

@tagged('post_install', '-at_install')
class TestSaleConfirm(TransactionCase):
    ...
```

- `at_install` (the **default**) runs right after the module installs, **before later modules load**.
- `post_install` runs after **all** modules are installed. Use it whenever another addon can alter the behavior under test — **every cross-addon flow** (sale↔stock↔account↔mrp). Testing those `at_install` gives false greens, because the addon that changes the behavior isn't loaded yet.

## Test-first for every bug fix

Write the test that **fails on current code**, confirm it fails, apply the fix, confirm it passes. A bug fix without a failing-then-passing test is unverified. (Same loop the `odoo-dev` skill hands you after its CODE step.)

## The cases this codebase keeps getting wrong

Runnable skeleton for each in `references/testing-patterns.md`.

- **Non-admin user** — change touches ACL / record rules / `sudo()` / groups? Run it as a real user via `record.with_user(user)`, not admin. "Works as admin" proves nothing about a salesperson, who is constrained by record rules admin bypasses.
- **Multi-company** — model has `company_id`? Create records in ≥2 companies and assert **no cross-company leakage**; respect `self.env.company`.
- **Batch create/write** — overrode `create`/`write`? Test with a **list / multi-record recordset**, not one record. Catches the classic "works on one, breaks on many" and a missing `@api.model_create_multi`.
- **Onchange vs API** — logic in `@api.onchange`? Add a test that `create()`s directly (no onchange fires) and asserts the invariant still holds — or move the logic to a compute/constraint. Onchange is UI-only.

## Install / update — not just the running DB

- `-i <module>` on a **clean** DB: proves it installs from scratch (data/XML/constraints all valid).
- `-u <module>` on a DB **with existing data**: proves the upgrade path (new required fields, migrations, stored-compute recomputes) doesn't break live records.

```bash
odoo-bin -d clean_db -i my_module --test-enable --stop-after-init
odoo-bin -d staging  -u my_module --test-enable --stop-after-init
# one class or method only:
odoo-bin -d clean_db -u my_module --test-enable --stop-after-init \
  --test-tags /my_module:TestSaleConfirm.test_confirm_reserves_stock
```

`--test-tags` syntax: `[-][tag][/module][:class][.method]`; a leading `-` deselects.

## PR checklist (reject if missing)

```md
## Odoo AI Customization Checklist
### Runtime facts (attach odoo-introspect JSON)
- [ ] model_brief JSON attached (fields + MRO + super analysis)
- [ ] entrypoints JSON attached if a button/view/action is involved
- [ ] trace_flow JSON attached for large flows (sale/stock/account/mrp)
- [ ] security impact noted (ACL / record rules) if touched
### Design
- [ ] Reused existing fields/methods/hooks where possible
- [ ] Smallest extension point (prefer _prepare_* / _action_* over create/write/action_*)
- [ ] Manifest depends place the override at the intended MRO layer
- [ ] No unjustified sudo(); multi-company considered; batch-safe
### Tests
- [ ] Test fails before patch, passes after
- [ ] Non-admin user tested (if security touched)
- [ ] Multi-company tested (if company_id)
- [ ] Batch create/write tested (if overridden)
- [ ] post_install used for cross-addon flows
- [ ] `-i` clean DB and `-u` data DB both pass
```

## References

- `references/testing-patterns.md` — runnable skeletons: `TransactionCase`, `Form()` onchange, `with_user` non-admin, multi-company leak assert, batch create override, `HttpCase` tour.
