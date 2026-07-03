---
name: odoo-upgrade
description: >
  Migrate Odoo custom modules and databases between major versions, currently
  specialized for Odoo 18 -> 19. Use this skill WHENEVER the user mentions
  upgrading Odoo, migrating a module/addon to a new version, "port to 19",
  version bump, breaking changes between Odoo versions, writing migration
  scripts (pre/post/end-migrate), OpenUpgrade, upgrade.odoo.com, or fixing a
  module that fails to install after an upgrade — even if they don't use the
  word "migration". Also trigger when a traceback clearly comes from
  version-incompatible code (removed model/field/xmlid, tree->list,
  _sql_constraints, type='json' routes).
---

# Odoo Upgrade (18 → 19)

Migrate custom modules AND their data to a new Odoo major version with runtime
verification. This skill orchestrates existing tools first (official
`upgrade_code`, OCA `odoo-module-migrate`, vendored MIT pattern references) and
adds what none of them have: a **generated breaking-change manifest**, a
**semantic per-module brief**, a **runtime verify loop**, and **data-migration
script templates** built on `odoo/upgrade-util`.

## Honesty contract (read first, applies to every phase)

1. Never claim a module is "migrated" or "compatible" until
   `upgrade_verify.py` returns verdict `ok` AND its tests pass. Static analysis
   verdicts are always phrased as "no known breakage detected", never "safe".
2. Manifest entries under `*_candidates` keys are HEURISTIC (similarity-scored).
   Present them as candidates with their score; confirm via git history or the
   target runtime before renaming anything.
3. When unsure how to fix a finding, insert `# TODO(migration): <why>` and list
   it in the final report instead of guessing silently.
4. `upgrade_brief.py` findings tagged `word-boundary text match` can be false
   positives — say so when reporting them.
5. Distinguish clearly in all reports: what a script measured vs. what you
   inferred.

## Outputs

All machine outputs go to `/tmp/odoo-ai/upgrade/<module>/`:
`precheck.txt`, `upgrade_code.txt`, `module_migrate.txt`, `brief.json`,
`verify.json`, `verify.log`, `report.html`. Fleet runs add
`_fleet/fleet.json`; database runs add `_db/<step>.{json,log}`.

## Migrating EVERYTHING (all custom addons + the database)

The per-module phases below are the inner loop. This outer workflow took a
real 89-module production fleet to a verified-green 19 install in one day
(details + every breakage: `references/field-notes-18-19.md`). Work on a
COPY of the addons tree — never the originals.

1. **Scope by the production db FIRST** — don't port dead code:
   ```sql
   SELECT name FROM ir_module_module WHERE state='installed';
   ```
   Feed that list to the fleet brief. On our fleet 18/89 modules were
   uninstalled — one of them would have been the hardest "fix" in the pile.
2. **Fleet brief + portable set** — one command over the whole tree:
   ```bash
   python3 scripts/migrate_all.py --addons-dir /path/to/COPY \
       --manifest references/manifest_18_19.json \
       --installed-file prod_installed.txt \
       --exclude <enterprise-dep and quarantined modules>
   ```
   `fleet.json` gives the dependency-sorted port `order` and S/M/L effort;
   `install_set.txt` is the portable set (exclusions propagate to dependents
   transitively). Quarantine early: modules built on removed subsystems
   (SVL, procurement.group, l10n data rewrites) are redesign work with
   business decisions — mark `TODO(migration)` and keep them out of the loop.
3. **Pre-flight, before any container run** (each of these otherwise costs a
   full install iteration):
   ```bash
   python3 scripts/preflight.py --addons-dir /path/to/COPY --out /tmp/odoo-ai/upgrade/_fleet
   ```
   applies the 10 DETERMINISTIC field-notes transforms in one pass
   (`_sql_constraints`→Constraint incl. dead comment blocks that crash the
   rewriter, version bump, description xml-decl strip, target=inline,
   search-view group string/expand, module categories, tree leftovers,
   odoo.fields stdlib import) and writes `pydeps.txt` for the verify container.
   Then **read `brief.json`'s WARNINGs as a checklist** — the day-1 brief
   already names high-signal breakages (e.g. a removed field like
   `crm.lead.mobile`); fix those preventively now instead of letting them
   crash at iteration N. Source-verified transforms (res.groups→privilege,
   group renames, SVL, mobile→phone...) come next via agents reading
   `references/field-notes-18-19.md`.
4. **Rewriters** (Phase 1) on the copy, then **`anchor_check.py`**:
   ```bash
   python3 scripts/anchor_check.py --addons-dir /path/to/COPY \
       --target-addons ~/src/odoo-19.0/addons \
       --modules-file install_set.txt
   ```
   It composes every core parent through its full 19 inheritance chain and
   reports ALL broken view anchors at once — offline, no database. At
   runtime Odoo stops at the first one; this collapses N crash-iterations
   into one pass.
5. **Batch verify loop** — install the WHOLE portable set on a fresh target
   db in one run (`-i m1,m2,...`); parse the log with `upgrade_verify.py`'s
   parser; fix the first failure; repeat. The loaded-module count is your
   progress metric. Opaque errors: re-run with
   `--log-handler odoo.tools.convert:DEBUG` (data ParseError) or
   `--log-handler odoo.tools.view_validation:DEBUG` (RNG). For large fleets,
   fan mechanical sweeps out to parallel agents with DISJOINT file domains
   (security XML+py / view XML / Python APIs), each verifying every rename
   against the target source — never from memory. Make ONE boundary explicit
   to avoid a gap: `groups_id` in Python (`res.users` reads/writes) is owned by
   the Python-API agent, not the security-XML agent — else each assumes the
   other did it. A missed `groups_id` search does not crash at install (it
   fails only when that path runs), so it slips past a green install.
6. **Green means proven**: rc==0 AND every module in the set shows
   `Loading module <name> (n/m)` in the log AND no tracebacks/log errors.
   rc==0 alone lies (silent skips) — see the honesty contract.
7. **Database rehearsal** (Community) — restore a copy, run OpenUpgrade,
   then update your ported customs on it:
   ```bash
   git clone --depth 1 --branch 19.0 https://github.com/OCA/OpenUpgrade ~/src/OpenUpgrade
   export OPENUPGRADE=~/src/OpenUpgrade CUSTOM_ADDONS=/path/to/PORTED-addons
   python3 scripts/db_upgrade.py full --dump prod.dump --db up19 \
       -C docker/docker-compose.upgrade.yml --modules all
   ```
   Each step (`restore`/`upgrade`/`check`) writes a structured verdict.
   **Enterprise / Odoo.sh / Online**: the db goes through upgrade.odoo.com
   (free with an active Enterprise subscription; check
   `ir_config_parameter` key `database.expiration_date` first) — steps 1–6
   are still yours, and `db_upgrade.py check` still verifies your customs on
   the returned test db.
8. **Done means**: every module verdict `ok` + tests pass on the upgraded
   rehearsal db, functional spot-checks by a human (silently-dead overrides
   of removed hooks lose behavior WITHOUT crashing — grep for them, see
   field notes #20), then Phase 5's backup / cutover / rollback discipline.
   A green rehearsal is the entry ticket to cutover planning, not the cutover.

## Phase 0 — Inventory & brief

For each custom module:

```bash
bash scripts/run_pipeline.sh /path/to/custom_module references/manifest_18_19.json
```

This runs, in order, skipping tools that aren't installed:
1. **TAQAT precheck** (vendored, MIT) — XML/JS/SCSS syntax-pattern issues
   (tree tags, attrs, Bootstrap classes...). NOTE: a clean precheck means only
   "no syntax-pattern issues" — it does NOT see semantic breakage.
2. **`upgrade_brief.py`** (this skill) — cross-references the module against
   `references/manifest_18_19.json`: removed modules/models/fields/methods/xmlids,
   with rename/merge candidates and file:line locations.

If `references/manifest_18_19.json` is missing or the user targets other
versions, generate it first (needs both source trees checked out, ideally
community + enterprise):

```bash
python3 scripts/gen_manifest.py --old ~/src/odoo-18.0 --new ~/src/odoo-19.0 \
    --addons-subdirs odoo/addons,addons --out references/manifest_18_19.json
```

Read `brief.json` and summarize per module: ERROR count (blocks install),
WARNING count (probable breakage), estimated effort (S: warnings only /
M: <5 errors / L: removed-model rewrites or OWL work).

## Phase 1 — Deterministic rewrites (never hand-edit what a tool rewrites)

Run BEFORE any manual/AI editing, in this order:

1. Official rewriter (covers `_sql_constraints`→`models.Constraint`,
   `type='json'`→`'jsonrpc'`, deprecated properties, dynamic-date domains...):
   `$ODOO19_SRC/odoo-bin upgrade_code --from 18.0 --to 19.0 --addons-path <dir>`
2. OCA rewriter (AGPL — ALWAYS an external CLI, never vendor its code):
   `odoo-module-migrate --directory <dir> --modules <m> --init-version-name 18.0
   --target-version-name 19.0 --no-commit`

Commit after each tool so diffs stay reviewable.

## Phase 2 — Manifest-driven fixes (the AI part)

Work through `brief.json` findings in severity order. Before editing, load the
relevant reference:

| Topic | Read |
|---|---|
| **Verified breakage→fix playbook from a real fleet migration** (res.groups privilege, group_ids vs all_group_ids, SVL, uom tree, mobile→phone, RNG rules, data patterns, process lessons) | `references/field-notes-18-19.md` — walk it as a checklist BEFORE the verify loop |
| Python/ORM 18→19 (SQL(), Constraint, groups_id, type hints) | `references/vendor/letzdoo-odoo-development/odoo-version-knowledge-18-19.md` |
| Curated view/JS/mail/portal patterns + real-world gotchas | `references/vendor/taqat-odoo-upgrade/reference/odoo18_to_19.md` |
| Error message → fix lookup | `references/vendor/taqat-odoo-upgrade/reference/error_catalog.md` |
| OWL 3.x component changes | `references/vendor/letzdoo-odoo-development/odoo-owl-components-18-19.md` |

Fix rules:
- `removed_model` with a candidate: rename references only after confirming the
  candidate (check the target source or `git log --follow`). `hr.contract` →
  `hr.version` is confirmed upstream; treat others case by case.
- `removed_xmlid`: find the successor id in the target tree
  (`grep -rn 'id="..."' $ODOO19_SRC/...`) — do not delete the reference blindly.
- `removed_field_usage` / `removed_method_usage`: locate each hit, decide
  rename vs. rewrite vs. drop; unsure → `# TODO(migration)`.
- Update `__manifest__.py` `version` to `19.0.x.y.z` and fix `depends`.

## Phase 3 — Runtime verify loop (mandatory)

```bash
python3 scripts/upgrade_verify.py --module <m> --db verify19 \
    --docker-compose docker/docker-compose.verify.yml            # first run: -i
# after fixes:
python3 scripts/upgrade_verify.py --module <m> --db verify19 \
    --docker-compose docker/docker-compose.verify.yml --update   # -u
```

Loop protocol: read `verify.json` → for each traceback open
`custom_frame.file:line` (that's YOUR code; if `custom_frame` is null the
failure is in core/interaction — read the full frames) → fix → re-run with
`--update`. Repeat until verdict `ok`. Then run tests:
`--test-tags /<module>`. Cap at ~8 iterations; if still failing, stop and
report the remaining tracebacks honestly instead of thrashing.

## Phase 4 — Data migration scripts (when module data structure changed)

Needed when YOUR module renamed/removed/retyped its own fields or models
between the 18-version and the 19-version of the module (Odoo's platform only
migrates standard-module data). In this repo, hand off here: the
**`odoo-migration`** skill owns writing these scripts (the "-u or migration?"
decision table, upgrade-util/openupgradelib function map), and
`odoo-introspect/scripts/upgrade_check.py` schema-diffs your module between two
live registries and scaffolds the pre-migrate for you. Standalone fallback:
copy from `templates/`:

```
my_module/migrations/19.0.1.0.0/{pre-migrate.py,post-migrate.py,end-migrate.py}
```

Rules (see `references/vendor/letzdoo-odoo-development/data-migration-patterns.md`
for worked examples):
- pre = SQL-level renames BEFORE ORM loads (`util.rename_field`,
  `util.rename_model`, `util.rename_xmlid` — signatures verified against
  odoo/upgrade-util source).
- type change = backup column in pre → repopulate via ORM in post → drop
  backup in end.
- NEVER hardcode integer ids; resolve via xmlid/`ir_model_data`.
- Test with realistic data volume, not 50 dev records.

## Phase 5 — Database upgrade path (whole-instance decision)

- **Enterprise / Odoo.sh / Online**: request an upgraded TEST database from
  upgrade.odoo.com IN PARALLEL with code migration (official recommended flow);
  standard-module data is Odoo's job, phases 0–4 above are yours. Repeat
  request→test until clean, then schedule production.
- **Community**: OpenUpgrade (AGPL, external tool), strictly one version per
  hop — 18.0→19.0 directly here, chains for older sources. This skill's
  `scripts/db_upgrade.py` + `docker/docker-compose.upgrade.yml` is the
  rehearsal harness for exactly this (see "Migrating EVERYTHING" above).
- Always: full `pg_dump -Fc` + filestore backup, staging rehearsal, low-activity
  go-live window, tested rollback.

## Phase 6 — Report

Render `/tmp/odoo-ai/upgrade/<module>/report.html` (single self-contained file,
no CDN) from `brief.json` + `verify.json` + tool logs, with sections: summary
verdict, what each tool auto-fixed, manual fixes applied, remaining
`TODO(migration)` items, verify iterations table, and the honesty notes from
this contract. Vietnamese or English to match the user.

## Scope limits (state them when relevant)

- Manifest quality = quality of the scanned trees; partial scans mean
  "removed" may be "moved". The shipped `manifest_18_19.json` is community-only:
  enterprise-only changes require a regen with enterprise checkouts.
- `upgrade_verify.py` verdict `module_not_loaded` means Odoo exited 0 but never
  loaded your module (18.x version string → installable=False, unmet deps, not
  on the addons path) — bump `__manifest__.py` version to `19.0.x.y.z` and fix
  deps first; do NOT read rc==0 as success.
- Multi-hop (16→19) is out of scope for now: run this skill per hop.
