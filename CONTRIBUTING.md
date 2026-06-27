# Contributing

Thanks for helping improve **odoo-ai-skills**. This suite has one organizing
principle, and contributions should reinforce it:

> Read ground truth from the running instance first → build the smallest correct
> change → prove it with a test → review it before it merges. Never guess Odoo
> internals from memory.

## What this repo is

A [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin: a set of
skills (`skills/<name>/SKILL.md` + `references/`) plus the introspection engine
and `odoo-ai` CLI under `skills/odoo-introspect/scripts/`. Skills are
documentation; the scripts are the only executable surface.

## Project layout

```
.claude-plugin/                  # plugin + marketplace manifests
skills/<name>/SKILL.md           # one skill per directory
skills/<name>/references/        # progressive-disclosure deep dives
skills/odoo-introspect/scripts/  # introspection engine + odoo-ai CLI
skills/odoo-introspect/scripts/tests/   # unit + integration tests
.github/workflows/               # ci (unit) + integration
```

## Development setup

No build step. For the scripts you need Python 3.10+; the tests need `pytest`
(optional — each test file has a no-pytest `__main__` fallback).

```bash
python -m pytest skills/odoo-introspect/scripts/tests -q
# or, without pytest:
python skills/odoo-introspect/scripts/tests/test_pure_functions.py
```

## Writing scripts (the engine)

The introspection scripts run **inside `odoo-bin shell`**. To keep them testable
without Odoo, follow the established structure:

- Put **pure helpers** (no `env`, no ORM) at module level so they can be
  imported and unit-tested.
- Put env-dependent work in `run()`, guarded at the bottom:
  ```python
  if "env" in globals():
      run()
  ```
  This makes the module import-safe — importing it for tests must not touch a
  database.
- Emit **pure JSON between sentinels** (`===ODOO_<LAYER>_START===` …). Collect
  non-fatal problems into a `_warnings` list rather than failing silently.
- Bound everything that reads runtime data (string length, recordset size,
  recursion depth) and never raise from a serializer.
- If output can contain runtime values, **redact sensitive keys by default**
  (see `state_capture.py`); see `SECURITY.md`.

Add unit tests for every new pure helper in `tests/test_pure_functions.py`.

## Writing skills (the docs)

- One skill per directory with a `SKILL.md`; push detail into `references/`.
- Lead with *when to use this* and *read ground truth first*; show the exact
  introspection command before any code guidance.
- Be version-aware: target Odoo 17/18 through 19, and note deltas in
  `skills/odoo-introspect/references/version-matrix.md`.
- State Odoo-specific traps that fail **silently at runtime** (MRO vs runtime,
  `sudo()` bypassing ACL+rules, incomplete `@api.depends`, dead xpath, `noupdate`).

## Testing against a real Odoo

The integration smoke test asserts the env-bound paths the unit tests can't
reach. It's opt-in (skipped unless `ODOO_DB` is set):

```bash
# against a dev container — see references/introspection.md for the wrapper
ODOO_DB=<db> ODOO_CONF=/etc/odoo/odoo.conf ODOO_BIN=/path/to/odoo-docker \
    SMOKE_RECORD_ID=<id> \
    python skills/odoo-introspect/scripts/tests/integration_smoke.py
```

CI runs the same script against official `odoo:17.0` / `18.0` / `19.0` images. If your
change touches the scripts, please run it against at least one live instance and
say so in the PR.

## Pull requests

- Keep PRs focused; describe what changed, why, and how you verified it.
- Run `python -m pytest skills/odoo-introspect/scripts/tests -q` and confirm
  every script still compiles (`python -m py_compile <script>`).
- Update `CHANGELOG.md` under `[Unreleased]`.
- Update `references/sample-output.md` if you change a layer's JSON shape.
- Don't commit secrets, real customer data, or raw `state`/source JSON dumps.

## Commit messages

Conventional-commit style is used in history (`feat:`, `fix:`, `docs:`,
`ci:`, `chore:`). Please match it.

## Reporting bugs

Open an issue with the Odoo version, the command you ran, and the relevant JSON
(redacted). For security-sensitive reports, see `SECURITY.md` instead.
