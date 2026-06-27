# Sample output — what each layer returns

Abbreviated, realistic JSON for each script (model: `sale.order`, Odoo 18), trimmed with `"...": "..."` to show the **schema shape**, not full data. Each script prints its JSON between sentinels (e.g. `===ODOO_BRIEF_START===` … `===ODOO_BRIEF_END===`); `odoo-ai` strips those and writes `<model>.<layer>.json`.

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
    "server_actions": [],
    "automated_actions": [
      {"id": 2, "name": "Auto-confirm web orders", "trigger": "on_create_or_write",
       "filter_domain": "[('state','=','draft')]", "active": true}
    ],
    "crons": []
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
    "official_addons": ["sale_stock", "sale"],
    "custom_addons_seen": [],
    "unknown_addons": [],
    "note": "Depend on the official module(s) that own the method ... Do NOT blindly depend on every custom addon in the chain ..."
  },
  "_warnings": [],
  "_caveat": "MRO is the POTENTIAL super() chain. Use has_super / super_position ..."
}
```

- `methods.<name>` is **MRO order**: index 0 may run first; `super()` descends 0 → 1 → …
- `fields.<name>.selection` lists the **actual `(value, label)` literals** for selection fields — stop guessing `state='confirmed'` when the real value is `'sale'`. A method-resolved selection shows `{"_dynamic": "method:_name"}`.
- Per-field `ondelete` / `inverse_name` / `domain` appear on relational fields; `index` / `copy` / `translate` / `tracking` / `has_default` round out the attributes AI most often gets wrong.
- `manifest_depends` splits the chain into `official_addons` vs `custom_addons_seen` (heuristic: module author) — depend on the official owner, not every custom addon you happened to traverse.
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
- `inheritance_chain` is the base view + every applied **extension** view in application order (parents first, siblings by `priority`). Before writing an xpath, inherit the right view from this list — the resolved arch alone hides which inheritors already touched a node. Render one specific view with `--view-xmlid`/`--view-id`.
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
  "total_addon_calls": 137, "total_sql": 88,
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
- `sql_count` is **cumulative including children**, so the depth-0 frame's count equals `total_sql`. A high count on a deep, frequently-called frame is your N+1 hotspot.
- `committed: false` = rolled back (default). `error` carries `"<ExcType>: msg"` if the method raised — the trace up to that point is still valid.
- `COMMIT=1` flips `committed` to `true` and **really persists** (`RELEASE SAVEPOINT` + `env.cr.commit()`) — throwaway/dev DB only.
