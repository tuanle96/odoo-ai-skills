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

**Version floor: Odoo 17/18, through Odoo 19 (current LTS).** The snippets here are verified against the 18.0 source. v16/17 differ (`props.value`/`props.update` and 3-arg `patch` are v16; `useService("user"/"rpc")` is v17, replaced by direct imports in v18). v19 continues the OWL 2 component model and the v17 field-value API below, but Odoo keeps refining OWL internals between majors — **read the canonical widget source in the target version** rather than trusting these snippets verbatim. For older targets see `skills/odoo-introspect/references/version-matrix.md`.

## Discover before you code (introspect-first)

OWL "ground truth" is the JS/XML source in the installed addons plus the view/action wiring in the DB:

- **Wiring** → use the `odoo-introspect` skill. Its `entrypoints.py` reads the resolved `get_view()` arch — it tells you which view places `<field widget="x"/>` or `<widget name="y"/>`, and which window/client action is in scope. Its `metadata.py` lists menu + action records. Never guess the view or action tag.
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

## Where each API comes from (do not guess imports)

| Symbol | Import |
|--------|--------|
| `Component, xml, useState, useRef, onMounted, onWillStart, …` | `@odoo/owl` |
| `useService` | `@web/core/utils/hooks` |
| `registry` | `@web/core/registry` |
| `patch` | `@web/core/utils/patch` |
| `standardFieldProps` | `@web/views/fields/standard_field_props` |
| `user` (current user — **direct import in v18**, was a service in v17) | `@web/core/user` |
| `_t` (lazy translation) | `@web/core/l10n/translation` |

See `references/owl-components.md` for the component/template/hooks/services reference and a hello-world client action end-to-end; `references/owl-field-widgets.md` for a complete custom field widget and core-component patching.

## Gotchas that fail silently

- **Template not declared in an assets bundle** → component mounts, renders nothing, no error. JS *and* XML must be globbed into `web.assets_backend` (or `web.assets_frontend`) in `__manifest__['assets']`. The `web.assets_qweb` bundle was **removed in v16** — don't target it.
- **`t-name` ≠ `static template`** → "Missing template" only at mount. The name is `module.DottedName`; it must match exactly.
- **`owl="1"` is dead.** It was a v14/15 transitional marker. v17/18 backend templates are `<templates xml:space="preserve"><t t-name="module.X">` with **no** `owl` attribute. Adding it is cargo-culting; the real requirement is the bundle + matching name above.
- **Field value API changed in v17.** Read `props.record.data[props.name]`, write `props.record.update({[props.name]: v})`. `props.value` / `props.update` are **v16** and silently `undefined` in 17/18.
- **Mutating non-reactive state doesn't re-render.** Only `useState(...)` objects and `props` are reactive. Reassigning a plain `this.x` updates nothing on screen.
- **`t-foreach` needs `t-key`** in OWL 2 (mandatory, unlike server QWeb) or you get duplicate/stale DOM.
- **`t-raw` is removed** → use `t-out` (HTML-escapes; emits raw only for `markup()` values). `t-esc` still works but `t-out` is the v16+ default.
- **`this.orm.create` takes a list** (`create(model, [vals])`); `nameGet` was removed in v17 — use `webRead` / `read`.

## References & scripts

- `references/owl-components.md` — component anatomy, templates + directives, lifecycle hooks, services (orm/notification/dialog/action), assets manifest, hello-world client action.
- `references/owl-field-widgets.md` — full custom field widget (`standardFieldProps`, read/write, options), view widgets, and patching a core component.
- Introspection lives in the `odoo-introspect` skill (`entrypoints.py` for view/action wiring; `metadata.py` for menus/actions).
