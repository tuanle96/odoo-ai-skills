---
name: odoo-views
description: >-
  Authoring or editing Odoo view XML — form, list, kanban, search, pivot, graph,
  calendar, activity, gantt — and view inheritance (inherit_id, xpath, position,
  <attribute>). Use whenever writing a view, adding a field/button/page/filter to
  an existing view, fixing field visibility, or migrating old view XML, even if
  the user never says "skill". Covers the v17/18 breaking syntax LLMs get wrong:
  `attrs` and `states` are REMOVED — use direct `invisible=` / `readonly=` /
  `required=` / `column_invisible=` with Python expressions; the list root is
  `<list>` (was `<tree>`); chatter is `<chatter/>`. Before editing any view, dump
  the inheritance-resolved arch + existing buttons/modifiers with the
  `odoo-introspect` skill (entrypoints) so your xpath targets actually exist — a
  wrong xpath fails silently.
---

# Odoo views

The view you edit is **not** the view that renders. Odoo merges the base arch with every inheriting view across the addon graph at `get_view()` time. An xpath that doesn't match the *resolved* arch silently no-ops — the field you "added" never appears and nothing is logged.

**Read ground truth first.** Run the `odoo-introspect` skill's **entrypoints (Layer B)** on the model: it dumps the inheritance-resolved arch, the buttons (which method/action each fires), and the view-level field modifiers (readonly/invisible/required). Now your xpath targets exist and you won't duplicate a field.

**Version floor: Odoo 17/18, through Odoo 19 (current LTS).** Pre-17 deltas and the v18.1 → 19 changes → `skills/odoo-introspect/references/version-matrix.md`.

## v17/18 breaking syntax — get this right

| Old (≤16) | Current | Since |
|---|---|---|
| `attrs="{'invisible': [('state','=','done')]}"` | `invisible="state == 'done'"` | **17.0** |
| `attrs` for readonly/required | `readonly="<expr>"` / `required="<expr>"` | **17.0** |
| `states="draft,sent"` | `invisible="state not in ['draft','sent']"` | **17.0** |
| `invisible` to hide a **list column** | `column_invisible="<expr>"` (plain `invisible` hides only the cell) | 17.0 |
| `<tree>` root + `view_mode="tree,form"` | `<list>` root + `view_mode="list,form"` | **18.0** |
| `<div class="oe_chatter"><field …/></div>` | `<chatter/>` (self-closing; `<chatter>…fields…</chatter>` to customize) | **18.0** |
| `t-esc` / `t-raw` in kanban/QWeb templates | `t-out` (escapes by default; `t-raw` **removed**) | **17.0** |

`attrs`/`states` in a v17+ view is a **hard parse error** at load. `<tree>` in v18 raises `ValueError: Wrong value for ir.ui.view.type: 'tree'`. Modifier values are **Python expressions** over field values + `parent` / `context` / `uid` — not domains.

```xml
<field name="commitment_date" invisible="state == 'done'" readonly="state != 'draft'"/>
<field name="discount" column_invisible="parent.pricelist_id == False"/>
<button name="action_confirm" type="object" invisible="state != 'draft'"/>
```

## View types

| Tag | Use | Key attrs / contents |
|---|---|---|
| `<form>` | one record | `<header>` · `<sheet>` · `<chatter/>` |
| `<list>` | rows (v18; `<tree>` ≤17) | `editable="bottom"`, `decoration-*`, `column_invisible`, `optional` |
| `<kanban>` | cards | `<templates>` + QWeb (`t-esc`, `t-if`) |
| `<search>` | filter bar | `<field>`, `<filter>`, `<group expand="1">`, `<searchpanel>` |
| `<pivot>` / `<graph>` | analysis | `<field type="measure">`, graph `type=` |
| `<calendar>` / `<activity>` / `<gantt>` | time / scheduling | `date_start=`; gantt is Enterprise |

## Inheritance — extend, don't fork

```xml
<record id="view_order_form_mycustom" model="ir.ui.view">
    <field name="name">sale.order.form.mycustom</field>
    <field name="model">sale.order</field>
    <field name="inherit_id" ref="sale.view_order_form"/>   <!-- extension mode -->
    <field name="arch" type="xml">
        <xpath expr="//field[@name='partner_id']" position="after">
            <field name="x_priority"/>
        </xpath>
    </field>
</record>
```

- **Extension** (default — `inherit_id` set, no `mode`): patches the parent in place; ships with the module's `data`.
- **Primary** (`mode="primary"` + `inherit_id`): a new standalone view *derived from* the parent — for a second form, or a child view referenced by an action.
- `position`: `after` | `before` | `inside` | `replace` | `attributes` (wrap `<attribute name="…">value</attribute>`).
- **Field shorthand** (no xpath): `<field name="partner_id" position="after">…</field>` targets the first matching node.

Full mechanics + every position with examples → `references/view-inheritance.md`.

## Common elements

- **Buttons**: `type="object"` (calls a model method) or `type="action"` (runs an `ir.actions.*`). Confirm the target exists via entrypoints before wiring.
- **decoration-***: `decoration-danger="amount_total < 0"` colors a list row by Python expr (`-info`, `-warning`, `-success`, `-muted`, `-bf`, `-it`).
- **Optional columns**: `<field … optional="hide|show"/>` — user-toggleable, off by default with `hide`.
- **statusbar**: `<field name="state" widget="statusbar" statusbar_visible="draft,done"/>` inside `<header>`.
- **chatter**: `<chatter/>` (v18) after `</sheet>`; model must inherit `mail.thread` / `mail.activity.mixin` (→ `odoo-dev`, `odoo-module-scaffold`).
- **search**: `<filter name="mine" string="Mine" domain="[('user_id','=',uid)]"/>` and group-by `<filter name="grp_state" context="{'group_by':'state'}"/>`.

## Gotchas that fail silently

- xpath doesn't match the **resolved** arch → no-op, no error. Dump the arch first (entrypoints).
- Adding a field already present in the view → "Field used twice" error, or a silent dedupe depending on placement.
- `priority` among competing inheriting views changes apply order → your patch lands, then a higher-priority sibling overwrites it.
- `position="replace"` on a node other views also target → their xpath now misses → a cascade of silent no-ops.
- Editing arch on an installed DB without `-u <module>` (or `--dev=xml`) → the old arch is still served.
- Plain `invisible` on a list field where you meant to hide the **column** → the cell hides but the header stays.

## References

- `references/view-inheritance.md` — xpath positions, primary vs extension, `<attribute>` add/remove, priority, debugging a dead xpath.
- `references/view-types.md` — per-type arch (form/list/kanban/search/pivot/graph/calendar) with v18 syntax.
- `odoo-introspect` entrypoints (Layer B) — resolved arch + buttons + modifiers, before you edit.
- `odoo-module-scaffold` — where view files get registered in `__manifest__.py` `data`.
