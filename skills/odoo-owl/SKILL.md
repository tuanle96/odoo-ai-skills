---
name: odoo-owl
description: >-
  Building or extending the Odoo 17/18/19 web client — OWL 2 components, custom
  field widgets (widget="..."), view widgets, client actions, systray items,
  patching core web components, ORM/notification/dialog/action services, and the
  assets bundles that load them. Use whenever writing JavaScript/XML under
  static/src, wiring a widget into a view, or debugging "my component renders
  nothing / props are undefined / the template isn't found". OWL is where LLM
  memory is most stale (v16→v17→v18 renamed half the field API) — do NOT write a
  hook, service, registry category, or field-prop name from memory. Read the real
  addon source first, then write.
---

# Odoo web frontend (OWL 2)

Odoo 17/18/19's web client is **OWL 2** (Odoo's own React-like framework) — not jQuery, not the legacy `web.widget`. The frontend has the same failure mode as the backend: the APIs you "remember" are wrong because they were renamed across versions, and the wiring (which view uses your widget, which bundle loads your file) lives in **this** instance, not in your head.

**The rule: read the canonical source for the thing you're extending, then write the smallest component.** Odoo ships the reference implementation of every field widget under `web/static/src/views/fields/`. Read one before you write one.

**Version floor: Odoo 17/18, through Odoo 19 (current LTS).** Snippets are verified against 18.0; v19 keeps the OWL 2 model and the v17 field-value API. The renames AI gets wrong are tabled next — but Odoo keeps refining OWL internals between majors, so **read the canonical widget source in the target version**, don't trust a snippet verbatim. Older targets → `skills/odoo-introspect/references/version-matrix.md`.

## The version churn AI gets wrong (v16 → v19)

Most training data is v15–v16, so these renames are the silent-failure zone: the old form often still parses and just does nothing. Emit the current form and confirm against the target instance.

| Concern | Old (≤ prior) | Current (v17/18/19) | Since |
|---|---|---|---|
| Field value — **read** | `props.value` | `props.record.data[props.name]` | **17.0** |
| Field value — **write** | `props.update(v)` | `props.record.update({ [props.name]: v })` | **17.0** |
| Field registration | `.add("x", MyComponent)` (bare class) | `.add("x", { component, supportedTypes, … })` (descriptor **object**) | **17.0** |
| `patch()` | `patch(T, "name", {...})` (3-arg) | `patch(T.prototype, {...})` (2-arg) | **17.0** |
| `user` / `rpc` | `useService("user")` / `useService("rpc")` (v17) | direct `import { user }` / `import { rpc }` | **18.0** |
| Lifecycle | `mounted()` / `willStart()` methods | `onMounted(…)` / `onWillStart(…)` hooks | **OWL 2** |
| QWeb raw output | `t-raw` | `t-out` (`t-raw` **removed**; `t-esc` superseded) | **17.0** |

Full cross-suite matrix (incl. v18.1/18.2 deltas) → `skills/odoo-introspect/references/version-matrix.md`.

## Discover before you code (introspect-first)

OWL "ground truth" is the JS/XML source in the installed addons plus the view/action wiring in the DB:

- **Wiring** → run the `odoo-introspect` skill's `odoo-ai entrypoints <model>`: it reads the resolved `get_view()` arch — which view places `<field widget="x"/>` or `<widget name="y"/>`, and which window/client action is in scope. `odoo-ai brief <model>` + `metadata` list the model fields and the menu/action records. Never guess the view or action tag.
- **The API you're extending** → read the actual file: `grep -rl 'category("fields").add' <addons>/web/static/src/views/fields/` and open the closest field to what you need. Odoo's own field/view/service implementations are the contract — match their imports and prop shapes rather than recalling them.

## What you're building → where it registers

| Goal | Registry / mechanism | Referenced from |
|------|----------------------|-----------------|
| Custom field input | `registry.category("fields").add("name", {component, supportedTypes})` | `<field widget="name"/>` |
| Non-field view widget | `registry.category("view_widgets").add("name", {component})` | `<widget name="name"/>` |
| Full-screen client action | `registry.category("actions").add("tag", Component)` | `ir.actions.client` tag |
| Systray icon | `registry.category("systray").add("name", {Component})` | auto |
| Top-level singleton (overlay) | `registry.category("main_components").add(...)` | auto |
| Modify a core component | `patch(Comp.prototype, {...})` from `@web/core/utils/patch` | — |

## Custom field widget — the part AI gets wrong

Register a **descriptor object** (not the bare component, v17+) in the `fields` category; reference it from a view by `widget="name"`. Read and write the value **through `record`** — `props.value` / `props.update` are v16 and silently `undefined` now.

```js
/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class MyField extends Component {
    static template = "my_module.MyField";
    static props = { ...standardFieldProps };                       // declare EVERY prop or OWL validation throws
    get value() { return this.props.record.data[this.props.name]; }              // READ
    update(v) { return this.props.record.update({ [this.props.name]: v }); }     // WRITE
}
registry.category("fields").add("my_widget", {                      // an OBJECT, not MyField
    component: MyField,
    supportedTypes: ["char"],
});
```

A view then uses it as `<field name="ref" widget="my_widget"/>`. Full pattern — template, `options="{…}"`, `extractProps`, the common mistakes, and the per-version deltas → `references/field-widget-patterns.md`.

## Where each API comes from (do not guess imports)

| Symbol | Import |
|--------|--------|
| `Component, xml, useState, useRef, onMounted, onWillStart, …` | `@odoo/owl` |
| `useService` | `@web/core/utils/hooks` |
| `registry` | `@web/core/registry` |
| `patch` | `@web/core/utils/patch` |
| `standardFieldProps` | `@web/views/fields/standard_field_props` |
| `user` (current user — **direct import in v18**, was a service in v17) | `@web/core/user` |
| `rpc` (low-level RPC — **direct import in v18**, was a service in v17) | `@web/core/network/rpc` |
| `_t` (lazy translation) | `@web/core/l10n/translation` |

## Services & `useService`

`useService("name")` (from `@web/core/utils/hooks`) returns a service instance auto-cancelled when the component unmounts. The four you reach for: `orm` (model RPC — prefer over raw `rpc`), `action` (`this.action.doAction(...)`), `notification` (`this.notification.add(msg, {type})`, type ∈ `success|warning|danger|info`), `dialog` (`this.dialog.add(Comp, props, {onClose})`). **v18 moved `user` and `rpc` off the service registry — import them directly** (see the import table); calling `useService("user"/"rpc")` is v17 and returns `undefined` on 18+. Full service list + verified ORM signatures → `references/owl-components.md`.

## Patching a core component

To change Odoo's own component, patch its **`.prototype`** — don't fork the file. v17/18 is the **2-arg** form `patch(target, patchObject)`; the v16 3-arg `patch(target, name, patch)` is gone. Always chain `super`:

```js
/** @odoo-module **/
import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
patch(FormController.prototype, {
    setup() { super.setup(...arguments); /* your hooks / state / services */ },
});
```

Skipping `super.method(...arguments)` silently drops core behaviour (autosave, breadcrumbs). The patch applies to every instance, including already-mounted ones. Load order matters — `depends` on the addon you patch so your file loads after it. More → `references/owl-field-widgets.md`.

## Assets — wire JS *and* XML into a bundle

A component whose template isn't in a bundle mounts and renders **nothing, no error**. Glob **both** JS and XML into a bundle in `__manifest__['assets']` — a dict since v15, **not** an `<template inherit_id="web.assets_backend">`, and **not** `__manifest__['data']`:

```python
"assets": {
    "web.assets_backend": [                    # logged-in web client; web.assets_frontend = public site
        "my_module/static/src/**/*.js",
        "my_module/static/src/**/*.xml",
        "my_module/static/src/**/*.scss",
    ],
},
```

`web.assets_qweb` was **removed in v16** — don't target it. Asset XML ≠ data XML (asset templates aren't loaded via `<data>`). End-to-end manifest + client action → `references/owl-components.md`.

## Gotchas that fail silently

- **`t-name` ≠ `static template`** → "Missing template" only at mount. The name is `module.DottedName`; it must match `static template` exactly.
- **`owl="1"` is dead.** It was a v14/15 transitional marker. v17/18 backend templates are `<templates xml:space="preserve"><t t-name="module.X">` with **no** `owl` attribute. The real requirement is the bundle + matching name, not that attribute.
- **Mutating non-reactive state doesn't re-render.** Only `useState(...)` objects and `props` are reactive. Reassigning a plain `this.x` updates nothing on screen.
- **`t-foreach` needs `t-key`** in OWL 2 (mandatory, unlike server QWeb) or you get duplicate/stale DOM.
- **`this.orm.create` takes a list** (`create(model, [vals])`), not a single dict.

## References & scripts

- `references/owl-components.md` — component anatomy, templates + directives, lifecycle hooks, services (orm/notification/dialog/action) + ORM signatures, assets manifest, hello-world client action.
- `references/owl-field-widgets.md` — full custom field widget (`standardFieldProps`, read/write, options), view widgets, and patching a core component.
- `references/field-widget-patterns.md` — a minimal correct v18/19 field widget end-to-end (component + template + registration + assets), the common mistakes, and the v16→17→18→19 deltas.
- Introspection lives in the `odoo-introspect` skill (`odoo-ai entrypoints` for view/action wiring; `odoo-ai brief` / `metadata` for fields/menus/actions).
