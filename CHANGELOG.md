# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-06-27

Adds **Layer H — the Native Capability Atlas**: a Step-0 scanner and a new
`odoo-capabilities` skill that answer the question that comes *before* "where do
I extend?" — **"does Odoo already ship this?"** The suite's rule (read ground
truth, don't guess) applied one step earlier: enumerate the native surface from
the live registry and reuse it, instead of reinventing sequences, crons,
automation rules, mixins, or wizards in custom code.

### Added
- **`odoo-ai capabilities <model>` / `--module <addon>`** (new `capabilities.py`,
  Layer H). Enumerates the native capability surface straight from the running
  instance: per **module** (via the `ir.model.data` xmlid registry) it lists the
  models, wizards, window/server/report actions, crons, automation rules,
  sequences, mail templates, feature groups, and menus the addon shipped — each
  with its **xmlid as evidence**; per **model** (a.k.a. `feature-map`) it maps
  the mixins (mail/activities/portal), functional fields, actions/reports, the
  bound Action-menu surface (where native wizards attach), crons, and automation
  rules around it. Pure enumeration — **no matching/scoring**, and it **never
  reads server-action/cron code bodies** (nothing to gate).
- **`odoo-ai feature-map <model>`** — alias for `capabilities <model>`.
- **New `odoo-capabilities` skill** (Tier 0, Step 0). The "native-first" gate:
  before *adding* a field/model/wizard/report/cron/automation or *overriding a
  core flow*, enumerate native candidates, cite the instance evidence, and decide
  reuse / reject-with-reason / the real gap. Stays silent for bug-fixes, view
  tweaks, and edits inside your own module. Carries
  `references/native-primitives.md` — the anti-pattern → native-primitive
  catalogue (ir.sequence, ir.cron, base.automation, computed-vs-onchange,
  mail.thread/activities, the standard wizards, `_prepare_*`/`_action_*` hooks,
  reports, feature groups, record rules), version-noted for 17/18/19.
- **Worked example** `examples/native-capability-check.md` — two native-checks
  where reading the instance turns "write a module" into "reuse the existing
  `ir.sequence` / `mail.thread` / automation rule": the best patch is no patch.
- **Tests** — pure-function coverage for the scanner (`bucket_for_imd_model`,
  `is_functional_field`, `mixin_capabilities`, `count_surface`) and the CLI
  `_summ` capabilities summaries, in both the pytest and unittest suites; an
  integration smoke (`capabilities.py`, model + module mode) added to
  `integration_smoke.py`.

### Changed
- **Workflow gains a scoped Step 0.** The `odoo` router and `odoo-dev` now run
  `native-check → introspect → plan → code → test → review`, with Step 0 firing
  only for additive / core-overriding tasks. README, tier table, and the
  introspect script list updated; skill count 17 → 18.

## [0.4.2] - 2026-06-27

A follow-up hygiene patch on 0.4.1: surface the code-gating policy on the CLI,
lock the company-aware Layer G fix with a real regression test, document a
Layer D capture boundary, and finish the 17/18/19 docs sync.

### Added
- **`odoo-ai brief` / `all` gain `--code-preview` and `--code` flags.** The
  code-gating policy (server-action / cron bodies are summarized, not dumped)
  was previously reachable only via the `CODE_PREVIEW=1` / `CODE=1` env vars.
  The flags make the opt-in explicit on the CLI; the env vars still work and the
  default stays gated (no preview).
- **`odoo-ai security` gains `--allowed-companies`** (env `AS_ALLOWED_COMPANIES`).
  Layer G previously scoped the rule engine to a single active company
  (`AS_COMPANY`); this models a user with several companies toggled ON, so
  `company_ids` in a record-rule domain resolves to the full allowed set (Odoo's
  `env.companies`), not just one. Default stays `[AS_COMPANY]`. The simulated set
  is reported as `company.simulated_allowed_company_ids`.
- **Multi-company Layer G regression test** (`integration_smoke.py`). Sets up
  two companies, a user allowed in both, and a company-scoped record rule, then
  runs `security_sim.py` against each company and asserts the effective
  read-domain scopes to the *simulated* company (and that the two domains
  differ) — locking the 0.4.1 `_compute_domain` company-binding fix. It also
  checks that `--allowed-companies A,B` widens the domain to cover both. The
  setup is rolled back, so it never persists, even against a dev DB.

### Fixed
- **Layer G (`security_sim.py`) now runs on Odoo 19.** Two v19 changes broke it,
  both caught by running the smoke test against the official `odoo:19.0` image:
  - `res.users.groups_id` was renamed to **`group_ids`** (`ir.rule.groups` was
    not). Reading `user.groups_id` raised `AttributeError`; the script now
    resolves the field name from the registry, so it works on 17 → 19.
  - `ir.rule._compute_domain` now returns an **`odoo.orm.domains.Domain`**
    object (the new Domain API), which `json.dumps(default=str)` silently
    stringified — `effective_domain` came out as a string instead of a
    structured list. A new `normalize_domain` helper converts the Domain to the
    classic prefix-list form, keeping the output machine-readable across
    versions.
  Both deltas are now documented in `references/version-matrix.md`.
- **`introspection.md` integration-matrix note synced to 17/18/19.** The final
  line still read `odoo:17.0`/`18.0`; CI runs `17.0` / `18.0` / `19.0`.

### Changed
- **`writes_by_model` (Layer D) documents its capture boundary.** The trace
  summary now carries a `_writes_caveat`: field names are captured from traced
  `odoo.addons.*` frames only, so a `write` on a model that doesn't override
  `write` in an addon (running in core `odoo.models`) isn't counted. The
  `odoo-introspect` SKILL gotchas note the same, so the write map is read as
  "writes seen in addon code", not "every ORM write the flow made".

## [0.4.1] - 2026-06-27

A correctness and hygiene patch on top of 0.4.0: company-aware record-rule
simulation, no code preview by default, multi-create field capture, and docs
synced to the 17/18/19 integration matrix.

### Fixed
- **Layer G (`security_sim.py`) now binds the rule engine to the simulated
  company.** `ir.rule._compute_domain` was called under `with_user(user)` only,
  so company-dependent record rules (those referencing `user.company_id` /
  `allowed_company_ids`) resolved against the user's *default* company even when
  `AS_COMPANY` was set — the ACL/field checks honored the company but the
  effective record-rule domain could silently diverge. The engine is now
  `with_company(company).with_context(allowed_company_ids=[company.id])` so the
  effective domain matches what that company actually sees at runtime.
- **`brief` no longer emits a code preview by default.** `auto_triggers`
  server-action / cron bodies now report `code_present` / `code_len` with
  `code_preview = null`; even a 200-char head slice can leak a token, webhook
  URL, or API key. A short head slice is opt-in via `CODE_PREVIEW=1`, and full
  bodies still require `CODE=1` (both trusted-context only). `_code_gating`
  records which mode produced the output.
- **Layer D (`trace_flow.py`) captures field names for multi-create.** Modern
  `@api.model_create_multi def create(self, vals_list)` stores the payload in
  `vals_list`, not `vals`, so `writes_by_model` reported the create count but an
  empty field list. The tracer now falls back to `vals_list` when `vals` is
  absent.

### Changed
- Docs synced to the integration matrix: README and CONTRIBUTING now state the
  smoke test runs against official `odoo:17.0` / `18.0` / `19.0` images (README
  also notes the `sale_confirm_guard` worked-example job on `odoo:18.0`).
- `CODE_PREVIEW` added to the container env pass-through list in
  `references/introspection.md`; the `odoo-introspect` SKILL code-gating note and
  `sample-output.md` updated for the no-preview default.

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

[Unreleased]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.4.2...HEAD
[0.4.2]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tuanle96/odoo-ai-skills/releases/tag/v0.2.0
