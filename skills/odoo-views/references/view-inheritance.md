# View inheritance mechanics (v17/18)

The final arch is the base view + every view whose `inherit_id` points at it (or
at one of its children), applied in order. You patch that merged tree, not the
file you see. **Dump the resolved arch with the `odoo-introspect` skill
(entrypoints / `get_view()`) before writing an xpath** — that is the only
reliable source of what nodes exist.

## xpath anatomy

```xml
<xpath expr="//field[@name='partner_id']" position="after">
    <field name="x_ref"/>
</xpath>
```

- `expr` is XPath 1.0 over the resolved arch. Anchor on something stable:
  `//field[@name='x']`, `//button[@name='action_confirm']`, `//page[@name='…']`,
  `//group[@name='…']`, `//xpath` predicates like `//notebook/page[2]`.
- Prefer `@name` predicates over positional indexes — indexes shift when another
  module inserts a node above yours.

## `position` values

| `position` | Effect |
|---|---|
| `after` | insert children immediately after the matched node |
| `before` | insert immediately before |
| `inside` | append children inside the matched node (default if omitted) |
| `replace` | replace the matched node with the children (empty body → delete) |
| `attributes` | change attributes of the matched node (see below) |
| `move` | relocate an existing node (as a child of another xpath) |

## Changing attributes

```xml
<xpath expr="//field[@name='partner_id']" position="attributes">
    <attribute name="readonly">1</attribute>
    <attribute name="invisible">state == 'done'</attribute>
    <attribute name="string">Customer</attribute>
</xpath>
```

- Empty body removes the attribute: `<attribute name="readonly"/>`.
- For space-separated attrs (e.g. `class`) add/remove tokens without clobbering:

```xml
<attribute name="class" add="o_my_flag" remove="o_old" separator=" "/>
```

## Field shorthand (no `<xpath>`)

Targets the **first** node matching that field name in the resolved arch:

```xml
<field name="partner_id" position="after">
    <field name="x_ref"/>
</field>
<page name="extra" position="inside">…</page>
```

Convenient, but ambiguous when the field appears more than once (e.g. in a list
*and* a form section) — use an explicit `<xpath>` with a predicate then.

## Primary vs extension

| | `inherit_id` | `mode` | Result |
|---|---|---|---|
| **Extension** | set | omitted (=`extension`) | patches parent in place; every view of that model gets it |
| **Primary** | set | `primary` | new standalone view *derived from* parent; used only where referenced (action, child view, `<field mode="…">` in a one2many) |

Use primary for a second form/list of the same model that must differ from the
default, or for an embedded one2many sub-view. Use extension (the common case)
to add fields/buttons to the existing default view.

## Priority — who wins, who applies last

- `ir.ui.view.priority` defaults to **16**. For the view an action shows by
  default, the **lowest** priority among candidates wins.
- Among sibling views inheriting the same parent, application order is by
  priority then id. A later-applied, higher-priority sibling can overwrite an
  attribute you set — if your change "doesn't stick", check for a competing
  inheriting view and lower your priority below it.

## Debugging a dead xpath

1. The symptom: no error, your field/button just isn't there.
2. Dump the **resolved** arch for the model via the `odoo-introspect` skill
   (entrypoints, Layer B) — or in dev, open the view and use *Edit View* /
   `get_view()` in a shell. Confirm the node your `expr` targets actually exists
   *after* merging (another module may have renamed/removed it).
3. Loosen the predicate (`//field[@name='x']` → check the name is exact and the
   field isn't behind an `<xpath position="replace">` from another module).
4. Re-apply with `-u <module>` (or `--dev=xml`); a stale install serves the old
   arch and masks the fix.

## Activating / deactivating a view

A view can be toggled with `<field name="active" eval="False"/>` on its record,
or via `inherit_id` + `mode="primary"` to supersede. Don't delete core views —
deactivate or extend.
