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
  Targets Odoo 17/18/19.
---

# Odoo testing — the gate

The `odoo-introspect` skill stops the agent guessing; this stops the patch silently breaking something else. **A customization isn't done until it's proven.** Require all of this before submitting or merging.

**Version floor: Odoo 17/18, through Odoo 19 (current LTS).** The class/decorator names below are v17/18; for v16 and older see `skills/odoo-introspect/references/version-matrix.md`. Note newer **online** versions don't load demo data by default — don't rely on a demo record existing in a test; create what you assert on.

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

## The CI-bound evidence gate — proof CI produces, not the agent

A disciplined-looking test can still prove nothing: assert on a mock, `assertTrue(True)`, a happy-path that never hits the broken branch, or hand-authored evidence JSON. High coverage, green gate, runtime still throws. The CI-bound evidence gate closes this by moving proof to an **agent-untrusted runner (CI)** and binding it to the git diff. The agent writes code + tests; **CI generates and signs the evidence**. Run these (all `python3`, no DB needed except where noted) — or `odoo-ai deploy-gate --strict <bundle>` which enforces them together:

- **`odoo-ai diff-targets --base <sha> --head <sha>`** — AST-maps the git diff to `{model, method, changed_exec_lines}`. This is the anchor everything else binds to; produced from git, **not** from the agent.
- **`odoo-ai test-quality <paths>`** — AST-lint that BLOCKS the fakes: vacuous asserts, `assertRaises(Exception)`, a mocked model-method-under-test, swallowed exceptions, and tests not imported from `tests/__init__.py` (Odoo never runs those — a silent green).
- **`odoo-ai changed-coverage --targets diff_targets.json --coverage coverage.json`** — proves the **changed lines** ran under a real test context (not setup/import), 100% for critical/high, ≥90% normal. Run Odoo through `coverage run --branch --context=test_function` for both `-i` and `-u`.
- **runtime-path binding** — the recorder (`runtime_path_probe.py`, loaded in the odoo-bin test run) proves the changed method executed **through the live registry MRO** on a recordset of its own model; `odoo-ai runtime-path` assembles the artifact. Kills mocks, stubs, and wrong-MRO-layer tests that coverage alone can't catch.
- **`odoo-ai scenario-satisfaction --scenarios scenarios.json --observations obs.json`** — turns the risk scenarios above from advisory into enforced: `required − satisfied == ∅` (non-admin uid actually seen, ≥2 companies actually exercised, batch len ≥2, `-i`+`-u` both run, locked-period `UserError` actually raised).
- **`odoo-ai mutation-smoke --targets diff_targets.json ...`** — mutates the changed lines of each target (flip a comparison, remove a `raise` guard, swap a boolean op) and re-runs the covering tests; a **surviving** mutant means the test asserts nothing the mutation breaks (the antidote coverage can't provide). Required for high/critical/sensitive changes; a survivor there blocks. Targeted (changed lines only, capped per target) to stay affordable on Odoo's DB-spin-up cost.
- **`odoo-ai red-green-replay ...`** — CI re-runs the test-first ritual itself: checkout base, apply only the PR's test files, run → must **fail** with a *legitimate* red (not a `self.fail("TODO")` / import / syntax error); checkout head, run → must **pass** with the same test identities. Required when the manifest flags a bug fix; a faked or absent before/after blocks. (Head test output must be verbose so the passing identities are provable.)
- **`odoo-ai attest <artifact> --name X` / `verify-attestation`** — HMAC-signs each evidence file on the CI host (`ODOO_AI_ATTEST_KEY`, which must **never** exist inside the test container). `deploy-gate --strict` then **content-binds** every consumed artifact (the real file bytes must hash to something CI signed — a forged/hand-authored/tampered file is rejected even next to a valid unrelated envelope) and **commit-binds** it: export the true `HEAD` as `ODOO_AI_EXPECTED_HEAD_SHA` on the CI runner so a replayed past-green bundle from another commit blocks. Without that env var strict fails **closed** (`needs_human`, never a silent approve); the agent-authored `manifest.json` is **not** a trusted source for it.

**Trust boundary (read this — the gate is only as honest as CI's wiring).** The runtime facts (runtime-path, changed-coverage, scenario observations) are measured *inside* the same `odoo-bin` process the untrusted addon/test code runs in, then CI HMAC-signs the resulting files. Provenance proves *"CI signed these bytes,"* **not** *"the observer wasn't tampered with."* The `runtime_observer.py` module closes most of this: it's a CI-owned tracer that **fails closed** — it seals as `tampered`/`incomplete` (never `sealed`) if a test calls `sys.settrace(None)` *even briefly then restores it* (caught by a permanent `sys.addaudithook` settrace counter), never hits the target, or the evidence dir isn't confirmed non-writable by the test. `deploy-gate --strict` then blocks any `runtime_path` whose `producer.trace_integrity != "sealed"`, blocks a mismatched observer `tool_digest`, and **fails closed to `needs_human` when `ODOO_AI_EXPECTED_PROBE_DIGEST` is not pinned** (an unpinned observer identity can't be trusted). The seal is only trustworthy if CI injects the observer from a CI-owned path (not the repo) and writes evidence to a dir the test process can't overwrite — full wiring in `skills/odoo-introspect/references/ci-trusted-observer.md`. **Honest residual:** a sophisticated in-process test can still mutate the observer's own state (event list, counters) before it seals; nothing running inside one interpreter can be fully self-verifying — only OS/container isolation closes that, which is CI's job. This is a **CI-bound evidence gate with an explicit trust boundary**: treat `--strict = approve` as a **strong hardening gate, not an auto-approve guarantee**.

Genuinely un-eradicable: a test can assert the wrong business rule perfectly (tests need a human oracle), equivalent mutants survive, and coverage ≠ correctness — so **human review stays mandatory** for accounting/stock/payment/hr, security, and controllers even when the strict gate is green.

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
### CI-bound evidence (CI-produced, `deploy-gate --strict`)
- [ ] `diff-targets` extracted the changed method(s) from the git diff
- [ ] `changed-coverage` gate passes (changed lines covered by a real test)
- [ ] `runtime-path` binds each target to the live registry MRO (no mock/stub)
- [ ] `scenario-satisfaction` ok (required scenarios exercised, not just written)
- [ ] `test-quality` has 0 blocking (no vacuous assert / mocked model / swallowed exception)
- [ ] evidence bundle is CI-signed (`attest`) and `deploy-gate --strict` = approve
```

## References

- `references/testing-patterns.md` — runnable skeletons: `TransactionCase`, `Form()` onchange, `with_user` non-admin, multi-company leak assert, batch create override, `HttpCase` tour.
- **Generate the required scenarios**: `odoo-ai scenarios <model> [--methods a,b]` classifies the change risk and emits the *mandatory* test matrix (non-admin / multi-company / batch / `-i`+`-u` / locked-period for accounting) plus a `TransactionCase` skeleton with a failing stub per scenario — the test gate as a generator, not a memory check.
- **Enforce the proof**: `diff-targets`, `changed-coverage`, `runtime-path`, `scenario-satisfaction`, `test-quality`, `attest`/`verify-attestation`, and `deploy-gate --strict` — the CI-side, agent-untrusted gate that makes "high coverage but runtime breaks" a **block**, not a green tick. See "The CI-bound evidence gate" above.
