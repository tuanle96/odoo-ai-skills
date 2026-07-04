# 18→19 field notes — verified breakages from a real production-fleet migration

Every entry below was hit while porting a **real 89-module production fleet**
(2026-07-03), verified against the Odoo 19 source before fixing, and confirmed
by a green runtime install afterwards (45/45 portable modules, 141 modules
loaded, 0 tracebacks). These are the breakages the generated manifest can NOT
express — structural renames with changed semantics, RNG rules, data patterns.
Use them as a checklist BEFORE the runtime loop; each cost one full install
iteration to discover the hard way.

Format: **symptom → root cause → verified fix**. File paths cite the 19 source
as evidence — re-verify against your own target checkout, don't trust prose.

## Environment / packaging (cheap to pre-flight, crash first)

1. **`Unable to install module X: external dependency not met: <pkg>`**
   → scan ALL manifests up front: `external_dependencies.python`, and
   pre-install into the verify container
   (`pip install --user --break-system-packages <pkgs>`).
2. **`ValueError: Unicode strings with encoding declaration are not supported`
   during registry load** → 19 parses `static/description/index.html` with
   lxml while computing `description_html`; an `<?xml ... encoding=?>` first
   line kills it. Strip the declaration line.
3. **Module silently skipped, exit 0** — version string `18.0.x.y.z` →
   `installable=False` on 19. Bump manifests BEFORE verifying; never read
   rc==0 as success (use `upgrade_verify.py`'s `module_not_loaded` verdict).

## `upgrade_code` rewriter gotchas (Phase 1)

4. The official rewriter **crashes** on `_sql_constraints` containing
   non-literals (`_( )` translation calls) and even on **commented-out**
   constraint blocks (its regex reads across comments). Convert all
   `_sql_constraints` → `models.Constraint` yourself first (attribute name =
   `_<constraint_name>`; message as plain string), delete dead commented
   blocks, then run the rewriter.
5. The rewriter walks EVERY addons path including core; run it in a throwaway
   container (`docker run --rm --user root -v <workspace>:/mnt/extra-addons
   --entrypoint odoo odoo:19 upgrade_code --addons-path=/mnt/extra-addons
   --from 18.0 --to 19.0`) — core rewrites land in the discarded layer, your
   mount gets the real ones.

## Security / access model (res.groups restructure)

6. **`Invalid field 'category_id' in 'res.groups'`** → 19 hangs groups off a
   new model `res.groups.privilege` (which holds the category):
   per module create `<record model="res.groups.privilege">` (name +
   `category_id`) and swap each group's `category_id` → `privilege_id`.
   Evidence: `base/models/res_groups.py:36`, sales_team security XML.
   Order matters: the privilege record must appear AFTER any
   `ir.module.category` record it references, BEFORE the groups.
7. **`Invalid field 'users' in 'res.groups'`** → renamed `user_ids`
   (`res_groups.py:17`).
8. **`res.users.groups_id`** split with changed semantics:
   - writes/creates → `group_ids` (explicit groups, `res_users.py:257`)
   - membership READS/searches → `all_group_ids` (includes implied groups —
     the 18 rel table materialized implied membership, 19 `group_ids` does
     NOT). Upstream evidence: `sale/models/sale_order.py:213` uses
     `('all_group_ids', 'in', ...)`.
   - ir.rule domains: `user.groups_id.ids` → `user.all_group_ids.ids`.
9. **Same rename on FOUR more models** (easy to miss in view/action XML):
   `groups_id` → `group_ids` on ir.ui.view (`ir_ui_view.py:175`), ir.ui.menu
   (`ir_ui_menu.py:29`), ir.actions.act_window (`ir_actions.py:329`),
   ir.actions.server (`ir_actions.py:661`). The XML `groups="xml.id"`
   ATTRIBUTE is unaffected — never touch it.
10. Module categories were consolidated: `module_category_inventory*`,
    `module_category_manufacturing` → `module_category_supply_chain`;
    `module_category_sales_sales` → `module_category_sales`.
    Check `base/data/ir_module_category_data.xml` for survivors.

## Views (RNG + anchors)

11. **Search views: RelaxNG now rejects `expand=` and `string=` on `<group>`**
    (`base/rng/common.rng` group definition). Strip both — but ONLY inside
    `<search>`; form-view groups keep `string`. Symptom is misleading:
    `Element search has extra content: field`.
12. **`target="inline"` removed** from act_window (`current/new/fullscreen/
    main` remain) — settings actions just drop the field.
13. `<tree>` leftovers the rewriter missed: `mode="tree"` on subview fields,
    `'tree_view_ref'` context keys → `list`/`list_view_ref`.
14. Anchor renames confirmed in 19 core (fix by re-anchoring, verified against
    the target arch): picking form `move_ids_without_package` → `move_ids`;
    sale PDF `td_name` → `td_product_name` (rows split product/combo/section —
    review column alignment in UAT); user-preferences form lost `company_id`
    (anchor on `tz`); crm search filters `won`/`lost` →
    `filter_won_status_won`/`_lost`; exact-match `//div[@class='oe_title']`
    breaks when 19 adds classes → use `hasclass('oe_title')`.
15. **Run `anchor_check.py` before the first install** — it composes every
    core parent through its 19 inheritance chain and reports ALL broken
    anchors at once instead of one per crashed install. **Triage each hit
    against the module's OWN `depends`, not by eyeballing.** A "sibling-inject
    false-miss" (the anchor field comes from a sibling module, not the
    ancestor chain the tool composes) is only safe if that sibling is in the
    module's declared `depends` AND installed. Real example both misfire on the
    same file: `branch/inherited_purchase_order.xml` anchors on `incoterm_id`
    (from `purchase_stock`, which IS in branch's depends → safe false-miss)
    AND `requisition_id` (from `purchase_requisition`, NOT in depends → **real
    crash at install**). The 18 code got away with the undeclared dependency
    only because the prod env happened to have that module installed. Fix:
    declare the dependency if the feature is wanted, or drop the op if it was
    injecting foreign fields that aren't this module's concern.

## ORM / Python APIs

16. **`from odoo.fields import <stdlib name>`** (datetime, date...) — 19 made
    `odoo.fields` a package; import from stdlib instead.
17. **`_name_search` override signature is dead** (silently never called) —
    port to `_search_display_name(operator, value) -> Domain`
    (`orm/models.py:1442`); `_search(..., bypass_access=True)` replaces the
    old sudo-bypass pattern; build domains with `odoo.fields.Domain`.
18. **`request.jsonrequest`** → `request.get_json_data()` (`http.py`).
19. `odoo.osv.expression` still exists but DeprecationWarns — new code uses
    `odoo.fields.Domain`. `get_module_resource` is GONE
    (`odoo.tools.misc.file_path` replaces it). `name_get()` definitions are
    dead code (never called by the framework).
20. **Stock valuation rework: `stock.valuation.layer` REMOVED.** The link is
    now direct: `stock.move.account_move_id` /
    `account.move.stock_move_ids` (`stock_account/models/stock_move.py:51`).
    Computes over `move_ids.stock_valuation_layer_ids.account_move_id` port to
    `move_ids.account_move_id`. Overrides of the 18 SVL hooks
    (`_account_entry_move`, `_generate_valuation_lines_data(..., svl_id, ...)`)
    become SILENTLY DEAD — grep for them explicitly; that's lost behavior, not
    a crash.
21. **`procurement.group` removed** (stock rework) — fields/flows built on it
    need redesign, plus a data migration for stored `group_id` columns.
22. **`uom.category` removed** — UoM is now a tree (`relative_uom_id` /
    `relative_factor`, `uom/models/uom_uom.py`). Category-based logic ports to
    walking `relative_uom_id` to the root reference unit (kg / litre / meter
    xmlids survive). `uom.uom.factor` and `.rounding` still exist (computed).
    **This reaches into XML DATA too**: `<record model="uom.category">` records
    and `uom.uom` records using `category_id` / `uom_type` / `factor_inv` all
    break. Port the data to the tree: drop the `uom.category` record; a root
    unit gets `relative_factor eval="1.0"` (no `relative_uom_id`), a derived
    unit gets `relative_factor eval="N"` + `relative_uom_id ref="root"`
    (mirrors `uom/data/uom_data.xml`).
23. **`mobile` → `phone` consolidation** on partner/users/crm: related fields,
    domains and create-vals referencing `mobile` crash at registry setup.
24. Monkeypatches of ORM internals (`_read_group_process_groupby`,
    `fields.Many2many.read = ...`) — check whether the module is even
    installed in production before porting; ours wasn't (dead since 17).
32. **`from odoo import registry` removed** from the top-level `odoo`
    namespace — `from odoo.modules.registry import Registry`, call
    `Registry(dbname)`. Often it's just an unused import (drop it).
33. **`fields.date` / `fields.datetime` (lowercase helpers) removed** — use the
    `fields.Date` / `fields.Datetime` classes (`fields.Date.today()`,
    `fields.Datetime.now()`). Mechanical; `preflight.py` does this.
34. **res.groups internals renamed (a roles/security module hits all of
    these).** On `res.groups`: `category_id` → `privilege_id.category_id`
    (category moved to the privilege); `trans_implied_ids` → `all_implied_ids`
    (`base/models/res_groups.py:71`, "Transitively Implied Groups"); `users` →
    `user_ids`. These are the Python-side twins of the XML #6/#7 transform — a
    module that *reads/iterates* groups (not just declares them) needs them too.
36. **Custom model that `_inherit`ed a core model inherits its COLUMNS —
    orphaned on the DB upgrade.** When a custom model does `_inherit='uom.uom'`
    (etc.) it gets the core model's columns in the DB. If 19 drops one of those
    core fields, the custom table keeps the orphaned column and the DB-upgrade
    framework hard-errors: `UpgradeError: forgot to call util.remove_field on
    <custom.model>.<field>`. Fix is a Phase-4 migration in the owning custom
    module: `migrations/19.0.x.y.z/pre-migrate.py` → `util.remove_field(cr,
    "<custom.model>", "<field>")`. Real case: `bm.report.product.uom`
    (`_inherit='uom.uom'`) kept `hr_timesheet`'s `timesheet_widget` column,
    removed in 19. This is a CODE-install-green module that still breaks the
    DATABASE upgrade — installability ≠ data-migration-ready.
37. **Series-less module versions (`"1.0"`, `"1.0.0"`) are a trap.** `preflight`
    bumps only `18.0.`/`17.0.`/`16.0.`-prefixed versions, so a series-less one
    stays put — and Odoo runs `migrations/<ver>/` scripts only when the version
    INCREASES. A module at `"1.0"` will silently skip its own migration scripts
    on `-u`. Bump such modules to `19.0.x.y.z` by hand (preflight flags them).

35. **19 hard-asserts `unknown comodel_name` at field setup.** A Many2one/
    Many2many pointing at a model that does not exist now raises
    `AssertionError: Field X with unknown comodel_name 'Y'` and blocks the
    whole registry — 18 tolerated the dangling reference. Low-quality vendor
    code carries these; they're PRE-EXISTING bugs the upgrade merely surfaces.
    Point the field at the real model, or quarantine the module and report it —
    do not invent a model to force a green.
28. **Install/uninstall hook signature changed.** 18: `def post_init_hook(cr,
    registry)` (also `pre_init_hook`, `uninstall_hook`). 19 passes a single
    `env`: `def post_init_hook(env)` — the 18 signature raises
    `TypeError: post_init_hook() missing 1 required positional argument:
    'registry'` at install. Inside: `env.cr` for the cursor, `env.registry`
    for the registry; drop the `api.Environment(cr, SUPERUSER_ID, {})` line —
    `env` already is one. Evidence: `account_payment/__init__.py:8`,
    `delivery/__init__.py:10`. Grep every `__init__.py`/`hooks.py` for
    `def *_hook(cr, registry)`.

## Mixed repos (custom + vendored + OCA — a different migration SHAPE)

Some repos are not a clean custom-addons tree. Classify every top-level module
FIRST — the port strategy differs per class:

29. **Vendored upstream (Enterprise / OCA) is REPLACE, not port.** Enterprise
    modules copied into the repo (`web_enterprise`, `web_gantt`, `web_cohort`,
    `web_grid`, `web_map`, `documents`) and OCA modules (`fastapi`,
    `queue_job`, `base_rest`, `auth_jwt`...) are upstream code — swap them for
    the target-version originals (Enterprise 19 checkout; the OCA repo's `19.0`
    branch), never hand-migrate them. Identify by license (`OEEL-1`),
    author (OCA), or a vendored subdir.
30. **`auto_install` poisoning.** Vendored `auto_install=True` modules in the
    addons path AUTO-LOAD as soon as their trigger deps install — even if NOT
    in your `-i` list — and crash on their own un-ported code (observed:
    `web_cohort/controllers/main.py` imports `xlsxwriter` from
    `odoo.tools.misc`, gone in 19 → `Couldn't load module web_cohort`,
    registry fails, your custom modules never even load). For the runtime
    verify, isolate the custom modules into a CLEAN addons dir (copy just
    them), or mark the vendored ones uninstallable — otherwise a module you
    aren't testing sinks the whole run.
31. **Custom-on-OCA-stack.** A custom module's real dependency closure may run
    through OCA (an API layer on `fastapi`/`base_rest`, jobs on `queue_job`,
    auth on `auth_jwt`). It cannot be runtime-verified until those OCA deps
    exist at the target version — clone the OCA repo's target branch first
    (they usually exist: rest-framework/queue/server-auth all have `19.0`).
    Scope the first verify to the subset whose closure is core-only; unblock
    the rest by staging the OCA target branches. "Port the customs" here means
    "port the customs AND stage their upstream stack at the target version".

## Enterprise tier + the uom-field rename wave (only a real enterprise run surfaces these)

Found porting 21 enterprise-dependent modules (crm_enterprise, quality, mrp
integration, account_reports) — verify against a REAL enterprise checkout, not
community. To run the verify at all you must mount the enterprise addons:
`docker run odoo:19 -v <ent_src>/odoo/addons:/mnt/ent:ro -v <custom>:/mnt/custom:ro
--addons-path=/mnt/ent,/mnt/custom` (the community `odoo:19` image has none).
The `odoo_19.0+e` tarball merges community+enterprise into one `odoo/addons`.

38. **The uom `_id`-suffix rename wave.** 19 renamed the uom Many2ones to add
    `_id`: `sale.order.line.product_uom` → `product_uom_id`, `stock.move.
    product_uom` → `product_uom_id`, etc. Breaks `@depends`, related paths,
    create-vals and view `<field>`s. CAREFUL: many custom modules define their
    OWN `product_uom` field (e.g. `sale.blanket.order.line.product_uom`) — those
    stay; only references that traverse into a CORE model get `_id`.
39. **uom rework ripples further than the model.** Beyond `uom.category` (#22):
    `uom.uom.factor_inv` → gone (use `1.0/factor`), `uom_type`/`ratio` → gone
    (relative tree), and any `related='...uom_id.category_id'` field (e.g.
    `product_uom_category_id` on account.analytic.line or a custom line) is dead —
    drop it and any `domain=[('category_id','=',product_uom_category_id)]` uom
    filter it fed.
40. **mrp/quality lot rework: single → multi (M2m).** `mrp.production.
    lot_producing_id` (M2o) → `lot_producing_ids` (M2m); `quality.check.lot_id`
    → `lot_ids`, `finished_lot_id` → `finished_lot_ids` (M2m). Views: rename +
    `widget="many2many_tags"`. Python single-lot logic best-ports to
    `..._ids[:1]` for reads and `[(6,0,ids)]` for writes with a
    `TODO(migration)` — but if a module REWRITES the whole quality.check form
    and leans on removed 18-era quality fields (`spreadsheet_check_cell`, …),
    that's a quality-domain redesign, not a rename → quarantine it honestly.
41. **`account.reconcile.model.rule_type` → `trigger`.** 18 values map:
    `writeoff_button` → `manual`; `invoice_matching`/`writeoff_suggestion` →
    `auto_reconcile`. Hits reconcile-model data/records.
42. **`account.analytic.account._name_search` gone** (and `name_get` across the
    board): #17/#19 apply to enterprise/accounting models too. `name_get` →
    `_compute_display_name`; `from odoo.osv.expression import SQL` → `from
    odoo.tools import SQL`; `name_search(self, name, args=..., operator, limit)`
    → `name_search(self, name, domain=..., operator, limit)` (2nd param renamed).
43. **Undeclared python deps surface only at runtime.** A module importing
    `pandas` (etc.) without declaring `external_dependencies.python` won't show
    in preflight's deps scan and dies with `ModuleNotFoundError` at install —
    grep the module imports, not just the manifests, before the verify.

## Data (the manifest can't see these)

25. **Odoo 19 ships Vietnam's NEW 34-province structure** (18 had 63):
    `base/data/res.country.state.csv` — any module correcting/extending VN
    states, districts or wards needs a business mapping old→new, not a code
    fix. (Generalizes: check l10n data your modules touch.)
26. **Production-db exports masquerading as module data**: `<delete>` of
    xmlids that only exist on the prod db → crashes every fresh install; move
    to an idempotent migration script (`env.ref(..., raise_if_not_found=False)`).
    Records with hardcoded res.users db ids → FK violation on fresh install;
    that data belongs to the DATABASE (travels through the upgrade), remove
    the file from the manifest.
27. Group-by filters hardcoding DB ids (e.g. `('all_group_ids','in',32)`) —
    survives the rename but breaks on any other database; flag for cleanup.

## Fleet process lessons (why this run converged)

- **Scope by production state FIRST**: `SELECT name, state FROM
  ir_module_module WHERE name IN (...)` on the live db. Our fleet: 89 dirs →
  71 installed → 18 dead modules never ported (one of them was the single
  hardest "fix" in the pile).
- **Exclude transitively**: enterprise-dependent and quarantined modules drag
  their dependents out of the portable set (`migrate_all.py --exclude` does
  the closure).
- **One-shot batch install** (`-i m1,m2,...` on a fresh db) beats per-module
  runs: one container boot per iteration, and the log still names the first
  offender. Parse with `upgrade_verify.parse_output`; count
  `Loading module (n/m)` lines as the progress metric between iterations.
- **Debug flags that unlock opaque errors**: ParseError with no message →
  `--log-handler odoo.tools.convert:DEBUG`; RNG view errors →
  `--log-handler odoo.tools.view_validation:DEBUG`.
- **Fan out mechanical sweeps to parallel agents with disjoint file domains**
  (security-XML+py renames / view-data XML / Python APIs), each required to
  verify every rename against the target source, never from memory. Hand off
  cross-domain findings explicitly (our security agent found the
  view-XML groups_id renames and handed 15 exact sites to the XML agent).
  Assign `groups_id`-in-Python to the Python agent explicitly so it doesn't
  fall between the two.
- **A SMALL module set still needs the source-verified sweep.** `preflight.py`
  (deterministic) and `anchor_check.py` (views) do NOT cover the
  source-verified transforms (mobile→phone, groups_id, SVL, uom.category,
  `_name_search`, jsonrequest). For a handful of modules, skip the agents and
  grep the field-notes patterns directly — but DO grep them. Skipping this on
  a 6-module set missed `base_phone`'s `related='...mobile'` and cost an
  iteration.
- **Sweep `groups_id` in BOTH Python AND XML.** `<field name="groups_id">`
  inside a view / menu / action record → `group_ids` (#9). This is NOT a safe
  preflight blind-replace: `<field name="groups_id">` also appears in a view
  ARCH to display some model's own field, so it needs record-model context.
  Grep the XML too — a Python-only sweep leaves the view records broken.
- **Distinguish a module's OWN field from the core one.** A roles/security
  module may define its own `groups_ids` / `group_ids` Many2many — that field
  is NOT the res.users rename and must be left alone. Only res.users
  reads/writes get #8. Read before you sed.
- **The db container needs a healthcheck for `compose up -d --wait db` to
  mean "ready".** Without it, `--wait` returns on "running" and the next odoo
  run races postgres startup and dies on connect — a non-failure that burns an
  iteration. The shipped compose files set a `pg_isready` healthcheck +
  `depends_on: {db: {condition: service_healthy}}`.
- **`preflight.py` reports only errors it INTRODUCED**, snapshotting the
  source's already-broken files first. A real repo shipped 123 files with
  pre-existing syntax errors (committed-broken tests); without the baseline,
  pre-flight would look like it corrupted the tree.
- **End with positive proof**: every module in the set must show
  `Loading module <name> (n/m)` in the final green run — rc==0 alone lies.
