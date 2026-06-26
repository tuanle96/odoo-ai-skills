# OWL 2 components, templates, hooks, services

Verified against Odoo 18.0 source. Version floor 17/18.

## Component anatomy

```js
/** @odoo-module **/
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export class Counter extends Component {
    static template = "my_module.Counter";      // must match a <t t-name> in an asset XML
    static props = { start: { type: Number, optional: true } };
    static defaultProps = { start: 0 };
    static components = {};                        // child component classes used in the template

    setup() {                                     // the constructor — no business logic in render
        this.orm = useService("orm");
        this.state = useState({ value: this.props.start });   // ONLY useState is reactive
        onWillStart(async () => {
            this.partners = await this.orm.searchRead("res.partner", [], ["name"]);
        });
    }
    increment() { this.state.value++; }
}
```

Reactivity: a re-render is triggered only by mutating a `useState` object or by new `props`. Reassigning a plain instance field (`this.x = …`) changes nothing on screen.

## Template file

```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">
    <t t-name="my_module.Counter">
        <div class="o_counter">
            <button t-on-click="increment">+</button>
            <span t-out="state.value"/>
        </div>
    </t>
</templates>
```

No `owl="1"`. The template name is `module.Name`; it must equal `static template`. The file must be in an assets bundle (see below).

### Directives

| Directive | Use |
|-----------|-----|
| `t-if` / `t-elif` / `t-else` | conditional render (`t-else=""` needs a value attr) |
| `t-foreach="coll" t-as="item" t-key="item.id"` | loop — **`t-key` is mandatory in OWL** |
| `t-on-click`, `t-on-keydown`, … | DOM events; `t-on-click.stop` / `.prevent` modifiers |
| `t-att-href="expr"` / `t-attf-class="a {{x}}"` | dynamic attr (eval) / format-string attr |
| `t-out="expr"` | output, HTML-escaped (raw only for `markup()`); **replaces removed `t-raw`** |
| `t-esc="expr"` | output, always escaped (still valid; `t-out` preferred) |
| `t-set="x" t-value="expr"` | local variable |
| `t-call="module.Other"` | render another template inline |
| `t-slot="name"` / `t-set-slot="name"` | render a slot / supply slot content from parent |
| `t-model="state.field"` | two-way bind an `<input>`/`<select>` to state |
| `t-ref="name"` | mark element for `useRef("name")` |

## Hooks & lifecycle (from `@odoo/owl`)

| Hook | Fires |
|------|-------|
| `useState(obj)` | wrap obj in a reactive proxy |
| `useRef("name")` | handle to a `t-ref` DOM node / child component (`ref.el`) |
| `onWillStart(async () => …)` | once, before first render — load initial data here |
| `onMounted(() => …)` | after DOM is inserted (focus, measure, 3rd-party libs) |
| `onWillUpdateProps(async (next) => …)` | parent passed new props, before re-render |
| `onWillUnmount(() => …)` | before removal from DOM (teardown listeners/timers) |
| `onWillDestroy`, `onWillRender`, `onRendered`, `onPatched` | finer-grained steps |
| `useSubEnv(obj)` / `useChildSubEnv(obj)` | extend `env` for self+children / children only |

OWL 2 removed the old *method* lifecycle (`mounted()`, `willStart()`); use the `on*` hooks only.

## Services & registries

`useService("name")` returns a service instance, auto-cancelled when the component unmounts. Import: `@web/core/utils/hooks`.

| Service | Typical use |
|---------|-------------|
| `orm` | model RPC (read/write/call) — prefer over raw rpc |
| `action` | `this.action.doAction(xmlIdOrAction)`, `doActionButton`, `loadState` |
| `notification` | `this.notification.add(msg, {title, type, sticky, buttons})` — type ∈ `success\|warning\|danger\|info` |
| `dialog` | `this.dialog.add(DialogComponent, props, {onClose})` → returns a `close()` fn |

**v18 moved two off the service registry — import them directly:**

```js
import { user } from "@web/core/user";          // was useService("user") in v17
import { rpc }  from "@web/core/network/rpc";    // was useService("rpc")  in v17
// user.userId, user.context, user.hasGroup("base.group_system")
// await rpc("/my/route", { key: 1 })
```

The central `registry` (`@web/core/registry`) holds categories: `fields`, `view_widgets`, `main_components`, `services`, `actions`, `systray`, `views`, `formatters`, `parsers`. Add with `registry.category("x").add("key", value, {force, sequence})`.

## ORM service (verified 18.0 signatures)

```js
this.orm = useService("orm");
await this.orm.read(model, ids, fields, kwargs);
await this.orm.search(model, domain, kwargs);
await this.orm.searchRead(model, domain, fields, kwargs);   // kwargs: {limit, offset, order, context}
await this.orm.searchCount(model, domain, kwargs);
await this.orm.readGroup(model, domain, fields, groupby, kwargs);
await this.orm.create(model, [vals1, vals2]);               // records is a LIST
await this.orm.write(model, ids, vals);
await this.orm.unlink(model, ids);
await this.orm.call(model, method, args, kwargs);           // any public method
await this.orm.webRead(model, ids, kwargs);                 // replaces removed nameGet
```

`this.orm.silent.call(...)` suppresses the global RPC error dialog. Pass `context` inside `kwargs`.

## Hello-world client action (end to end)

1. JS — component + register the tag:
```js
/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";

class AwesomeDashboard extends Component {
    static template = "awesome.Dashboard";
}
registry.category("actions").add("awesome.dashboard", AwesomeDashboard);
```
2. XML template (same module, in the backend bundle):
```xml
<templates xml:space="preserve">
    <t t-name="awesome.Dashboard"><div class="p-3">Hello dashboard</div></t>
</templates>
```
3. `ir.actions.client` record so a menu can open it:
```xml
<record id="action_awesome_dashboard" model="ir.actions.client">
    <field name="name">Dashboard</field>
    <field name="tag">awesome.dashboard</field>
</record>
<menuitem id="menu_awesome_dashboard" name="Dashboard" action="action_awesome_dashboard"/>
```
4. Manifest — load the assets (JS **and** XML):
```python
"assets": {
    "web.assets_backend": [
        "awesome/static/src/**/*.js",
        "awesome/static/src/**/*.xml",
        "awesome/static/src/**/*.scss",
    ],
},
```
Bundles: `web.assets_backend` (logged-in web client), `web.assets_frontend` (public website), `web.assets_common`. Order follows manifest/`depends`; use an `ir.asset` record to inject before/after a specific path. Asset XML ≠ data XML — asset templates are not loaded via `<data>` in `__manifest__['data']`.
