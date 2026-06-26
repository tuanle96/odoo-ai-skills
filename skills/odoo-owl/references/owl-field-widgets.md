# Custom field widgets, view widgets & patching

Verified against Odoo 18.0 source. Version floor 17/18. **This is the #1 LLM-hallucination spot** — the field-prop API changed at v17. Read a real field under `web/static/src/views/fields/` before writing.

## standardFieldProps (verified 18.0)

A field component receives exactly these — nothing else by default:

```js
export const standardFieldProps = {
    id:       { type: String,  optional: true },
    name:     { type: String },                    // the field name on the record
    readonly: { type: Boolean, optional: true },
    record:   { type: Object },                     // the datapoint
};
```

There is **no `props.value` and no `props.update`** (those were v16). Read and write through `record`:

- **Read:**  `this.props.record.data[this.props.name]`
- **Write:** `this.props.record.update({ [this.props.name]: newValue })`
- **Field meta:** `this.props.record.fields[this.props.name]` (type, string, …)

## A complete custom field widget

Goal: a clickable star-rating input for an `integer` field, with a `max` option set from the view.

```js
/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { _t } from "@web/core/l10n/translation";

export class StarsField extends Component {
    static template = "my_module.StarsField";
    static props = {
        ...standardFieldProps,
        max: { type: Number, optional: true },   // declare every prop or OWL validation throws
    };
    static defaultProps = { max: 5 };

    get value() { return this.props.record.data[this.props.name] || 0; }   // READ
    get stars() { return Array.from({ length: this.props.max }, (_, i) => i + 1); }

    setValue(n) {
        if (this.props.readonly) return;
        this.props.record.update({ [this.props.name]: n });                // WRITE
    }
}

// The field descriptor — an OBJECT, not the bare component (v17+).
export const starsField = {
    component: StarsField,
    displayName: _t("Stars"),
    supportedTypes: ["integer"],
    supportedOptions: [
        { label: _t("Maximum"), name: "max", type: "number", default: 5 },
    ],
    // map the view's options="{...}" / attrs onto component props
    extractProps: ({ attrs, options }) => ({ max: options.max }),
    isEmpty: (record, fieldName) => !record.data[fieldName],
};
registry.category("fields").add("stars", starsField);
```

Template (`my_module.StarsField`, in the backend bundle):

```xml
<templates xml:space="preserve">
    <t t-name="my_module.StarsField">
        <span class="o_stars_field">
            <t t-foreach="stars" t-as="n" t-key="n">
                <i class="fa" t-att-class="n &lt;= value ? 'fa-star' : 'fa-star-o'"
                   t-on-click="() => this.setValue(n)"/>
            </t>
        </span>
    </t>
</templates>
```

Use it in a view (`widget` name = the registry key):

```xml
<field name="priority_score" widget="stars" options="{'max': 10}"/>
```

### Field descriptor keys

| Key | Purpose |
|-----|---------|
| `component` | the OWL component class (required) |
| `supportedTypes` | model field types it accepts, e.g. `["integer"]` |
| `displayName` | label in Studio / widget picker (`_t(...)`) |
| `supportedOptions` | `[{label, name, type, default}]` — declares `options="{…}"` keys |
| `extractProps` | `({attrs, options}, dynamicInfo) => props` — view → component props |
| `isEmpty` | `(record, fieldName) => bool` — drives "empty" styling |
| `fieldDependencies` | extra fields to fetch, e.g. `[{name:"currency_id", type:"many2one"}]` |

To **extend** a built-in widget instead of starting over, subclass its component and re-register under a new name (import the base from its source path).

## View widgets (non-field) — `<widget/>`

For chrome that isn't bound to one field (a banner, an aggregate button):

```js
registry.category("view_widgets").add("my_banner", {
    component: MyBanner,
    // extractProps optional; component gets props.record (the row) when in a list/form
});
```
```xml
<widget name="my_banner"/>
```

## Patching a core component (`@web/core/utils/patch`)

When you must change Odoo's own component, patch its prototype — do **not** fork the file. Signature is 2-arg in v17/18 (`patch(target, patchObject)`); the v16 3-arg `patch(target, name, patch)` is gone.

```js
/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);     // keep the original behaviour
        // your additions: hooks, state, services
    },
    async saveButtonClicked(params = {}) {
        // pre-logic …
        return super.saveButtonClicked(params);
    },
});
```

Gotchas:
- **Always call `super.method(...arguments)`** unless you deliberately replace it — skipping it silently drops core behaviour (autosave, breadcrumbs…).
- Patch the **`.prototype`** for instance methods. The patch applies to every instance, including already-mounted ones.
- A patch returns an **unpatch** function — keep it only if you need to revert (rare).
- Load order matters: your patch file must be in a bundle that loads after the patched module (it is, if you `depends` on its addon).
- To add a brand-new method, just include it in the patch object; to fully override, omit the `super` call (and accept the maintenance cost across versions — re-verify on upgrade).
