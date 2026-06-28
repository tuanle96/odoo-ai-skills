# Field widget patterns (v18/19) — minimal correct, the mistakes, the deltas

Verified against Odoo 18.0 source; floor 17/18, valid on 19. **Field widgets are the #1 OWL hallucination spot** — the prop and registration API changed at v17. Always open a real field under `web/static/src/views/fields/` (e.g. `char/char_field.js`) before writing. This file is the end-to-end skeleton + the traps + the per-version deltas; the star-rating worked example and the descriptor-key table are in `owl-field-widgets.md`.

## A field widget is four wired pieces

| Piece | What | Lives in |
|-------|------|----------|
| Component | OWL class that reads/writes **through `record`** | `static/src/fields/my_field.js` |
| Template | `<t t-name="module.MyField">` matching `static template` | `static/src/fields/my_field.xml` |
| Descriptor + registration | a **plain object** added to `registry.category("fields")` | same JS file |
| Assets | JS **and** XML globbed into a bundle | `__manifest__['assets']` |

Miss any one and it fails silently: no widget, or "Missing template", or it mounts and renders nothing.

## 1. Component (`fields/char_counter_field.js`)

A `char`/`text` field with a live remaining-characters counter and a `max_length` option from the view.

```js
/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { _t } from "@web/core/l10n/translation";

export class CharCounterField extends Component {
    static template = "my_module.CharCounterField";
    static props = {
        ...standardFieldProps,                  // id, name, readonly, record
        maxLength: { type: Number, optional: true },   // declare EVERY extra prop or OWL throws at mount
    };
    static defaultProps = { maxLength: 0 };

    get value() {                               // READ — never props.value (that's v16, undefined now)
        return this.props.record.data[this.props.name] || "";
    }
    get remaining() {
        return this.props.maxLength ? this.props.maxLength - this.value.length : null;
    }
    onInput(ev) {                               // WRITE — never props.update
        this.props.record.update({ [this.props.name]: ev.target.value });
    }
}

// The field descriptor is an OBJECT (v17+), not the bare component.
export const charCounterField = {
    component: CharCounterField,
    displayName: _t("Char with counter"),
    supportedTypes: ["char", "text"],
    supportedOptions: [
        { label: _t("Max length"), name: "max_length", type: "number" },
    ],
    // view options="{...}" / attrs → component props
    extractProps: ({ options }) => ({ maxLength: options.max_length || 0 }),
    isEmpty: (record, fieldName) => !record.data[fieldName],
};
registry.category("fields").add("char_counter", charCounterField);
```

## 2. Template (`fields/char_counter_field.xml`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">
    <t t-name="my_module.CharCounterField">
        <div class="o_field_char_counter d-inline-flex align-items-center gap-1">
            <input type="text" class="o_input" t-att-value="value"
                   t-att-readonly="props.readonly" t-on-input="onInput"/>
            <small t-if="remaining !== null" class="text-muted" t-out="remaining"/>
        </div>
    </t>
</templates>
```

No `owl="1"`. The name `my_module.CharCounterField` must equal `static template` exactly. `t-att-readonly` removes the attribute when `props.readonly` is falsy (OWL `t-att-*` semantics).

## 3. Registration — the descriptor object (v17+)

Registration is the `registry.category("fields").add("char_counter", charCounterField)` line above. The **key** (`"char_counter"`) is what a view's `widget="..."` resolves to. Registering the bare component (`add("char_counter", CharCounterField)`) is the v16 shape and resolves to nothing on 17+. Descriptor keys (`supportedTypes`, `extractProps`, `isEmpty`, `fieldDependencies`, …) → `owl-field-widgets.md`.

## 4. Assets (`__manifest__.py`)

Both files must be globbed into a bundle — the component JS *and* the template XML:

```python
{
    "name": "My Module",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [                 # backend client; web.assets_frontend for the public site
            "my_module/static/src/fields/**/*.js",
            "my_module/static/src/fields/**/*.xml",
            "my_module/static/src/fields/**/*.scss",
        ],
    },
    "data": ["views/my_view.xml"],              # the VIEW xml goes in data, NOT the asset template
}
```

Asset XML (the `<t t-name>` template) is loaded by the **bundle**, never by `__manifest__['data']`. `web.assets_qweb` was removed in v16 — don't target it.

## Use it in a view

```xml
<field name="summary" widget="char_counter" options="{'max_length': 280}"/>
```

The `widget` value is the registry key; `options` keys must match `supportedOptions[].name` and are delivered to the component via `extractProps`.

## The field-value API — always through `record`

`standardFieldProps` (verified 18.0) gives the component exactly `{ id?, name, readonly?, record }` — **no `value`, no `update`**. Three access patterns:

- **Read:**  `this.props.record.data[this.props.name]`
- **Write:** `this.props.record.update({ [this.props.name]: v })` — returns a promise; the view's save awaits it
- **Meta:**  `this.props.record.fields[this.props.name]` (`type`, `string`, …)

## Common mistakes (each fails silently or only at mount)

- **Registering the bare component** instead of the descriptor object → `widget="x"` resolves to nothing. (v16 → v17 change.)
- **Reading `props.value` / writing `props.update(v)`** → `undefined`; the field shows blank and never persists. (v16 API.)
- **Custom prop not declared in `static props`** → OWL prop-validation error at mount.
- **`extractProps` returns a prop you didn't declare** (or a declared prop it never sets) → validation throws / the option is silently ignored.
- **Template name ≠ `static template`** → "Missing template: my_module.CharCounterField" at mount.
- **XML not in the bundle** → component mounts, renders nothing, no error.
- **`t-foreach` without `t-key`** → duplicated/stale DOM (mandatory in OWL 2).
- **Mutating a plain `this.x` for display** → no re-render; only `useState`/`props`/`record` are reactive.
- **Omitting `supportedTypes`** → your test view works, but the widget is offered for the wrong field types (or none) elsewhere.

## Field-widget version deltas (v16 → v19)

| Concern | Old (≤ v16) | Current (v17/18/19) | Since |
|---------|-------------|---------------------|-------|
| Read the value | `props.value` | `props.record.data[props.name]` | **17.0** |
| Write the value | `props.update(v)` | `props.record.update({ [props.name]: v })` | **17.0** |
| Register | `.add("x", Component)` | `.add("x", { component, supportedTypes, … })` | **17.0** |
| Base props | ad-hoc prop list | spread `...standardFieldProps` | **17.0** |
| Option mapping | legacy `attrs` parsing | `extractProps({ attrs, options }, dynamicInfo)` | **17.0** |
| `user` / `rpc` inside the widget | `useService("user"/"rpc")` (v17) | direct `import` (`@web/core/user`, `@web/core/network/rpc`) | **18.0** |

v19 keeps the v17/18 field API above; confirm against the target version's `web/static/src/views/fields/` source rather than trusting this verbatim. Cross-suite matrix → `skills/odoo-introspect/references/version-matrix.md`.

## Extend a built-in instead of starting over

To tweak a stock widget, subclass its component and re-register — don't reimplement it. Both the class and its descriptor are exported from the source path:

```js
/** @odoo-module **/
import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";

export class MyCharField extends CharField {
    // override one getter/method; keep the rest of CharField
}
registry.category("fields").add("my_char", { ...charField, component: MyCharField });
```

Spreading `...charField` preserves the base descriptor (`supportedTypes`, `extractProps`, …) and only swaps the component. Re-verify the import path and exports against the target version — Odoo moves field source between minors.
