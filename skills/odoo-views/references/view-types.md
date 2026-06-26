# View types — per-type arch (v17/18)

All modifiers (`invisible` / `readonly` / `required` / `column_invisible`) take
**Python expressions**, not domains. Dump the resolved arch with the
`odoo-introspect` skill before extending an existing view.

## form

```xml
<form>
    <header>
        <button name="action_confirm" type="object" string="Confirm"
                class="btn-primary" invisible="state != 'draft'"/>
        <field name="state" widget="statusbar" statusbar_visible="draft,done"/>
    </header>
    <sheet>
        <div class="oe_button_box" name="button_box">
            <button class="oe_stat_button" type="object" name="action_view_loans"
                    icon="fa-book"><field name="loan_count" widget="statinfo"/></button>
        </div>
        <group>
            <group><field name="name"/></group>           <!-- two columns -->
            <group><field name="author"/></group>
        </group>
        <notebook>
            <page string="Lines" name="lines">
                <field name="line_ids"/>
            </page>
        </notebook>
    </sheet>
    <chatter/>                                              <!-- v18; v17: <div class="oe_chatter">…</div> -->
</form>
```

- `<header>` = action buttons + statusbar. `<sheet>` = the record body.
- `<group>` lays out 2 columns; nest two `<group>` for side-by-side.
- Smart buttons go in `<div class="oe_button_box">` with `oe_stat_button`.

## list (v18; `<tree>` ≤17)

```xml
<list editable="bottom" decoration-danger="amount &lt; 0" decoration-muted="state == 'done'">
    <field name="name"/>
    <field name="amount" sum="Total"/>
    <field name="discount" column_invisible="parent.no_discount"/>  <!-- hides whole column -->
    <field name="note" optional="hide"/>                            <!-- user-toggleable column -->
    <field name="sequence" widget="handle"/>                        <!-- drag to reorder -->
</list>
```

- `editable="bottom"` / `"top"` = inline edit, no dialog.
- `decoration-<style>="<expr>"`: `danger info warning success muted bf it`.
- `column_invisible` hides the **column**; plain `invisible` hides only the cell.
- `sum="…"` shows a column total; `optional="hide|show"` makes it toggleable;
  `widget="handle"` enables drag-reorder on an integer `sequence` field.
- `multi_edit="1"` on `<list>` allows editing a field across selected rows.

## kanban

```xml
<kanban>
    <field name="state"/>
    <templates>
        <t t-name="kanban-box">
            <div class="oe_kanban_card">
                <strong><field name="name"/></strong>
                <span t-if="record.state.raw_value == 'done'">✓</span>
            </div>
        </t>
    </templates>
</kanban>
```

- Body is QWeb: `t-name="kanban-box"`, `record.<field>.value` / `.raw_value`,
  `t-if`, `t-esc`, `t-att-*`. Declare fields used in JS via bare `<field>`.

## search

```xml
<search>
    <field name="name"/>
    <filter name="mine" string="My Books" domain="[('user_id','=',uid)]"/>
    <separator/>
    <filter name="available" string="Available" domain="[('state','=','available')]"/>
    <group expand="0" string="Group By">
        <filter name="by_state" string="State" context="{'group_by':'state'}"/>
    </group>
    <searchpanel>
        <field name="category_id" icon="fa-folder" enable_counters="1"/>
    </searchpanel>
</search>
```

- `<field>` = searchable; `<filter domain=…>` = predefined filter;
  `<filter context="{'group_by':'…'}">` = group-by toggle.
- `<searchpanel>` = left-hand category/many2one drill-down (list/kanban).

## pivot / graph

```xml
<pivot>
    <field name="category_id" type="row"/>
    <field name="amount" type="measure"/>
</pivot>

<graph type="bar">                 <!-- bar | line | pie -->
    <field name="category_id"/>
    <field name="amount" type="measure"/>
</graph>
```

## calendar / activity / gantt

```xml
<calendar date_start="date_due" color="user_id" mode="month">
    <field name="name"/>
</calendar>
```

- `<calendar>`: needs `date_start` (and optional `date_stop`); `color` buckets
  records by a field; `mode="day|week|month|year"`.
- `<activity>`: activity-centric grid for `mail.activity.mixin` models.
- `<gantt>`: **Enterprise only** — `date_start` / `date_stop`, `default_group_by`.
