# odoo-upgrade — Agent Skill for Odoo version migration (18 → 19)

An [Agent Skill](https://agentskills.io) that lets AI coding agents (Claude
Code, Codex, Gemini CLI, OpenCode...) migrate Odoo custom modules **and their
data** between major versions — with runtime verification, not just pattern
rewriting. Part of the `odoo-ai-skills` family; companion to
[`mcp-odoo`](https://github.com/tuanle96/mcp-odoo).

## Why another migration tool

Existing tooling is either deterministic rewriters (official
`odoo-bin upgrade_code`, OCA `odoo-module-migrate`) or static pattern
libraries for AI agents (letzdoo, TAQAT — both excellent, both vendored here
with thanks). None of them:

1. **generate** their breaking-change knowledge from the actual source diff
   (hand-curated lists go stale — e.g. OCA's renamed-model data stops at 16→17);
2. do **semantic** per-module impact analysis (removed models/fields/xmlids
   with locations) — on our test fixture with 9 real 18→19 breakages, the best
   pattern scanner reports *zero* issues; the manifest-driven brief finds 9/9;
3. **verify on a real target runtime** and hand the agent structured
   tracebacks to fix in a loop;
4. help with **data migration of custom models** (`pre/post/end-migrate.py`),
   which Odoo's upgrade platform explicitly leaves to you.

This skill adds exactly those four pieces and orchestrates everything else.

## Status — what is tested, honestly

| Component | Status |
|---|---|
| `scripts/gen_manifest.py` | ✅ Run on FULL community trees 18.0 vs 19.0 (610 vs 680 addons). Ground truth confirmed at scale: `hr.contract`→`hr.version` (0.75), `hr.candidate`→`hr.applicant` (0.771), `hr.expense.sheet`→`hr.expense` (0.618), `res.partner.title` removed, `res.groups.privilege` added. Calibration hardened after full-scale run: absolute-intersection floor on rename matching + `test_*` addons excluded (they produced 124 phantom "removed models" and spurious rename candidates). |
| `scripts/upgrade_brief.py` | ✅ 9/9 planted breakages on `examples/fixture_module_18` (6 ERROR, 3 WARNING) with file:line — regression-checked after every change. Dogfooded on real 18.0 production modules with the full manifest; `static/lib` now correctly skipped and generic method tokens (create/get/check...) require the model name on the matched line. |
| `scripts/run_pipeline.sh` | ✅ Smoke-tested end-to-end with graceful skips. |
| Vendored TAQAT precheck | ✅ Runs standalone (`python3 -m scripts.precheck`). |
| `scripts/upgrade_verify.py` | ✅ Integration-tested against a live Odoo 19 container (docker compose, postgres 16). First real run exposed a FALSE PASS: Odoo exits 0 when it silently skips a module (`installable=False` on an 18.0.x version string, unmet deps, not on addons path) — fixed by requiring positive proof of load (`Loading module <name> (n/m)`); new verdict `module_not_loaded` + `not_loaded_reasons`. Real-module install verified end to end (verdict `ok`, module loaded 15/15). |
| `docker/docker-compose.verify.yml` | ✅ Worked first try — no volume tweaks needed (`CUSTOM_ADDONS` env → `/mnt/extra-addons`). |
| `references/manifest_18_19.json` | ✅ FULL community 18.0 vs 19.0 manifest shipped (community-only: enterprise trees not scanned — enterprise-referencing modules need a regen with enterprise checkouts). `manifest_18_19.partial.json` kept as the small demo/test input. |
| `scripts/migrate_all.py` | ✅ Dogfooded on a real 64-module production addons tree: one command → dependency-sorted port order, 9 errors / 198 warnings, effort S=57 M=3 L=4. |
| `scripts/db_upgrade.py` + `docker/docker-compose.upgrade.yml` | ✅ E2E-tested: seeded a real Odoo 18 db (25 modules), ran OpenUpgrade 19.0 (`--upgrade-path`, real pre/post migration scripts executed), post-upgrade `-u all` check, then installed a custom module on the MIGRATED db — all four verdicts `ok`. Caveat: the rehearsal db was small and clean; a production dump will surface data-level issues this smoke run cannot — that is exactly what the rehearsal harness is for. |
| `scripts/anchor_check.py` | ✅ Battle-tested on the same fleet: composes every core parent view through its full target-version inheritance chain (Odoo locate semantics incl. `hasclass()`) and reports ALL broken anchors offline — collapsed a one-crash-per-install cycle into a single pass that found the last 3 breaks at once. Needs `lxml` (the skill's only non-stdlib dependency). Known limits in its honesty contract (ancestor-chain only — sibling-injected anchors false-miss). |
| `references/field-notes-18-19.md` | ✅ 27-entry verified breakage→fix playbook + fleet process lessons, distilled from the full real migration (every entry confirmed against 19 source AND by the final green install). |
| **Whole-fleet proof** | ✅ 2026-07-03: real 89-module production tree → scoped to 71 prod-installed → 45 portable (community runtime) → **45/45 install green on Odoo 19** in 17 verify iterations + 3 parallel domain agents; 141 modules loaded, 0 tracebacks, every module positively verified loaded. 3 modules quarantined for business redesign, 10 blocked on enterprise-19 source — honestly reported, not glossed. |
| **Reproducibility proof** | ✅ Same day, re-ran the ENTIRE workflow on a fresh clone of a different branch (65 modules) using ONLY the skill artifacts (scoping → preflight → anchor_check → field-notes agents): **green on the FIRST runtime iteration** (35/35 loaded), vs 17 iterations of blind discovery originally. The artifacts move breakage-finding offline and up-front. |
| `scripts/preflight.py` | ✅ Idempotent; distilled from the transform pass hand-written across both fleet runs, unit-tested. |

## Quickstart

```bash
# 0) one-time: generate the FULL manifest (needs both source trees; add your
#    enterprise checkouts to --addons-subdirs roots as extra trees if desired)
python3 scripts/gen_manifest.py \
    --old ~/src/odoo-18.0 --new ~/src/odoo-19.0 \
    --addons-subdirs odoo/addons,addons \
    --out references/manifest_18_19.json

# 1) per module: precheck + rewriters + semantic brief
export ODOO19_SRC=~/src/odoo-19.0
bash scripts/run_pipeline.sh /path/to/custom_module references/manifest_18_19.json

# 2) let your agent fix findings per SKILL.md Phase 2, then verify on runtime
python3 scripts/upgrade_verify.py --module custom_module --db verify19 \
    --docker-compose docker/docker-compose.verify.yml
```

Outputs land in `/tmp/odoo-ai/upgrade/<module>/` (`brief.json`, `verify.json`,
`verify.log`, tool logs, `report.html`).

## Layout

```
odoo-upgrade/
├── SKILL.md                      # agent workflow (phases 0–6 + honesty contract)
├── scripts/
│   ├── gen_manifest.py           # source-diff → breaking-change manifest (stdlib)
│   ├── upgrade_brief.py          # module × manifest → findings JSON (stdlib)
│   ├── upgrade_verify.py         # runtime install/verify → structured tracebacks
│   ├── migrate_all.py            # FLEET: brief all, topo order, S/M/L, prod-scoped install set
│   ├── preflight.py              # 10 deterministic field-notes transforms, one pass, before runtime
│   ├── anchor_check.py           # offline view-anchor validator vs target tree (needs lxml)
│   ├── db_upgrade.py             # DATABASE: restore → OpenUpgrade → check, verdicts
│   └── run_pipeline.sh           # per-module orchestrator (external CLIs, never vendored)
├── references/
│   ├── manifest_18_19.partial.json
│   └── vendor/                   # MIT content from letzdoo & TAQAT (see NOTICE.md)
├── templates/                    # pre/post/end-migrate.py on odoo/upgrade-util
├── docker/
│   ├── docker-compose.verify.yml    # per-module install verify (Odoo 19 + pg)
│   └── docker-compose.upgrade.yml   # whole-DB rehearsal (odoo18 seed / odoo19+OpenUpgrade)
└── examples/fixture_module_18/   # 9 planted breakages — regression fixture
```

## Design notes

- **Heuristics labeled as heuristics.** Rename/merge matching uses the overlap
  coefficient on field-name sets (Jaccard fails when the new model is a
  superset: hr.contract 27 fields → hr.version 63). Every candidate carries a
  similarity score and a confirm-before-use note. Keys never overclaim:
  "removed" means "not defined in the scanned trees".
- **License hygiene.** MIT content vendored with licenses; AGPL tools
  (module-migrator, OpenUpgrade) only ever subprocess'd; LGPL libs as deps.
  See `NOTICE.md`.
- **Agent-in-the-loop, not magic.** Scripts produce facts (JSON with
  locations); the agent makes changes; the runtime is the referee.

## Roadmap

1. ~~Integration-test `upgrade_verify.py` + compose harness~~ ✅ done 2026-07-03
   (live Odoo 19 container; exposed and fixed the `module_not_loaded` false pass).
2. ~~Full manifest run; publish `manifest_18_19.json`~~ ✅ done 2026-07-03
   (community trees; enterprise regen still open for enterprise-referencing modules).
3. ~~`gen_data_migration.py`~~ — already exists in this repo:
   `odoo-introspect/scripts/upgrade_check.py` schema-diffs two live briefs and
   scaffolds `pre-migrate.py` (see the `odoo-migration` skill).
4. (parked) Upstream PR: feed generated renamed/removed-model data to
   OCA/odoo-module-migrator (their datasets stop at 16→17).
5. `make_report.py`: single-file HTML report generator (or reuse this repo's
   `html-report` skill).
6. Odoo 19 → 20 manifest when 20 lands (Oct 2026) — the generator makes this a
   one-command refresh.

## License

MIT for own code; vendored/third-party components per `NOTICE.md`.
