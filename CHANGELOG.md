# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1]

### Added
- **Worked example** (`examples/sale_confirm_guard/` + `examples/sale-order-walkthrough.md`):
  a real `sale.order` change taken through introspect → plan → patch → test,
  with every decision grounded in the live registry. Its tests run in CI (the
  `example` job in `integration.yml`).
- **Demo GIF** rendered from a committed VHS tape (`examples/demo.tape`),
  embedded in the README.
- Layer F is now exercised in the integration smoke test by **auto-resolving a
  record id** when `SMOKE_RECORD_ID` is unset (15 checks instead of 12), so
  redaction is covered in CI.

### Fixed
- Integration CI: pass `HOST`/`PORT`/`USER`/`PASSWORD` so the official Odoo
  image connects to the Postgres service instead of the entrypoint's default
  `db` host.

## [0.3.0]

Hardening pass driven by a review of the introspection engine and validated
against a live Odoo 18 instance.

### Fixed
- **`noupdate` semantics were stated backwards** in `odoo-introspect` (SKILL,
  `metadata.py`, `sample-output.md`) and in an `odoo-data` comment. Corrected
  everywhere: a `noupdate=True` record is loaded once on install and then
  **protected from `-u`** (later XML edits don't apply — change it with a
  migration); only default `noupdate=False` records are re-asserted from XML on
  `-u`. This had inverted the migration/data advice an agent would follow.
- **`manifest_depends` mis-split official vs custom by module author.** Custom
  modules routinely ship `author = 'Odoo S.A.'`, so the split was wrong on real
  databases. Replaced with **path-based classification** (`core` / `enterprise`
  / `local` / `unknown`) using each module's on-disk location — ground truth.
- `odoo-data` mislabeled `metadata.py` as "Layer D"; it is **Layer C**.
- `state_capture.py` docstring claimed frames were filtered by a `/addons/`
  path; corrected to `odoo.addons.*` **module name** (matches the implementation
  and catches custom/enterprise addons mounted outside a literal `/addons/`).

### Added
- **Default redaction in Layer F (`state_capture.py`).** Locals, dict keys, and
  field names matching a sensitive-key set (`password`, `token`, `secret`,
  `api_key`, `authorization`, `session`, …) are emitted as `<redacted>`.
  Configurable via `--redact-extra a,b` / `REDACT_EXTRA` and disabled with
  `--no-redact` / `NO_REDACT`. Output carries a `redaction` block for
  transparency. Source bodies and explicit `--fields` values are **not**
  redacted (documented).
- **Field inventory enrichment (Layer A).** Selection fields now expose their
  `(value, label)` literals (method-resolved when possible); relational fields
  add `ondelete` / `inverse_name` / `domain`; all fields add `index`, `copy`,
  `translate`, `tracking`, `has_default`, and `help`.
- **View inheritance chain (Layer B).** Each rendered view now includes
  `root_view_id` and an `inheritance_chain` (base view + applied extensions in
  application order, with xmlid/priority/mode). New `VIEW_XMLID` / `VIEW_ID`
  (CLI `--view-xmlid` / `--view-id`) render a specific view.
- **Integration smoke test** (`scripts/tests/integration_smoke.py`) — runs the
  layers against a real Odoo and asserts structural invariants; opt-in (skipped
  unless `ODOO_DB` is set, so the unit CI is unaffected).
- **Integration CI** (`.github/workflows/integration.yml`) — runs the smoke
  test against official `odoo:17.0` / `odoo:18.0` images with a Postgres
  service, on demand / weekly / on PRs touching the scripts.
- Documentation: container wrapper + integration-test instructions in
  `references/introspection.md`; a "Security — handling introspection output"
  section in the README; updated `sample-output.md` for the enriched shapes.

### Security
- Introspection output (especially Layer F `state` and Layer A `SOURCE=1`) can
  contain secrets, PII, or proprietary logic. Redaction is now on by default for
  `state`; see `SECURITY.md` for the full handling guidance.

## [0.2.0]

### Added
- Initial public skills suite: router (`odoo`), the introspection engine
  (`odoo-introspect`) with Layers A–F + the `odoo-ai` CLI, the core loop
  (`odoo-dev`, `odoo-module-scaffold`, `odoo-views`, `odoo-security`,
  `odoo-testing`, `odoo-review`, `odoo-debug`), frontend/reporting
  (`odoo-owl`, `odoo-web`, `odoo-reports`), lifecycle (`odoo-data`,
  `odoo-migration`, `odoo-perf`, `odoo-deploy`), and domain playbooks.
- Packaged as an installable Claude Code plugin with a self-hosted marketplace.
- Pure-function unit tests and a compile/test CI workflow.
- Odoo version coverage extended to 19 (current LTS).

[Unreleased]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tuanle96/odoo-ai-skills/releases/tag/v0.2.0
