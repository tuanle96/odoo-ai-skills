---
name: odoo-introspect
description: >-
  Read Odoo ground truth from the running instance BEFORE writing any
  customization — field inventory, method resolution order (MRO) + super() chain,
  view/button wiring, security (ACL + record rules), the auto-trigger surface, and
  the real runtime call graph. Use this FIRST whenever you'd otherwise GUESS at
  Odoo internals: which fields already exist, what a method's super() chain is,
  where a form button leads, which record rules apply, what `_get_report_values`
  feeds a report, or "what actually fires when I confirm this order". Odoo composes
  each model at runtime from the installed addon dependency graph, so none of this
  is knowable from memory or source-grep — only from the live registry. Produces
  four JSON layers via `odoo-bin shell` scripts, or one `odoo-ai all <model>`
  command; RPC fallback for Odoo Online/SaaS. The foundation every other Odoo skill
  builds on. Version floor: Odoo 17/18, through Odoo 19 (current LTS).
---

# Odoo introspection — read ground truth, then customize

Odoo composes each model class at runtime from the installed addon dependency graph. The field list, the MRO, the `super()` chain, the view layout, the security rules, the automations that fire on write, the report parser — none are reliably knowable from memory or source-grep. They exist only in **this** running instance. Guessing is the root cause of "half-working" customizations that break elsewhere.

**The rule: read ground truth from the running registry first, then customize. Never guess.**

## Three different "orders" — do not conflate them

1. **Module load order** — from `depends` in `__manifest__.py`. Determines what exists when the registry builds. Override `sale.order.action_confirm` while depending only on `sale` (not `sale_stock`) and your override lands at a *different MRO layer* than intended.
2. **Method resolution order (MRO)** — the class chain of the final registry model. The **potential** `super()` path, **not** a guarantee of what runs. An override that skips `super()` cuts the chain; an early `return` under a context flag skips the rest. Layer A reports `has_super` / `super_position` / `returns_before_super` (heuristics) so you can judge.
3. **Runtime call order** — what actually executes on a click/cron: onchange → constrains → method → procurement → stock moves → invoice hooks → automations → recomputes. A **graph across many models**, not a list. Static analysis can't reconstruct it. Trace it (Layer D).

## The four ground-truth layers

| Layer | Script | Answers | Run when |
|-------|--------|---------|----------|
| **A** | `model_brief.py` | fields (+ selection literals, `ondelete`/`inverse_name`/`domain`, index/copy/tracking; + which modules touched each), MRO + super analysis, security (ACL + record rules), auto-triggers, recommended `depends` (official vs custom split) | **Always** |
| **B** | `entrypoints.py` | form/list buttons (→ which method/action), view modifiers (readonly/invisible/required), **view inheritance chain** (base + applied extensions by priority), window actions, reports (quick) | a button / view / action / xpath is in scope |
| **C** | `metadata.py` | menu graph (navigation paths), seeded `ir.model.data` + `noupdate` records, **deep** report wiring (QWeb templates + paperformat + parser) | navigation, seeded data, or a report is in scope |
| **D** | `trace_flow.py` | the **real** runtime call sequence + SQL across addons (executes; rolls back by default) | any sizable flow (sale/stock/account/mrp) — MRO alone isn't enough |

Two more focused scanners cover questions the four layers don't:

| Scanner | Script | Answers | Run when |
|---------|--------|---------|----------|
| **E (refs)** | `field_refs.py` | reverse impact — every compute/related/view/rule/filter/action that **depends on a field** (so a rename/retype/drop covers all of them) | before renaming, retyping, or dropping a field |
| **preflight** | `preflight.py` | is the module actually installed/loaded, from which path, with shadow/duplicate `addons_path` traps flagged | "my change didn't apply" / before trusting an edit landed |

And one runtime-**state** layer for when you need the values, not just the call graph:

| Layer | Script | Answers | Run when |
|-------|--------|---------|----------|
| **F (state)** | `state_capture.py` | the **values** at runtime — args/locals/`self` at a breakpoint (`model.method` or source line), and the **full call stack with every frame's locals** when the method raises. The non-interactive, JSON analog of an IDE's "inspect variables" / post-mortem | Layer D shows *what* runs but you need *what the values were*, or a flow raises and the traceback alone doesn't explain why |

Each script runs inside `odoo-bin shell` and prints pure JSON between sentinels (`===ODOO_BRIEF_START===` … etc.). Feed that JSON to the agent **before** any code.

## One command: `odoo-ai all <model>`

The `scripts/odoo-ai` CLI runs every layer for you, extracts the JSON, and writes a folder — instead of four shell invocations:

```bash
# Layers A+B+C (D needs a record id + method):
scripts/odoo-ai --db <DB> all sale.order --methods action_confirm,write,create

# include the runtime trace too:
scripts/odoo-ai --db <DB> all sale.order --methods action_confirm \
    --record-id 42 --method action_confirm

# individual layers / a single trace:
scripts/odoo-ai --db <DB> brief sale.order --methods action_confirm --source
scripts/odoo-ai --db <DB> trace sale.order 42 action_confirm   # --commit to persist (dev DB only)

# reverse impact before a rename, and the "did my edit even load?" preflight:
scripts/odoo-ai --db <DB> refs sale.order commitment_date      # who breaks if I change this field
scripts/odoo-ai --db <DB> preflight my_module                  # installed? loaded from where? shadowed?

# runtime VALUES (Layer F): break when execution enters a method and dump its state,
# or capture the full stack-with-locals if it raises:
scripts/odoo-ai --db <DB> state sale.order 42 action_confirm \
    --break sale.order._action_confirm --fields state,amount_total   # inspect-variables, as JSON
scripts/odoo-ai --db <DB> state sale.order 42 action_confirm --on-exception   # post-mortem stack + locals
```

> **Layer F redacts sensitive data by default.** Locals, dict keys, and field names that look like secrets (`password`, `token`, `secret`, `api_key`, `authorization`, `session`, …) are emitted as `<redacted>`. Add more with `--redact-extra ssn,iban`; turn it off with `--no-redact` on a trusted dev box only. Redaction is key-name based — it won't catch a secret stored under a benign name, and source bodies (`--source`) / explicit `--fields` values are **not** redacted. Don't paste raw `state`/source JSON into an external LLM unless reviewed.

Config via flags or env: `--db/ODOO_DB` (required), `--conf/ODOO_CONF`, `--odoo-bin/ODOO_BIN`, `--out-dir` (default `/tmp/odoo-ai/<model>`). Or run a single script directly: `MODEL=sale.order odoo-bin shell -d <DB> --no-http < scripts/model_brief.py`.

**Path when installed as a Claude Code plugin.** The examples use `scripts/odoo-ai` (relative to a clone). When this suite is installed as a plugin, it lives in Claude's cache, so invoke the CLI via the plugin-root variable instead: `"${CLAUDE_PLUGIN_ROOT}"/skills/odoo-introspect/scripts/odoo-ai --db <DB> all <model>`. The `--odoo-bin` flag still points at the *target instance's* `odoo-bin` (or `odoo`), which is unrelated to where the plugin lives.

See `references/sample-output.md` for the JSON shape each layer returns.

## Workflow: discover → plan → code (never skip step 1)

1. **Discover** — run `odoo-ai all <model>` (always Layer A; add B/C/D as scope demands). No shell on Odoo Online/SaaS? See `references/introspection.md` for the RPC fallback + `mcp-odoo` helper.
2. **Plan** — from the briefs: which inheritance mode; which fields to **reuse** vs add (half the "new" fields already exist — check the inventory); the **smallest** extension point (prefer a `_prepare_*` / `_action_*` hook over overriding `create`/`write`/`action_*`); whether/where you'll call `super()`; the `depends` so your override lands at the right MRO layer; security / multi-company / performance risk.
3. **Code** — smallest patch extending the real `super()` from step 1, then prove it with a test.

## Gotchas that fail silently

- **MRO ≠ runtime.** The chain lists where overrides *resolve*, not what *runs*. A layer that doesn't call `super()` ends it. `super_position` is a regex heuristic (`"heuristic": true`) — confirm big flows with Layer D.
- **`noupdate=True` seeded records** (Layer C) are loaded once on install, then **protected from `-u`** — your later XML edits won't apply either. To change one on an installed DB, write a migration. (Default `noupdate=False` records are re-asserted from XML on `-u`, so runtime/UI edits revert.)
- **Wrong `depends` → wrong MRO layer.** Depend on the addon that owns the method you extend, or your override silently sits below the one that matters and "never runs".
- **API renames bite across versions** — `name_get`→`_compute_display_name`, `fields_view_get`→`get_view`, `attrs`/`states` removed. See `references/version-matrix.md`.
- **Empty `_warnings` ≠ nothing wrong**, but a non-empty one (e.g. `field_modules lookup failed`) means a layer is partial — read it.

## References & scripts

- `scripts/model_brief.py` — Layer A: fields, MRO + super analysis, security, auto-triggers, depends.
- `scripts/entrypoints.py` — Layer B: buttons, view modifiers, view inheritance chain, window actions, reports (quick). `VIEW_XMLID`/`VIEW_ID` renders one specific view.
- `scripts/metadata.py` — Layer C: menu graph, seeded data + noupdate, deep report wiring.
- `scripts/trace_flow.py` — Layer D: real runtime call sequence (executes; rolls back unless `COMMIT=1`).
- `scripts/field_refs.py` — Layer E: reverse impact of a field (computes/related/views/rules/filters/actions that depend on it) before a rename/retype/drop.
- `scripts/preflight.py` — module preflight: installed/loaded state, load path, shadow/duplicate `addons_path` traps.
- `scripts/state_capture.py` — Layer F: runtime state — breakpoint snapshot (args/locals/`self` at a `model.method` or source line) + exception post-mortem (full call stack with each frame's locals). Non-interactive, JSON.
- `scripts/odoo-ai` — CLI that runs all four and writes a JSON folder.
- `references/introspection.md` — RPC fallback for SaaS + mcp-odoo integration.
- `references/sample-output.md` — abbreviated sample JSON for each of the four layers.
- `references/version-matrix.md` — v16/v17/v18 API differences this suite depends on.
