# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-06-27

A feature release building on the 0.3.x hardening: two new introspection layers
(effective security, richer runtime trace), a graph-aware field resolver, Odoo
19 in CI, committed JSON fixtures, and domain scenario playbooks.

### Added
- **Committed JSON sample fixtures** (`skills/odoo-introspect/references/samples/`).
  One full, valid JSON document per layer (brief / entrypoints / metadata / trace
  / refs / preflight / security / state) for `sale.order`, the machine-readable
  companions to `sample-output.md`. A new test (`test_sample_fixtures.py`) parses
  each and asserts it carries the required top-level keys the matching script
  emits, so the fixtures can't silently drift from the code.
- **Scenario playbooks** (`skills/odoo-domain-playbooks/references/scenario-playbooks.md`):
  three end-to-end `introspect → plan → patch → test` walkthroughs — `invoice_post`
  (`account.move._post`), `picking_validate` (`stock.picking.button_validate` +
  the v17 field trap), and `mrp_produce` (`mrp.production.button_mark_done`) — each
  with the right hook, the guard-before / react-after rule, and per-flow gotchas.
- **Richer trace summary (Layer D, `trace_flow.py`).** The trace now carries a
  `summary` block: `top_self_sql` (SQL hotspots by SELF cost — cumulative minus
  children, computed in one O(n) pass over the depth-ordered trace, so a thin
  parent no longer masks an expensive callee), `call_counts` (most-invoked
  `(model, method)` pairs → N+1 smell), `writes_by_model` (creates/writes per
  model + the field **names** touched — names only, never values), and
  `exception_origin` (the innermost addon frame an exception passed through).
  `trace_flow.py` was refactored to be import-safe (env work moved into `run()`),
  so the new pure helpers `compute_self_sql` / `summarize_calls` /
  `aggregate_writes` are unit-tested.
- **Effective-security simulator (Layer G, `security_sim.py` + `odoo-ai security`).**
  Answers "what can THIS user (in THIS company) actually do to a model, and which
  rows can they see?" — combines `ir.model.access` additively across the user's
  applicable rows, resolves each mode's record-rule `effective_domain` via Odoo's
  own `ir.rule._compute_domain` under `with_user`, lists group-restricted fields
  (the diff of `fields_get` as superuser vs as the user), reports multi-company
  posture, and cross-checks the ACL verdict against `check_access` /
  `check_access_rights`. Flags the superuser bypass and the `sudo()` blind spot.
  Pass the user via `--user` / `AS_USER` (login or id) and `--company` /
  `AS_COMPANY`. Pure helpers `effective_acl` / `field_visible` /
  `parse_field_groups` are unit-tested; a `smoke_security` integration check runs
  it against a real non-superuser.
- **Odoo 19.0 in the integration smoke CI matrix** (alongside 17.0 / 18.0),
  using the official `odoo:19.0` image + Postgres service.
- **Layer E now exercised in the integration smoke test.** `field_refs.py` runs
  in graph-resolved mode against a real registry (relation hops + `comodel_name`)
  and asserts `path_resolution == "graph-resolved"`, the severity buckets, and
  that every resolved field reference lands on exactly the target `model.field`.
- **Graph-aware field reverse-impact (Layer E, `field_refs.py`).** New
  `--resolve-paths` / `RESOLVE_PATHS=1` mode walks each dotted `depends` /
  `related` path through `comodel_name` and confirms it resolves to the *exact*
  `model.field` instead of matching only the last segment — so `partner_id.name`
  and `company_id.name` are no longer conflated. Output adds `path_resolution`
  (`graph-resolved` / `text-heuristic`) and a `resolved_via` detail on matched
  field references. Pure helpers `resolve_dotted_path` / `path_hits_target` are
  unit-tested.

### Changed
- **Clarified that `entrypoints.py` `inheritance_chain` is diagnostic/best-effort.**
  The resolved `arch` is authoritative for a given `get_view()` context; the
  chain is ordered by parent/priority but the real applied set also depends on
  context, action, groups, company and website. Added a top-level `_caveat` and
  updated the docstring / `sample-output.md`.

### Fixed
- **Stale `manifest_depends` docs in `sample-output.md`.** The Layer A sample
  and note still described the pre-0.3.0 author-heuristic split
  (`official_addons` / `custom_addons_seen`); updated to the path-based
  `by_location` (`core` / `enterprise` / `local` / `unknown`) + `module_paths`
  shape the code actually emits. Added a Layer E (`field_refs`) sample.

## [0.3.2]

Hardening pass from a review focused on safe defaults and runtime robustness.

### Security
- **Server-action / cron code bodies are now gated in Layer A (`model_brief.py`).**
  `auto_triggers.server_actions[].code` and `crons[].code` previously dumped the
  full body, which often embeds secrets, endpoints, and sensitive business
  logic. By default they now emit a redacted summary (`code_present`,
  `code_len`, `code_preview`); set `CODE=1` to include full bodies (trusted
  context only). An `auto_triggers._code_gating` marker records the mode.

### Fixed
- **`trace_flow.py` SQL counter is now best-effort.** Wrapping the cursor's
  `execute` is guarded; if it fails in the target environment the call graph is
  still traced. Output adds `sql_count_enabled` (bool) and `warnings`, and
  `total_sql` is `null` when counting was disabled instead of failing the whole
  trace.

### Changed
- **Clarified `odoo-ai all` scope** in the CLI help, runtime output, and docs:
  `all` = `brief + entrypoints + metadata` (+ optional `trace`), and explicitly
  **not** `refs` / `preflight` / `state`.
- Documented the code/source leakage caveat and the `CODE=1` gate in
  `odoo-introspect` SKILL and `sample-output.md`; added `CODE` to the container
  env pass-through list in `references/introspection.md`.

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

[Unreleased]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tuanle96/odoo-ai-skills/releases/tag/v0.2.0
