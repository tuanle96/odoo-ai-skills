# Sample output — what each layer returns

Abbreviated, realistic JSON for each script (model: `sale.order`, Odoo 18), trimmed with `"...": "..."` to show the **schema shape**, not full data. Each script prints its JSON between sentinels (e.g. `===ODOO_BRIEF_START===` … `===ODOO_BRIEF_END===`); `odoo-ai` strips those and writes `<model>.<layer>.json`.

> **Full, valid JSON fixtures** (not trimmed) live in [`samples/`](samples/) — one file per layer, validated in CI against each script's emitted keys.

---

## Layer A — `model_brief.py`

```json
{
  "identity": {
    "model": "sale.order", "table": "sale_order", "description": "Sales Order",
    "order": "date_order desc, id desc", "rec_name": "name",
    "inherit": ["portal.mixin", "mail.thread", "mail.activity.mixin"],
    "inherits": {}, "transient": false, "auto": true,
    "capabilities": {"mail_thread": true, "activities": true, "portal": true,
                     "company_dependent_fields": []}
  },
  "field_count": 142,
  "fields": {
    "amount_total": {
      "type": "monetary", "string": "Total", "help": "...", "store": true, "required": false,
      "readonly": true, "index": false, "copy": false, "translate": false, "tracking": null,
      "has_default": false, "compute": "_compute_amounts", "inverse": null, "search": null,
      "related": null, "depends": ["order_line.price_total"], "comodel": null,
      "groups": null, "company_dependent": false, "modules": "sale"
    },
    "state": {
      "type": "selection", "string": "Status", "store": true, "tracking": 3,
      "has_default": true,
      "selection": [
        {"value": "draft", "label": "Quotation"},
        {"value": "sent", "label": "Quotation Sent"},
        {"value": "sale", "label": "Sales Order"},
        {"value": "cancel", "label": "Cancelled"}
      ],
      "...": "..."
    },
    "partner_id": {
      "type": "many2one", "string": "Customer", "comodel": "res.partner",
      "ondelete": "restrict", "domain": "[('type','!=','private')]", "...": "..."
    },
    "order_line": {
      "type": "one2many", "comodel": "sale.order.line",
      "inverse_name": "order_id", "...": "..."
    }
  },
  "security": {
    "access_rights": [
      {"id": 1, "name": "sale.order.user", "group_id": [10, "Sales/User"],
       "perm_read": true, "perm_write": true, "perm_create": true, "perm_unlink": false}
    ],
    "record_rules": [
      {"id": 4, "name": "Personal Orders", "active": true, "global": false,
       "domain_force": "['|',('user_id','=',user.id),('user_id','=',False)]",
       "groups": [[6, false, [10]]], "...": "..."}
    ]
  },
  "auto_triggers": {
    "server_actions": [
      {"id": 7, "name": "Notify finance", "state": "code", "usage": "ir_actions_server",
       "code_present": true, "code_len": 834,
       "code_preview": "for rec in records:\n    rec.message_post(body=…  (set CODE=1 for full body)"}
    ],
    "automated_actions": [
      {"id": 2, "name": "Auto-confirm web orders", "trigger": "on_create_or_write",
       "filter_domain": "[('state','=','draft')]", "active": true}
    ],
    "crons": [],
    "_code_gating": "redacted (set CODE=1 for full bodies)"
  },
  "overridden_methods": ["action_confirm", "write", "create"],
  "methods": {
    "action_confirm": [
      {"addon": "sale_stock",
       "class": "odoo.addons.sale_stock.models.sale_order.SaleOrder",
       "file": ".../sale_stock/models/sale_order.py", "line": 41, "decorators": null,
       "has_super": true, "super_position": "early (before custom logic)",
       "returns_before_super": false, "hooks_called": ["_action_confirm"],
       "heuristic": true},
      {"addon": "sale", "class": "odoo.addons.sale.models.sale_order.SaleOrder",
       "file": ".../sale/models/sale_order.py", "line": 512, "decorators": null,
       "has_super": true, "super_position": "late (after custom logic)",
       "returns_before_super": false,
       "hooks_called": ["_action_confirm", "_send_order_confirmation_mail"],
       "heuristic": true}
    ]
  },
  "manifest_depends": {
    "method_chain_addons": ["sale_stock", "sale"],
    "by_location": {
      "core": ["sale_stock", "sale"],
      "enterprise": [],
      "local": [],
      "unknown": []
    },
    "module_paths": {
      "sale_stock": "/opt/odoo/odoo/addons/sale_stock",
      "sale": "/opt/odoo/odoo/addons/sale"
    },
    "note": "'core'/'enterprise' addons ship with Odoo — depend on the one that OWNS the method ... 'local' addons are yours/third-party ... Classification is by on-disk path, not author."
  },
  "_warnings": [],
  "_caveat": "MRO is the POTENTIAL super() chain. Use has_super / super_position ..."
}
```

- `methods.<name>` is **MRO order**: index 0 may run first; `super()` descends 0 → 1 → …
- `fields.<name>.selection` lists the **actual `(value, label)` literals** for selection fields — stop guessing `state='confirmed'` when the real value is `'sale'`. A method-resolved selection shows `{"_dynamic": "method:_name"}`.
- Per-field `ondelete` / `inverse_name` / `domain` appear on relational fields; `index` / `copy` / `translate` / `tracking` / `has_default` round out the attributes AI most often gets wrong.
- `manifest_depends.by_location` splits the method chain into `core` / `enterprise` / `local` / `unknown` by each module's **on-disk path** (not author — custom modules routinely ship `author = 'Odoo S.A.'`). Depend on the module that *owns* the method you extend; don't blindly depend on every `local` addon you traversed. `module_paths` shows where each resolved.
- `super_position` / `returns_before_super` / `hooks_called` are regex heuristics — every entry carries `"heuristic": true`. Confirm big flows with Layer D.
- `_warnings` is `[]` when clean; a non-empty entry such as `"field_modules lookup failed (...); 'modules' will be null per field"` means that part of the brief is partial — read it, don't trust silence.

---

## Layer B — `entrypoints.py`

```json
{
  "model": "sale.order",
  "views": {
    "form": {
      "buttons": [
        {"name": "action_confirm", "type": "object", "string": "Confirm",
         "context": null, "groups": null,
         "invisible": "state not in ['draft','sent']", "confirm": null}
      ],
      "fields": [
        {"name": "partner_id", "widget": "res_partner_many2one", "groups": null,
         "modifiers": {"readonly": "state in ['sale','cancel']"}},
        {"name": "state", "widget": "statusbar", "groups": null, "modifiers": null}
      ],
      "root_view_id": 423,
      "inheritance_chain": [
        {"xmlid": "sale.view_order_form", "name": "sale.order.form", "priority": 16, "mode": "primary"},
        {"xmlid": "sale_stock.view_order_form_inherit", "name": "...", "priority": 20, "mode": "extension"},
        {"xmlid": "custom_x.view_order_form_inherit", "name": "...", "priority": 30, "mode": "extension"}
      ]
    },
    "list": {"buttons": [], "fields": [{"name": "name", "widget": null,
             "groups": null, "modifiers": null}], "root_view_id": 425,
             "inheritance_chain": ["..."]}
  },
  "window_actions": [
    {"id": 366, "name": "Quotations", "view_mode": "list,kanban,form",
     "domain": "[]", "context": "{'default_...': 1}", "target": "current",
     "view_id": false}
  ],
  "reports": [
    {"id": 30, "name": "Quotation / Order", "report_name": "sale.report_saleorder",
     "report_type": "qweb-pdf"}
  ]
}
```

- `button.type`: `"object"` → calls the method named in `name`; `"action"` → runs the action with that xmlid/id.
- `inheritance_chain` is the base view + every applied **extension** view in application order (parents first, siblings by `priority`). Before writing an xpath, inherit the right view from this list — the resolved arch alone hides which inheritors already touched a node. Render one specific view with `--view-xmlid`/`--view-id`. It is **diagnostic/best-effort**: the real applied set also depends on context, action, groups, company and website, so the resolved `arch` (not the chain) is authoritative for a given `get_view()` call. A top-level `_caveat` repeats this.
- `modifiers` holds the v17+ direct expressions (`invisible`/`readonly`/`required`/`column_invisible`) — there is no `attrs` (removed in v17).
- `reports` here is the **quick** list; for QWeb templates + paperformat + parser, use Layer C.
- A view that fails to load returns `{"_error": "..."}` for that view type.

---

## Layer C — `metadata.py`

```json
{
  "model": "sale.order",
  "menu_graph": {
    "actions": [{"id": 366, "name": "Quotations", "view_mode": "list,kanban,form"}],
    "menus": [{"path": "Sales / Orders / Quotations", "action": "Quotations"}]
  },
  "seeded_data": {
    "count_returned": 12, "limit": 150,
    "by_module": {"sale": 9, "sale_stock": 3},
    "noupdate_records": ["sale.sale_order_rule_personal (res_id=4)"],
    "sample": ["sale.action_quotations", "sale.report_saleorder", "..."]
  },
  "reports": [
    {"name": "Quotation / Order", "report_name": "sale.report_saleorder",
     "report_type": "qweb-pdf", "paperformat": [3, "European A4"],
     "qweb_templates": ["sale.report_saleorder", "sale.report_saleorder_document"],
     "parser_model": null,
     "hint": "If customizing data, look for _get_report_values on the parser model; if customizing layout, inherit the qweb template(s) above."}
  ],
  "_warnings": []
}
```

- `noupdate_records` (noupdate=True) are loaded once on install, then **protected from `-u`** — later XML edits won't apply; change them on installed DBs with a migration. (Default noupdate=False records are re-asserted from XML on `-u`, so runtime/UI edits revert.)
- `parser_model` is non-null only when a `report.<report_name>` model exists (custom `_get_report_values`); null means the standard template-only path.

---

## Layer D — `trace_flow.py`

```json
{
  "root": "sale.order(42).action_confirm",
  "committed": false, "error": null,
  "sql_count_enabled": true, "warnings": [],
  "total_addon_calls": 137, "total_sql": 88,
  "summary": {
    "call_counts": [
      {"model": "stock.move", "method": "_action_done", "addon": "stock", "count": 14},
      {"model": "sale.order.line", "method": "_compute_qty", "addon": "sale", "count": 9}
    ],
    "top_self_sql": [
      {"model": "stock.move", "method": "_action_done", "addon": "stock",
       "line": 980, "self_sql": 31, "cumulative_sql": 44},
      {"model": "account.move", "method": "create", "addon": "account",
       "line": 120, "self_sql": 12, "cumulative_sql": 12}
    ],
    "max_depth": 9,
    "writes_by_model": {
      "account.move": {"creates": 1, "writes": 0, "fields": ["line_ids", "move_type", "partner_id"]},
      "sale.order":   {"creates": 0, "writes": 2, "fields": ["state"]},
      "stock.move":   {"creates": 6, "writes": 3, "fields": ["product_id", "quantity", "state"]}
    },
    "exception_origin": null
  },
  "distinct_steps": [
    {"model": "sale.order", "method": "action_confirm", "addon": "sale_stock"},
    {"model": "sale.order", "method": "action_confirm", "addon": "sale"},
    {"model": "sale.order", "method": "_action_confirm", "addon": "sale"},
    {"model": "stock.rule",  "method": "_run_pull",      "addon": "stock"},
    {"model": "account.move","method": "create",         "addon": "account"}
  ],
  "calls": [
    {"depth": 0, "addon": "sale_stock", "model": "sale.order",
     "method": "action_confirm", "line": 41, "sql_count": 88},
    {"depth": 1, "addon": "sale", "model": "sale.order",
     "method": "action_confirm", "line": 512, "sql_count": 84},
    {"depth": 2, "addon": "sale", "model": "sale.order",
     "method": "_action_confirm", "line": 530, "sql_count": 71},
    {"depth": 3, "addon": "stock", "model": "stock.rule",
     "method": "_run_pull", "line": 210, "sql_count": 35}
  ]
}
```

- `distinct_steps` is the compact, first-seen `(model, method, addon)` summary — read this first; `calls` is the full ordered trace.
- `summary` is the scan-first digest: `call_counts` (most-invoked pairs → N+1/loop smell), `top_self_sql` (frames doing the most SQL **themselves** — cumulative minus children, so a thin parent doesn't mask its callee), `writes_by_model` (creates/writes per model + the **field names** touched — names only, never values), and `exception_origin` (the innermost addon frame an exception passed through, or `null`).
- `sql_count` is **cumulative including children**, so the depth-0 frame's count equals `total_sql`. A high count on a deep, frequently-called frame is your N+1 hotspot.
- `sql_count_enabled` is `false` when wrapping the cursor's `execute` failed in this environment; the call graph is still traced, but `total_sql` is `null` and per-call `sql_count` is `0`. The reason is recorded in `warnings`.
- `committed: false` = rolled back (default). `error` carries `"<ExcType>: msg"` if the method raised — the trace up to that point is still valid.
- `COMMIT=1` flips `committed` to `true` and **really persists** (`RELEASE SAVEPOINT` + `env.cr.commit()`) — throwaway/dev DB only.

---

## Layer E — `field_refs.py`

```json
{
  "model": "sale.order",
  "field": "commitment_date",
  "field_exists": true,
  "path_resolution": "graph-resolved",
  "defining_modules": "sale_stock",
  "reference_count": 3,
  "severity_counts": {"high": 1, "medium": 2, "low": 0},
  "references": [
    {"kind": "stored_compute_depends", "severity": "high",
     "model": "sale.order.line", "field": "commitment_date_copy",
     "stored": true, "compute": "_compute_commitment",
     "related": null, "depends": ["order_id.commitment_date"],
     "resolved_via": {"via": "depends", "path": "order_id.commitment_date",
                      "terminal_model": "sale.order", "terminal_field": "commitment_date"}},
    {"kind": "view", "severity": "medium", "id": 781,
     "xml_id": "sale_stock.view_order_form_inherit", "name": "...",
     "type": "form", "inherit_id": "sale.view_order_form"},
    {"kind": "record_rule", "severity": "medium", "id": 55,
     "name": "Late orders", "global": false,
     "domain_force": "[('commitment_date','<',now)]"}
  ],
  "_warnings": [],
  "_caveat": "Field depends/related links are graph-resolved through comodel_name ..."
}
```

- `path_resolution` is `"graph-resolved"` with `--resolve-paths` (`RESOLVE_PATHS=1`) or `"text-heuristic"` by default. Graph mode walks each dotted `depends`/`related` path through `comodel_name` and confirms it lands on **this exact** `model.field` — so `partner_id.name` and `company_id.name` are no longer conflated. Matched field refs then carry `resolved_via` (the path + resolved terminal).
- Default (text-heuristic) mode matches the **last segment** of a dotted path regardless of which model it reaches — faster, but can false-positive on same-named fields. Use `--resolve-paths` before a real rename/drop.
- `severity`: stored/related computes are `high` (rot silently); views / rules / filters / actions are `medium`; metadata is `low`. View/domain/code text scans stay whole-identifier heuristics even in graph mode.
- `field_exists: false` means the field is already gone (renamed/removed) — the scan still runs so you can find lingering dependents.

---

## Layer G — `security_sim.py`

```json
{
  "model": "sale.order",
  "user": {"id": 7, "login": "salesperson@acme.com", "name": "Sam Sales",
           "is_superuser": false, "groups_count": 14},
  "company": {
    "acting_company": {"id": 1, "name": "Acme"},
    "user_allowed_companies": [{"id": 1, "name": "Acme"}, {"id": 2, "name": "Acme EU"}],
    "model_has_company_id": true
  },
  "access_rights": {
    "read": true, "write": true, "create": true, "unlink": false,
    "_source": "additive over applicable ir.model.access rows",
    "odoo_check": {"read": true, "write": true, "create": true, "unlink": false},
    "contributing_acl": [
      {"name": "sale.order user", "group": "Sales / User: Own Documents Only",
       "perm_read": true, "perm_write": true, "perm_create": true, "perm_unlink": false}
    ]
  },
  "record_rules": {
    "read": {
      "effective_domain": ["|", ["user_id", "=", 7], ["user_id", "=", false]],
      "global_rules": [{"id": 12, "name": "Multi-company", "domain_force": "[('company_id','in',company_ids)]", "groups": null}],
      "group_rules": [{"id": 33, "name": "Own Quotations", "domain_force": "[('user_id','=',user.id)]", "groups": ["Sales / User: Own Documents Only"]}]
    },
    "write": {"effective_domain": ["..."], "global_rules": ["..."], "group_rules": ["..."]}
  },
  "field_access": {
    "restricted": [{"field": "margin", "groups": "sale.group_sale_margin"}],
    "_note": "fields hidden by `groups=` for this user ..."
  },
  "_warnings": [],
  "_caveat": "Simulates the ACTING USER. sudo() bypasses BOTH ACL and record rules ..."
}
```

- `access_rights` is the **additive** ACL verdict (a mode is granted if any of the user's applicable `ir.model.access` rows grants it), with `contributing_acl` listing the rows that granted. `odoo_check` is Odoo's own `check_access` verdict as a cross-check — if they disagree, a `_warnings` entry tells you to trust `odoo_check`.
- `record_rules.<mode>.effective_domain` is the **authoritative** combined domain from Odoo's `ir.rule._compute_domain` (global rules ANDed, group rules ORed then ANDed). `null` means no row restriction (the user sees all rows for that mode). Empty record rules + ACL-granted = full table access.
- `field_access.restricted` are fields that **vanish** from this user's `fields_get` because of a `groups=` attribute — they can't read or write them through the ORM/UI.
- `is_superuser: true` (uid 1) means ACL **and** record rules are bypassed at runtime — the result is not representative; pass `--user <a real login/id>`.
- **`sudo()` is the blind spot.** This models the acting user; any code path calling `sudo()` ignores all of the above. Grep source (Layer A `SOURCE=1`) for `sudo(` on the methods you care about.
