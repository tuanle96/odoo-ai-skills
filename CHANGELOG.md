# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **`odoo-valuation-repair` (new skill)** — diagnose and repair broken
  inventory valuation on AVCO/FIFO products, distilled from a production
  engagement that repaired hundreds of drifted books: the core invariant
  (`Σ stock.valuation.layer.quantity` = physical stock in valued locations),
  the read-only RCA ladder (formula check, book-start dating via
  moves-vs-layers by month, config-flip archaeology on `stock_move_id=False`
  layers, per-lot tear listing under `lot_valuated`), a **fail-closed
  per-lot repair runbook** delivered as a DRY-RUN-first server action
  (SaaS-safe, context-parameterised, self-verifying with full rollback,
  remaining-qty hygiene against `_run_fifo_vacuum`, stored product/lot cost
  sync via jsonb SQL to avoid `_change_standard_price` double layers), and a
  daily drift-check SQL that also catches positive-but-wrong costs. Ten-row
  gotcha table (readonly `fields_get` metadata on SVL vs RPC create,
  `code_store` jsonb, NULL-company quants, transit scope, safe_eval limits).

## [0.15.0] - 2026-07-11

Three battle-tested additions distilled from a real statutory-reporting
engagement (full Vietnamese TT99 financial-statement set built and verified on
a production clone), plus the fleet-migration hardening accumulated since
0.14.0. The suite is now 24 skills.

### Added
- **`odoo-statutory-reports` (new skill)** — dynamic financial statements via
  the `account.report` engine (Enterprise `account_reports`): the
  line/expression anatomy, engine table (domain/aggregation/account_codes/
  tax_tags/external/custom), the `sum`/`-sum` sign convention, `date_scope`
  semantics (`strict_range` / `from_beginning` / `to_beginning_of_period`),
  the full custom-engine contract (`_report_custom_engine_*` full-name
  formula, `custom_handler_model_id`, the `{'result': …}` return key,
  `_get_report_query`), **self-verifying statutory design** (catch-all lines
  = 0, balance identities, reconciliation pairs, suspense visibility,
  prefix-overlap audit), regime versioning (never edit the old report in
  place), the render-test ritual (the date dict must go INTO
  `get_options()`), and the xlsxwriter data-pack escape hatch for what a grid
  can't hold.
- **`odoo-domain-playbooks`: `references/vietnam-accounting.md`** — the first
  country/regime playbook: Vietnamese accounting compliance verified 07/2026
  (with a re-verify instruction): the TT200→TT99/2025 regime switch with
  chart and statement deltas (B01/B02/B03/B09), the sổ-kế-toán
  "quy chế mapping appendix" pattern, the tax calendar (e-invoice NĐ254/TT91,
  VAT 8% to 31/12/2026, CIT Luật 67/2025, audit NĐ90/2025), what `l10n_vn*`
  ships vs what to build, and design decisions that survived a real client
  (expense categories = CoA sub-accounts not analytic tags, perpetual
  152→154→155 costing, the 131/331 no-offset per-partner split).
- **`odoo-worktree` (new skill)** — isolated feature development on a LIVE
  Odoo dev environment: the container runs the main tree's uncommitted files
  while a git worktree holds a clean branch cut from production; path-scoped
  sync only (`rsync` / `git restore --source`), idempotent `post_init_hook`
  adopt-vs-create packaging, and the mandatory clean-install test ritual
  (`-u` never re-runs hooks).
- **`odoo-upgrade`: migration coverage hardening for full 18→19 fleets.**
  `upgrade_verify.py` now supports batch `--modules-file` installs and reports
  `loaded_count/expected_count/missing_modules`; `migrate_all.py` classifies
  upstream/OCA/Enterprise/auto-install routing flags, emits `source_sweep.json`,
  writes `install_set.txt` unconditionally, and can stage a clean portable addons
  dir. New `source_sweep.py` is a non-mutating checklist scanner for the
  source-verified breakages the manifest cannot prove (`groups_id`, `mobile`,
  SVL hooks, UoM tree, data deletes, undeclared imports). `db_upgrade.py`
  `--modules-file` enforces per-custom-module load parity during check/full.
- **`odoo-upgrade`: battle-tested fleet workflow + two new assets from a real
  89-module production migration** (45/45 portable modules verified green on
  Odoo 19 in 17 iterations + 3 parallel domain agents).
  (1) `scripts/anchor_check.py` — offline view-anchor validator: composes every
  core parent view through its full target-version inheritance chain (Odoo
  locate semantics, `hasclass()`) and reports ALL broken xpath anchors at once,
  instead of one per crashed install; needs `lxml`.
  (2) `references/field-notes-18-19.md` — 27 verified breakage→fix entries the
  generated manifest cannot express (res.groups→privilege restructure,
  `group_ids` vs `all_group_ids` semantics, SVL removal, uom tree,
  mobile→phone, search-view RNG rules, prod-export data patterns) plus fleet
  process lessons. (3) `migrate_all.py` gains production-scoping:
  `--installed-file` (only port what the prod db actually has installed —
  18/89 modules on the real fleet were dead) and `--exclude` with transitive
  dependent closure, emitting the ready-to-install `install_set.txt`.
  SKILL.md's "Migrating EVERYTHING" rewritten as the proven 8-step outer loop
  (scope by prod db → fleet brief → pre-flight known transforms → rewriters +
  anchor_check → batch verify loop with positive per-module load proof →
  db rehearsal → done-means).
- **`odoo-upgrade`: `scripts/preflight.py`** — applies the 10 deterministic
  field-notes transforms in one pass before any container run
  (`_sql_constraints`→Constraint incl. rewriter-crashing dead comment blocks,
  version bump, description xml-decl strip, target=inline, search-view group
  string/expand, module categories, tree leftovers, odoo.fields stdlib import)
  and emits `pydeps.txt`. Idempotent, unit-tested. Distilled after the same
  transform pass was hand-written across two fleet migrations — the skill's own
  "codify experience into artifacts" lesson applied to itself.
- **`upgrade_verify.py` now brings the db up (`compose up -d --wait db`) before
  the compose run** — a cold `compose run` raced postgres startup and died on
  connect (observed twice), which would otherwise burn a verify iteration on a
  non-failure.
- **`odoo-upgrade` skill — cross-version module porting (18→19), 21st skill.**
  Four pieces no existing rewriter/pattern-library has: (1) `gen_manifest.py`
  *generates* the breaking-change manifest from real source-tree diffs (hand-curated
  lists go stale — OCA's renamed-model data stops at 16→17); ships
    `references/manifest_18_19.json` generated from community trees (565 vs 639
    scanned addon dirs in the shipped metadata) with confirmed ground truth (`hr.contract`→`hr.version`,
  `hr.candidate`→`hr.applicant`, `hr.expense.sheet`→`hr.expense`). (2)
  `upgrade_brief.py` cross-references a custom module against the manifest —
  severity-ranked findings with file:line, heuristic candidates always labeled
  with similarity scores. (3) `upgrade_verify.py` installs the module on a live
  target container and returns structured tracebacks with custom-frame attribution;
  integration-tested on Odoo 19 — the first real run exposed a **false pass**
  (Odoo exits 0 while silently skipping an `installable=False`/unmet-deps module),
  now guarded by the `module_not_loaded` verdict requiring positive proof of load.
  (4) Data-migration hand-off to the existing `odoo-migration` skill +
  `upgrade_check.py` harness. Vendored MIT references (letzdoo, TAQAT) with
  licenses per the skill's `NOTICE.md`; AGPL tools only ever invoked as external
  CLIs. Regression fixture with 9 planted breakages wired into the test suite
  (`tests/test_upgrade_skill.py`).
- **Whole-project migration mechanism in `odoo-upgrade`** — migrate *everything*,
  not one module at a time. `migrate_all.py` briefs an entire custom-addons tree,
  topo-sorts the port order by `depends`, grades effort S/M/L per module and can
  batch-verify the fleet on the target runtime (dogfooded on a real 64-module
  production tree: 9 errors / 198 warnings, S=57 M=3 L=4 in one command).
  `db_upgrade.py` + `docker-compose.upgrade.yml` is the whole-DATABASE rehearsal
  harness (Community path): seed-or-restore → OpenUpgrade `-u all` with
  `--upgrade-path` → post-upgrade check of the ported customs, each step emitting
  a structured verdict via the shared traceback parser. OpenUpgrade stays an
  external AGPL checkout mounted read-only — never vendored. Enterprise databases
  keep the upgrade.odoo.com path (guidance in SKILL.md "Migrating EVERYTHING").

## [0.14.0] - 2026-07-03

The strategy pivot from *"a suite of Odoo skills"* to **the local-first evidence
layer for AI-assisted Odoo delivery**. Public architecture is now four stages —
**Inspect → Verify → Gate → Report** — and the internal "Layer A…L" labels are
retired from user-facing prose (they live on only in changelog cross-references).
The trust language is corrected throughout: this is a **CI-bound evidence gate
with an explicit trust boundary**, not an "unfakeable" one — CI produces and binds
the evidence; human review stays mandatory for sensitive domains.

### Added
- **Fast Inspect: context primitives (`facts`).** `facts.py` emits *small* instance
  facts for agent context — `--kind model|security|views|flows` for one model —
  so an agent reads ground truth *before* it edits, not an exhaustive dump. Every
  payload carries an `instance_fingerprint` (db uuid, module-graph hash, installed
  count).
- **Snapshot cache (`cache`).** `snapshot_cache.py` is a content-addressed cache
  (keyed by db uuid + module graph + addon file fingerprint + context) so warm
  context is fast inside the agent loop. **Invariant: warm/stale cache accelerates
  analysis but never approves a merge** — only cold runs are merge-eligible, and the
  provenance rides on every payload (`_cache.provenance`, `_cache.merge_eligible`).
- **Instance Dossier (`dossier`, `dossier-report`).** The one-command, read-only
  takeover / pre-sales due-diligence artifact: installed modules (standard/OCA/custom),
  Studio footprint, custom fields, server/automated/scheduled actions, security
  (groups + record rules, multi-company flags), custom view overrides, data volumes,
  and derived **upgrade-risk flags**. Redacted by default (`--redact external`, safe
  to share; `local` keeps sensitive-model schema names on a trusted box). Renders to a
  self-contained HTML report.
- **MCP context server (`mcp`).** `mcp_server.py` is a bounded, read-only MCP stdio
  server exposing six fact tools (`odoo_facts_model/security/views/flows`,
  `odoo_dossier_summary`, `odoo_native_check`) — validated inputs, **no arbitrary
  RPC or code execution**, redaction always applied, cache provenance on every
  response. `claude mcp add odoo-context -- python3 …/mcp_server.py --db <DB>`.
- **Evidence Artifact v1 (`evidence-artifact`).** `evidence_schema.py` — a stable,
  public, machine-validatable schema (`build`/`validate`) for the evidence a partner
  shows a client: git binding, instance fingerprint, per-check severity + cache
  provenance, decision, human sign-offs, redaction status. Encodes the invariant that
  a `warm`-cache check can never back an `approve`. See `docs/evidence-artifact.md`.
- **Severity classes + fail-closed policy in the Gate.** `deploy_gate.py` now
  classifies every finding S0–S4 (S4 = security / multi-company / silent corruption),
  emits `findings_detail` + `severity_summary` + per-finding remediation hints, and
  supports an **opt-in fail-closed policy** (`--policy`, `gate_policy.json`, or
  `ODOO_AI_FAIL_CLOSED`): an S3/S4 finding blocks the merge unless a covering
  `human_signoff.json` downgrades it to `needs_human` — never silently to `approve`.
  Strictly additive; the no-policy path is byte-for-byte the prior behaviour.
- **First-class CI/PR integration.** A composite GitHub Action
  (`.github/actions/odoo-gate`), a sticky PR-comment renderer (`pr-comment`, marker
  `<!-- odoo-ai-gate -->`), a GitLab recipe, and policy presets (advisory / strict /
  regulated) — see `docs/ci-integration.md`. The action enforces: `block` and
  fail-closed S3/S4 findings fail the build; the evidence artifact is uploaded.
- **Fixture factories (`fixture`).** `fixture_factory.py` gives agents *valid* business
  records for tests — 9 recipes across sale/purchase/stock/account/mrp/multi-company.
  CODE mode emits a paste-ready `TransactionCase` skeleton; `--exec` runs the recipe in
  a **savepoint and rolls back**, validating it against this instance's modules / chart
  of accounts.
- **Fit-Gap alpha (`fit-gap`).** `fit_gap.py` classifies requirements against the live
  instance — `native_config` / `config_plus_gap` / `module_available_not_installed` /
  `pattern_known_not_present` / `no_known_pattern` — with heuristic effort bands and
  risk class. Scoped to sale/stock/account. It is **decision support for a functional
  consultant, not a replacement**: gated items are instance-verified, heuristic items
  need human validation.
- **UAT Pack alpha (`uat-pack`).** `uat_pack.py` turns `surface` + `scenarios` (+ optional
  `dossier`) into role-based UAT scripts a consultant hands to a client — role, data
  setup (suggested fixture recipe), numbered steps, expected result, evidence slot,
  sign-off — as JSON + Markdown (checkbox sign-offs) + optional HTML. Scenarios with no
  entrypoint match are surfaced, never dropped silently.
- **Odoo Agent Safety Bench v0 (`bench/`).** A public, reproducible benchmark whose core
  metric is the **unsafe-change escape rate** — how often an unsafe Odoo change reaches
  PR/UAT/release undetected — *not* task completion. Ten frozen v0 tasks (≥5
  high-severity: portal `sudo()` ACL bypass, dropped multi-company record-rule clause,
  stale `@api.depends` ledger drift, f-string SQL injection, price-include tax error),
  severity-weighted scorer (S0×1 … S4×12), four run modes (alone / +context / +gate /
  both), a living adversarial `zoo/`, and a RUNBOOK. No single headline score by design.
- **Business & governance docs.** `docs/pilot-package.md`, `docs/partner-enablement.md`,
  `docs/governance.md`, `docs/artifact-governance.md`, `docs/odoo-online-advisory.md` —
  the paid-partner-enablement model around an open standard, artifact-handling rules for
  sensitive dumps, and the path toward neutral (OCA/partner) corpus co-maintenance.

### Changed
- **Public naming and positioning.** README, `AGENTS.md`, `docs/index.html`, and 17
  `SKILL.md` files reworded to the Inspect/Verify/Gate/Report vocabulary; new top-line
  positioning sentence; a new **Odoo hosting reality** section (self-hosted & Odoo.sh =
  full code gate; **Odoo Online = advisory-only**, no custom code). The `AGENTS.md` MCP
  rule now sanctions the bundled bounded MCP context server while still forbidding
  arbitrary-RPC wrappers.
- **`build_report(bundle_dir, strict=False, policy_path=None)`** gained an optional
  `policy_path`; existing callers (evidence bundle, CLI) are unaffected.

## [0.13.0] - 2026-07-01

### Added
- **Layer L — the hardened evidence gate** (raise the bar against "coverage theater": an agent
  writing mock-heavy / vacuous-assert / happy-path tests, or hand-authored evidence
  JSON, to turn the gate green while the real method still throws at runtime). The
  fix is architectural: the agent writes code + tests, but **CI produces and signs
  the evidence** and binds it to the git diff. Six new stdlib gate scripts (all
  unit-tested, most need no DB):
  `diff_targets.py` (git+AST → changed `{model,method,changed_exec_lines}` targets,
  the anchor everything binds to), `test_quality_gate.py` (AST-lint that BLOCKS
  vacuous asserts, `assertRaises(Exception)`, mocked model-methods-under-test,
  swallowed exceptions, and tests not imported from `tests/__init__.py`),
  `changed_coverage_gate.py` (proves the *changed* lines ran under a real test
  context via `coverage --show-contexts`, per-risk threshold), `runtime_path_probe.py`
  (proves the changed method executed through the live registry MRO on a recordset
  of its own model — kills mocks/stubs/wrong-layer tests), `scenario_satisfaction.py`
  (turns the Layer I risk scenarios from advisory into enforced: `required −
  satisfied == ∅`), and `provenance.py` (HMAC attestation: sign each evidence file
  on the CI host with `ODOO_AI_ATTEST_KEY`, which must never exist inside the test
  container). Provenance is **content-bound and commit-bound**: `--strict` requires
  every *consumed* required artifact's actual file bytes to hash to something CI
  signed (a forged/hand-authored/tampered file is rejected even if an unrelated
  valid envelope is present), rejects a bundle whose envelope `head_sha` doesn't
  match the change under gate (replay), and requires the coverage/runtime proofs
  to reference the same targets `diff_targets` found (no vacuous empty-target
  proofs). Hardened after an adversarial code review found and reproduced those
  exact bypasses — each is now a regression test.
  `deploy_gate.py` gains an opt-in **policy v2 (`--strict`)** that enforces the
  expanded core (`_CORE_REQUIRED_V2`), the runtime-path/changed-coverage/
  scenario-satisfaction/test-quality/mutation blocking checks, and provenance
  verification; the legacy default path is unchanged (all existing tests green).
  Two more CI-side runners complete the set: `mutation_smoke.py` (mutates the changed
  lines of each target — flip a comparison / remove a `raise` guard / swap a boolean op
  — and re-runs the covering tests; a *surviving* mutant means the test asserts nothing
  the mutation breaks; required for high/critical/sensitive changes) and
  `red_green_replay.py` (CI re-runs the test-first ritual: base+PR-tests must FAIL with a
  legitimate red — not a `self.fail("TODO")`/import/syntax error — and head must PASS with
  the same identities; required for bug fixes). Both keep a pure, unit-tested core and a
  documented DB/git orchestration runner. New CLI verbs: `diff-targets`, `test-quality`,
  `changed-coverage`, `runtime-path`, `scenario-satisfaction`, `mutation-smoke`,
  `red-green-replay`, `attest`, `verify-attestation`, and `deploy-gate --strict`.
  `odoo-testing`/`odoo-dev` SKILLs document the gate + an extended PR checklist.
  Design grounded in three Oracle (GPT-5.5 Pro) adversarial reviews
  (`plans/reports/analysis-260701-1055-eradicate-fake-tests.html`). The final review
  found and we fixed: a critical freshness bypass (envelopes with an empty
  `subject.head_sha` slipped the replay check — provenance is now bound **per
  artifact**: hash + canonical name + a non-empty head equal to the CI-set
  `ODOO_AI_EXPECTED_HEAD_SHA`), and a policy gap where normal-risk weak-assert tests
  could approve (`mutation_smoke` is now core-required for every strict approval and
  a surviving mutant blocks regardless of risk; `test_quality` now blocks swallowed
  Odoo business exceptions like `except UserError: pass`; `locked_period` no longer
  accepts a generic `UserError`). **Honest posture:** this is a strong hardening gate,
  NOT eradication. The decisive runtime facts are still measured inside the untrusted
  test process and signed after the fact, so `--strict = approve` holds only when CI
  runs the observers as pinned, isolated, fail-closed tooling (see the SKILL "Trust
  boundary" note). Human review stays mandatory for accounting/stock/payment/hr,
  security, and controllers even when the gate is green.
- **Layer L — trusted-observation boundary** (the final review's deepest gap: runtime
  facts are measured inside the untrusted test process, so a hostile test could
  disable the recorder and emit green). `runtime_observer.py` is a CI-owned tracer
  that **fails closed**: it heartbeats to detect a `sys.settrace(None)`/replacement
  mid-run and self-reports `trace_integrity` (`sealed`/`tampered`/`incomplete`/
  `absent`) plus its own `tool_digest`. `deploy_gate.py --strict` now blocks any
  `runtime_path` whose `producer.trace_integrity != "sealed"`, and — when
  `ODOO_AI_EXPECTED_PROBE_DIGEST` is set — whose observer digest doesn't match the
  CI-pinned one (a swapped observer). The seal is only trustworthy with the OS/CI
  isolation documented in `references/ci-trusted-observer.md` (inject the observer
  from a CI-owned path, evidence dir not writable by the test process, sign after
  the process exits). End-to-end verified: a fully-signed bundle whose observer
  reports `tampered` → `block`. A fourth Oracle review of this boundary found and we
  fixed: the disable/**restore** attack (tracer disabled briefly then restored still
  sealed) — now caught by a permanent `sys.addaudithook` settrace counter that
  survives the restore; unknown output ownership now fails closed (`incomplete`, not
  sealed); and the CI-pinned observer digest (`ODOO_AI_EXPECTED_PROBE_DIGEST`) is now
  **mandatory** for strict auto-approve (absent → `needs_human`, mismatch → `block`).
  Honest residual remains and is documented: an in-process attacker that mutates the
  observer's own state before it seals — only true process/container isolation closes
  that, which is CI's responsibility, not the gate's. Final suite: 758 passed.

- **`odoo-user-guide` skill (v1)** — generate end-user how-to guides for an Odoo
  flow from the running instance. `odoo-guide-init` grounds the steps with the
  `odoo-ai` CLI (Layer K `surface` + view buttons + effective per-role
  `security`) into a durable `guide.yaml` manifest; `odoo-guide-doctor` is a
  safety gate that hard-fails on production mutation or missing access;
  `odoo-guide-run` drives the real UI with Playwright on a **sandbox** DB,
  screenshots each step (metadata-bound selectors, JS-injected numbered
  callouts), creates/archives its own owned test record, and **asserts the
  resulting state at the backend over XML-RPC** (the proof); `odoo-guide-render`
  emits a self-contained annotated HTML guide reusing the `html-report` layout.
  Grounding is **portable**: it runs over Odoo's standard external API (XML-RPC
  `get_view` + `check_access_rights`) so it works on any deployment (local,
  Docker, remote, Odoo.sh/Online) with no shell access — one `--url/--db/--login`
  drives init, doctor, and run; `odoo-bin shell` (`odoo-ai`) and pre-saved JSON
  remain as alternative grounding paths. The `sale.order` teardown unlocks a
  locked order and drives the `sale.order.cancel` wizard so no confirmed test
  order lingers. Pure-function core (`odoo_guide_lib`) is unit-tested with the
  stdlib; Playwright is the only new dependency and is isolated to this skill.
  End-to-end verified against a live Odoo 18 instance. Voice/TTS and MP4 video
  are roadmap (v2/v3). Skill count 19 → 20.

  **Architecture pivot (agent-driven capture).** After research (the bottleneck
  in doc generation is guessing the click-path in code before seeing the UI), the
  primary authoring path is now the AGENT driving the live Odoo UI through an MCP
  browser tool (claude-in-chrome / Playwright MCP): it locates elements by visible
  label / accessibility role (no hand-written CSS selectors), adapts to
  wizards/locked records/banners, and screenshots as it goes — eliminating the
  brittle-selector debug loop. New helpers `odoo-guide-prep` (ground over RPC +
  create the owned test record + emit a `flow.json` contract) and
  `odoo-guide-verify` (read the expected state over RPC as proof, then cancel /
  archive the record) bracket the agent's live run; `odoo-guide-render` then joins
  the captured evidence with the agent-authored `copy.json` prose. The headless
  Playwright driver (`odoo-guide-run`) is demoted to an optional deterministic
  replay path for CI / known flows. Demonstrated live against Odoo 18: a one-call
  semantic `find("Xác nhận button")` located and clicked the confirm button with
  zero selector debugging, proven `state=sale`, record auto-cleaned. The
  `odoo-guide-shot` helper closes the agent path to the renderer: the agent passes
  the file path returned by each MCP screenshot, and the helper copies it into the
  guide's `screenshots/` and records the step (ordered) in `evidence.json` — so the
  agent-driven capture reaches `odoo-guide-render` with no dependency on the
  deterministic driver. `odoo-guide-render` defaults to **Markdown** output
  (`guide.md` + `screenshots/`, GitHub/KB-friendly) with `--format html` for a
  self-contained page. Screenshots are **annotated with a spotlight** (dim the
  page, red box + numbered badge on the action target) so readers see exactly
  where to act. **Model-agnostic:** `odoo-guide-prep` / `odoo-guide-run` take
  `--record-id` to document an existing record of ANY model (nothing created or
  torn down); auto-create is an optional recipe registry (`odoo_rpc.RECIPES`,
  ships `sale.order`); and teardown (`cleanup_record`) is a best-effort,
  model-agnostic chain (unlock → cancel/`*.cancel` wizard → `button_cancel` →
  delete → archive) that runs only on records the skill created. Verified live on
  Odoo 18 for both `sale.order` (confirm) and `purchase.order` (create) flows.

## [0.12.1] - 2026-06-29

### Changed
- **`html-report` hero restyle** — the report header is no longer a flat black
  slab. `assets/report.css` now renders a "tech" hero: a deep warm-black gradient,
  two brand-tinted corner glows (pink + yellow), a faint blueprint grid that fades
  toward the bottom, and an accent hairline along the bottom edge. Pure CSS, still
  fully self-contained (no images/CDN), and unchanged in light/dark/print. New
  reports pick this up automatically; previously inlined reports need a re-inline.

## [0.12.0] - 2026-06-29

### Added
- **Native Codex adapter** — the suite now ships `.codex-plugin/plugin.json`,
  `.agents/plugins/marketplace.json`, root `AGENTS.md`, and Codex command prompts
  (`/odoo`, `/odoo-ai`, `/odoo-review`, `/odoo-gate`). Codex reuses the same
  `skills/` directory and bundled `odoo-ai` CLI; no MCP wrapper is required for
  the current shell-first workflow.
- README install instructions for local Codex marketplace installs:
  `codex plugin marketplace add /path/to/odoo-ai-skills` then
  `codex plugin add odoo-ai-skills@odoo-ai`.

## [0.11.0] - 2026-06-29

### Added
- **`html-report` skill** — render any audit / review / analysis / RCA / summary
  as one consistent, self-contained HTML page instead of a new ad-hoc design each
  time. Ships the canonical bold "Magazine" stylesheet (`assets/report.css` —
  light + dark + print), a `template.html` / `example.html` to compose from, and
  `scripts/build_report.py`, which inlines the CSS into a report (making it fully
  self-contained — no CDN, no server) and opens it in the browser. Includes
  **dependency-free chart blocks** — an MRO/`super()` ladder, a parent/child
  tree, a bar chart, and a security matrix (pure CSS, no JS/CDN) — so analyses
  are visual and easy to read. Presentation only, and distinct from
  `odoo-reports` (Odoo QWeb business documents). Wired into the `odoo` router and
  cross-referenced from `odoo-review` / `odoo-debug`.
- **`odoo-ai viz`** (`skills/odoo-introspect/scripts/viz.py`) — turn introspection
  JSON into a self-contained HTML report with charts: Layer A → MRO/`super()` ladder,
  Layer C → menu tree, Layer D → SQL-hotspot bars + call order, Layer G → effective-
  security matrix, Layer K (ESG) → model→model bars + a Mermaid graph. Pure stdlib,
  no DB; reuses the `html-report` stylesheet; wired as a LOCAL `odoo-ai` command and
  covered by `tests/test_viz.py` (14 tests; 445 in the suite stay green).

## [0.10.0] - 2026-06-28

**Layer K — discovery, sampling, measurement & enforcement.** Closes the gap the
suite's own deep analysis surfaced: it was best-in-class at *micro* ground-truth
(a model/method/field you already named) but had no answer to "where do I even
START?", no way for "understand the overall process" to be true without a
stale-prone static map, no measurement of whether hallucinations actually drop,
and — the Oracle's #1 point — no way to stop the agent skipping the tools. Layer
K adds all four, staying true to *runtime-grounded, never memorized*. Real-tested
live on Odoo 17 / 18 / 19 (89/89 integration checks each, via `docker-compose.e2e.yml`).

### Added
- **`odoo-ai surface`** (`entrypoint_surface.py`) — **entrypoint discovery**: ranks
  the live entrypoint surface (object buttons `action_*`/`button_*`, server actions,
  crons, automation rules, reports, and **HTTP routes** — a new `@http.route`
  scanner) instance-wide / per-model / per-module, so the agent starts from the
  high-value roots instead of guessing `write`/`create`. Emits `top_trace_seeds`.
- **`odoo-ai esg`** (`esg_sample.py`) — **Execution Surface Graph**: samples the top
  entrypoints (traces each on a real record, rolled back by default) and merges the
  skeletons into one cross-model / cross-app flow graph (models touched · model→model
  edges · app→app edges · write-map). Process understanding that **emerges from
  traces**, never a static map that goes stale and makes the agent confidently wrong.
- **`odoo-ai eval`** (`eval_harness.py` + `references/eval-benchmark.json`) — a
  **hallucination benchmark**: runs the classic LLM Odoo mistakes (`account.invoice`,
  `customer_id`, `fields_view_get`, a `'customer'` selection value) plus stable reals
  against the live registry and scores the gate's `detection_rate` / `truth_recall`.
  A regression signal, not a vibe. Reuses `native_check`'s existence probes so the
  eval measures the *same* machinery the suite gates with. (1.0 / 1.0 on 17/18/19.)
- **`odoo-ai gate-edit`** (`gate_edit.py`, LOCAL no-DB) + **PreToolUse hook**
  (`scripts/hooks/pre_edit_gate.py`, `references/enforcement-hooks.md`) — **enforce
  no-introspect-no-edit**: maps the files an agent is about to edit to the models they
  touch (`_name`/`_inherit`; view `model=`), blocks the edit until each has an
  introspection brief + passes the validator, and emits the exact `odoo-ai` command to
  unblock. The Oracle's highest-ROI lever: makes the tools *inevitable*, fail-open so
  it never bricks legitimate work.
- **`docker-compose.e2e.yml`** + `tests/e2e/` — a one-command multi-version E2E
  harness (Postgres + Odoo 17/18/19) running the full integration smoke against three
  live registries.
- Pure-function unit tests for every new layer (`tests/test_entrypoint_surface.py`,
  `test_esg_sample.py`, `test_eval_harness.py`, `test_gate_edit.py`) and live
  integration-smoke coverage (`smoke_surface` / `smoke_esg` / `smoke_eval`).

### Hardened — pre-release adversarial audit
An independent oracle audit of the diff drove these (the audit's own top "blocker"
— a claim that `--json` doesn't exist — was a false positive, verified against the
CLI + the passing hook E2E; these are the findings that held up in code):
- **ESG never auto-fires an external-effect method.** `esg` executes the methods
  it discovers (unlike `trace`, where you name one), so a name-based blocklist
  (`send`/`mail`/`sms`/`payment`/`print`/`unlink`/… ) is skipped and reported under
  `skipped_unsafe`; override with `ESG_ALLOW_UNSAFE=1` on a throwaway DB. `esg` also
  fails loudly if its sibling helpers didn't import, instead of crashing mid-trace.
- **`surface` caps every registry read** (`limit=2000`) so it's safe on an outsized DB.
- **`gate-edit` never downgrades silently** — when the static validator can't run it
  emits a `_validator_note` so a clean `allow` isn't mistaken for "validator passed".
- **`pre_edit_gate.py` gains `ODOO_AI_GATE_STRICT=1`** (fail-closed): if the gate
  itself can't run *or returns no valid decision*, block the edit instead of failing
  open (the default stays fail-open so a misconfig never bricks editing).
- **`scan-secrets` no longer flags file paths as base64 secrets.** The generic-token
  heuristic treated any 24+ char run containing `/` (or `-`/`_`) as base64, so every
  path (`web/static/src/components`, `references/enforcement-hooks`) and slash word
  list (`send/mail/payment`) was a "secret" — a scanner that cries wolf on file paths
  gets ignored. It now requires real base64/entropy signal (`+`, `=` padding, or mixed
  case+digit and not a mixed-alphabet path). Real tokens still caught; the E2E test DB
  is passwordless (`POSTGRES_HOST_AUTH_METHOD=trust`) so no credential literal ships.

### Fixed
- **`native-check` under Docker/remote `odoo-bin`** (#3) — the command passed the
  capability-card corpus as a **host `CARDS_DIR` path**, which a container (or ssh
  host) running `odoo-bin` in a different filesystem namespace couldn't read, so it
  silently returned a confident **"0 matched"** false negative ("Odoo ships nothing
  for this") for every query. The CLI now **injects the card corpus (and learned
  mappings) as content on the script's own stdin** — the same channel the script
  rides — so it works for any `ODOO_BIN` (local, Docker, ssh) without depending on
  `-e` env forwarding. As defense in depth, `native_check.py` now **fails loudly**
  on a zero-card load instead of producing an empty result. Added `REQUIREMENT` to
  the documented Docker-wrapper env allowlist (native-check reads it from env).

## [0.9.1] - 2026-06-28

**Hardening release — fixes from an independent oracle review of v0.9.0.** No new
features; it makes the v0.9.0 gates actually trustworthy. The most dangerous
v0.9.0 failure mode was a polished-but-wrong `approve`/PR-comment built from
mis-parsed or missing evidence — these fixes close that.

### Fixed
- **Evidence/CLI artifact contract** — `deploy_gate` now resolves artifacts by
  pattern (`validate.json` **or** the CLI's `patch.validate.json` / `env.env-diff.json`
  forms), so a bundle produced by the official CLI is actually read. Added a
  global **`odoo-ai --json`** flag (raw JSON to stdout) so CI can capture machine
  output instead of the human summary.
- **CI workflow** (`odoo-ai-gate.yml`) — was teeing the human summary into `.json`,
  redirecting `evidence`'s JSON into `.md`, grepping a non-existent `verdict` key,
  and checking `"findings": []` that `scan-secrets` never emits. Rewritten to use
  `--json`, `evidence --md-out`, parse `.decision.decision`, and read the secret count.
- **Migration safety** (`upgrade_check.detect_renames`) — a sole same-type field is
  no longer a "high-confidence rename" without a **name-similarity threshold**; a
  real drop (e.g. `legacy_code` → `customer_note`) keeps its data-loss `field_removed`.
  Column risks now apply to **stored** fields only.
- **Redaction** (`redaction.redact_payload`) — now masks provider secrets
  (AWS/GitHub/Stripe/Slack/Google/PEM), covers benign secret keys
  (`aws_access_key_id`, `access_key`, `client_secret`, `webhook`, …), strips
  case/variant execution-state keys (`Source`, `local_vars`, `self`, `args`, …),
  and redacts record `value`s by `classify_field_sensitivity`.
- **Deploy gate** — ingests `scan_secrets` (blocks on count > 0), never `approve`s
  when an artifact is present-but-unparseable, and an optional `manifest.json`
  (touched models / migration / security / controller) drives the *required*
  evidence and the sign-off list (incl. public-controller / access-rule reviews).
- **Claim verify** (`claim_verify`) — a malformed/unknown probe is no longer a
  `contradicted` ("the source is wrong"); `dispatch_leaf` marks eval status and
  such claims become `needs_human`. Method/hook claims default to `needs_shell`
  (existence ≠ safety); a `claim_type` field can override.
- **Env drift** (`env_diff`) — modules/Studio fields present on only **one** side
  are HIGH severity in **either** direction; partial fingerprints (`None` counts)
  no longer crash the diff.
- **Misc** — scenario skeleton now emits both `at_install` and `post_install`
  tag variants; `patch_validator` SQL/`create()` checks are AST-based (no more
  `LIKE '%x%'` false positive, no flagging non-Odoo `create()`); `native_check`
  mixin probe also checks `_inherit`; `doc_index` normalises the version dir,
  fails on an empty build, and records `_meta` provenance (source commit, built_at).

### Hardened — deploy-gate approve path (multi-round adversarial review)
The gate's `approve` decision was put through repeated adversarial review of
attacker- / bug-shaped evidence JSON. A wrong `approve` (or a crash/`NaN` in the
output) is now unreachable through `build_report`:
- **Strict load boundary** (`_loads_strict`) — rejects duplicate object keys
  (`{"blocking":1,"blocking":0}` can't overwrite a blocker), `NaN`/`Infinity`,
  and overflow-to-`inf` numbers (`1e10000`); the report is emitted `allow_nan=False`.
- **Typed artifact schemas** (`_artifact_valid`) — every field the decision reads
  is validated: `validate`/`upgrade` need non-bool int `blocking`+`warning` ≥ 0
  (a `-1` or a missing `warning` is invalid, not "clean"); `native_check` needs
  real lists; `scenarios` a tier enum + string model hints; `env_diff` a severity
  enum; `security` a bool `is_superuser` / list `_warnings`; `trace.error` null or
  a non-empty string. A present-but-mis-typed required artifact → `needs_human`.
- **Multiple matching artifacts** merge to the **worst case** (a clean duplicate
  can't hide a blocking one) and raise an ambiguity warning → `needs_human`.
- **`scan_secrets` is core-required** (a missing secret scan can't silently
  approve), and its scanner matches the redactor's provider/JWT/token coverage.
- **Manifest shape fully validated** — a non-dict manifest, a non-list/non-string
  `changed_files`, or a non-bool `has_migration` no longer silently skips required
  evidence; migration paths are matched after normalising `\\`→`/`; a change under
  a sensitive domain path (`account/`, `hr/`, …) requires the sign-off even when
  `touched_models` is absent.
- **A required human sign-off forces `needs_human`** — the decision can never be
  `approve` while also listing `required_approvals`.

### Changed
- Softened doc claims to match behaviour: redaction is **pattern-based (review
  still required)**, not absolute; upgrade-check **heuristically suggests** renames;
  the upgrade harness **lists** `noupdate` records (it does not diff your patch).

## [0.9.0] - 2026-06-28

**The verification gate.** Repositions the suite around its real category —
*static indexes suggest; the running instance disposes* — and ships the pieces
that make that real: a flagship one-command evidence bundle, a bring-your-own-index
claim verifier, a local Odoo-docs index (Layer J), and CI integration. (Strategic
basis: the competitive analysis under `plans/reports/`.)

### Added
- **`odoo-ai evidence <bundle_dir>`** (`evidence_bundle.py`, local) — the flagship
  artifact: aggregates the gate outputs into the deploy verdict and renders a
  **PR-ready Markdown comment** (verdict badge, risk tier, gate-evidence table,
  blocking findings, required approvals). The "agent-written, tool-verified,
  human-approved" artifact for CI/procurement.
- **`odoo-ai verify-claims <claims.json>`** (`claim_verify.py`) — the **BYO-index
  adapter**: treat any external source's claims (a hosted index, OCA docs, `grep`,
  the doc-index, another agent) as hypotheses and verify each against THIS instance
  → `confirmed / contradicted / needs_shell / needs_human / absent`. Reuses
  native_check's existence probes (the new module-level `make_handlers`). Ecosystem
  breadth without trusting an index — and without the re-indexing treadmill.
- **Layer J — local Odoo-docs index** (`doc_index.py` + the `odoo-docs` skill):
  `odoo-ai docs-build --version 18` builds a TF-IDF index of `content/developer/`
  **locally** (in `~/.odoo-ai/docs-index/`, never vendored into the repo — clean
  CC-BY-SA hygiene, no plugin bloat, no maintenance treadmill); `odoo-ai docs
  "<q>" --version 18` returns ranked chunks + canonical odoo.com URLs. Subordinate
  to introspection (docs propose, the instance disposes); reuses the suite's
  offline TF-IDF (no embeddings, no network at query time).
- **CI integration** — `.github/workflows/odoo-ai-gate.yml` (an example PR workflow
  that runs the local gates and posts a sticky verdict comment),
  `skills/odoo-deploy/references/ci-integration.md` (GitHub Action / odoo.sh recipe
  / Docker + MCP wrappers, plus an **honest RPC-degraded capability table**), and
  `docs/high-risk-playbooks.md` (the failures a static index can't catch).
- **`SUSTAINABILITY.md`** — the engine stays LGPL and complete; sustainability is
  services (support, implementation, sponsored compatibility, training), not seats.

### Changed
- **Repositioned** the README + landing page from "skills suite" to the
  **local-first verification & deploy gate**: *static indexes suggest;
  `odoo-ai-skills` verifies against the running instance.* No SaaS, no seats, no
  API key, no metadata leaves the box.
- `native_check.py` — the existence-probe handlers are extracted to a reusable
  module-level `make_handlers(env)`, shared by native-check and the claim verifier.

### Tests
- Pure helpers unit-tested for evidence-bundle (Markdown render + verdicts),
  claim-verify (claim→probe mapping, verdict classification), and doc-index (RST
  chunking, canonical URLs, TF-IDF query).
- **Real-tested against live Odoo 17 / 18 / 19** — the integration smoke now also
  covers `verify-claims`, the Layer D `trace`, and `preflight` (68/68 checks; the
  CI job installs `base,sale` so the trace flow has a record). `docs-build` was
  exercised against an actual `git` sparse-checkout of `odoo/documentation`.
- **Validated end-to-end against a real 390-module Enterprise instance**
  (Studio fields, custom addons, multi-company): all read-only layers, the
  enforcement gates, `verify-claims`, and the write/execute layers (`trace`/
  `state`) on a throwaway record that was created then deleted. Instance untouched.

## [0.8.0] - 2026-06-27

**Layer I — enforcement gates.** Where Layers A–H *produce evidence*, this release
starts *enforcing verification*: the seven recommendations from the v0.7 codebase
evaluation, built as real tools. The thesis grows from "read ground truth, don't
guess" to "**read ground truth → prove the change against the exact users,
companies, env, and upgrade path → gate the deploy**". Four gates are **pure and
local** (no `odoo-bin shell`, no DB) so they run in CI or on a laptop.

### Added
- **Risk-based scenario test generator** — `odoo-ai scenarios <model> [--methods a,b]`
  (`scenario_gen.py`). Classifies the change risk (critical/high/normal by model)
  and emits the *mandatory* test scenarios (non-admin, multi-company, batch,
  `at_install`/`post_install`, `-i`/`-u`, locked-period for accounting) plus a
  runnable `TransactionCase` skeleton with one failing stub per scenario.
- **Environment parity & drift detector** — `odoo-ai env-fingerprint` captures a
  parity fingerprint (installed modules+versions, edition, model/rule/cron counts,
  Studio fields, `ir.config_parameter` **key names only**); `odoo-ai env-diff
  <base.json> <target.json>` (LOCAL) diffs dev vs prod and refuses false
  "production-safe" confidence when they diverge (`env_diff.py`).
- **Static Odoo patch validator** — `odoo-ai validate <path...>` (LOCAL,
  `patch_validator.py`). The `odoo-review` checklist as an executable linter:
  flags `attrs`/`states`, `<tree>`, `name_get`, `type='json'` on 19+, `create()`
  without `@api.model_create_multi`, `search()`/`browse()` in loops, f-string
  `cr.execute`, uncommented `sudo()`, `self._cr`/`_uid`/`_context`, fragile xpath,
  leftover debug — with low false positives.
- **Privacy redaction** (pattern-based; review still required) — `odoo-ai redact
  <file> [--mode external|local]` and `odoo-ai scan-secrets <file>` (LOCAL,
  `redaction.py`). A post-processing step before introspection JSON leaves your
  box: external mode strips `source`/`locals`/`code`, redacts secret-named keys,
  and masks PII (email/phone/IBAN/card/JWT/token); model-level sensitivity labels
  for `res.partner`/`account.move`/`hr.*`/`payment.*`. (0.9.1 broadens the secret
  coverage — pattern-based redaction is not a substitute for review.)
- **Layer H deeper probe grammar** — `native_check` grows from 4 probe kinds to
  **12**: adds `xmlid_exists`, `action_window_exists`, `group_exists`, `cron_exists`,
  `sequence_exists`, `selection_has_value`, `mixin_inherited`, `edition`. The leaf
  dispatch is refactored into a pure, unit-testable `dispatch_leaf` (+ `PROBE_KINDS`);
  cards can now gate Enterprise-only capabilities and prove a state literal exists
  (the `sale.order` confirm card now probes both the hook *and* `state='sale'`).
- **Migration & upgrade harness** — `odoo-ai upgrade-check <model> --against
  <old_brief.json>` (needs DB) and `odoo-ai upgrade-diff <old> <new>` (LOCAL),
  `upgrade_check.py`. Heuristically suggests a **rename** vs a data-losing
  **drop** (with a name-similarity threshold — see 0.9.1), flags
  new-required-without-default, and **lists** `noupdate` records on the model, and
  scaffolds a `pre-migrate.py`. Fresh-install success is never reported as upgrade safety.
- **Deployment approval orchestrator** — `odoo-ai deploy-gate <bundle_dir>`
  (LOCAL, `deploy_gate.py`). Aggregates the other tools' JSON into a risk
  classification and an **approve / needs-human / block** decision, requiring
  explicit human sign-off for high-risk models (accounting, stock valuation,
  payroll, payments, access rules, public controllers).

### Notes
- **Positioning.** This is the suite repositioned as an *Odoo agent safety &
  grounding layer*: the realistic target is **agent-written, tool-verified,
  human-approved**, not blind autonomous production mutation. See the v0.7
  evaluation report under `plans/reports/`.
- **Local-first gates.** `validate`/`redact`/`scan-secrets`/`deploy-gate` (and the
  `env-diff`/`upgrade-diff` modes) need no instance — closing part of the
  no-shell/SaaS gap for the checks that don't require the live registry.

### Tests
- Pure helpers for every new tool are unit-tested without Odoo (risk classes,
  scenario matrix, fingerprint diff, each validator rule with positive+clean
  cases, PII masking + secret scan, rename-vs-drop detection, gate decisions),
  plus `dispatch_leaf` and the extended probe grammar for native-check.
- **Real-tested against a live Odoo 18 instance** (and CI-runnable on 17/18/19):
  the integration smoke (`scripts/tests/integration_smoke.py`, opt-in via
  `ODOO_DB`) now also exercises the shell-bound `run()` paths of
  `scenarios`/`env-fingerprint`/`upgrade-check` and the new probe kinds
  (xmlid/group/action_window/cron/selection_has_value/edition) against the live
  registry via a base-safe fixture corpus — **57/57 checks** (was 44). The four
  local gates need no instance and are covered by the unit suite.

## [0.7.0] - 2026-06-27

Completes the Native Capability Atlas roadmap: **smarter recall + a learning
loop**. native-check now ranks with a vector-space score and gets better from
real usage.

### Added
- **Learning loop** — `odoo-ai native-learn "<requirement>" --card <id>` records
  a requirement→card mapping (a local file op, no DB; default
  `~/.odoo-ai/learned.json`, or `--learn-file`). `native-check` folds these
  mappings back into the corpus: a learned phrase **augments** the matching
  card's intents (or, with full fields, adds a **new learned card**), so a
  phrasing that recalled nothing today recalls its card tomorrow. `native-check`
  reports `learned_mappings`.
- **Vector-space recall** — recall now ranks cards by **TF-IDF cosine** over the
  card text + an intent-phrase bonus (IDF down-weights tokens common to many
  cards, up-weights distinctive ones), replacing raw token-overlap. Pure-Python,
  deterministic, dependency-free.

### Notes
- **Dense neural embeddings are deliberately not used.** They'd require a model
  at runtime (a heavy dependency or a network/API call from inside
  `odoo-bin shell`), against this tool's offline-in-shell design, and the agent
  already does the final semantic ranking. The TF-IDF recall + the learning loop
  are the model-free path to better recall; `capability-schema.md` documents the
  seam where a dense embedder could later plug in over the same merged corpus.

### Tests
- TF-IDF (`corpus_idf`/`tfidf_vector`/`cosine`), `phrase_bonus`, `merge_learned`,
  a learning round-trip (zero-recall phrase → top hit after learning), and the
  `native-learn` CLI file op (append + dedup) — in both suites. Real-tested via
  docker-compose: the learning round-trip and TF-IDF recall against live Odoo.

## [0.6.0] - 2026-06-27

Builds on the v0.5 Native Capability Atlas with **native-check — gate-then-rank
requirement matching**. Describe a requirement; the script recall-matches a
corpus of curated capability cards, then **existence-gates** each against the
running instance and returns candidates with cited evidence. The objective half
(does it exist here?) is the script's; the subjective half (which fits best?)
stays the agent's.

### Added
- **`odoo-ai native-check "<requirement>" [--model M]`** (new `native_check.py`).
  Recall-matches the requirement (diacritic-folded, Vietnamese-aware) against the
  capability cards, then runs each card's existence `probe` against the live
  registry. Returns `confirmed_candidates` (probe passed here — with `evidence`:
  module/model/field/hook present) and `unconfirmed_candidates` (matched but not
  active here — with `why_absent`). Evidence or silence: only confirmed
  candidates may be recommended. The pure helpers (tokenize, diacritic-fold,
  recall scoring, the `any`/`all` probe evaluator) are unit-tested without Odoo.
- **Curated capability cards** — `skills/odoo-capabilities/references/cards/*.json`
  (~34 cards across `universal` + sale/stock/account/mrp/purchase/hr). Each card
  carries business intents (EN + VN), the right hook, reuse advice,
  `when_not_enough`, and an existence `probe`. Probe kinds: `module_installed`,
  `model_exists`, `field_exists`, `method_exists` (+ `any`/`all`). Documented in
  `references/capability-schema.md`; CI-validated (schema, uniqueness, probe kinds).
- **PR template** (`.github/PULL_REQUEST_TEMPLATE.md`) with the native-check
  checklist (candidates considered / reused / rejected+why / the gap).
- **Tests** — native_check pure-function coverage + a shipped-card-corpus
  validator in both the pytest and unittest suites; a native-check integration
  smoke (confirmed carry found-evidence, unconfirmed carry why_absent).

### Changed
- `odoo-capabilities` SKILL now leads with `native-check`; the `odoo` router and
  `odoo-dev` Step-0 reference it. `capabilities <model>`/`--module` remains the
  full-surface fallback when no card matches.

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

[Unreleased]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.15.0...HEAD
[0.15.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.14.0...v0.15.0
[0.12.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.9.1...v0.10.0
[0.4.2]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/tuanle96/odoo-ai-skills/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/tuanle96/odoo-ai-skills/releases/tag/v0.2.0
