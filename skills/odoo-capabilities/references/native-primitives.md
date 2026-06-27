# Native primitives — stop hand-rolling X, Odoo ships Y

A curated map of the Odoo native primitives an AI agent most often reinvents, with the canonical entrypoint and the anti-pattern each one replaces. **Targets Odoo 17/18/19.**

> This is a **map, not ground truth.** Names, editions, and availability drift across versions and installed modules. Before you rely on any entry, confirm it exists in *this* instance:
> ```bash
> odoo-ai --db <DB> capabilities <model>        # native surface around a model
> odoo-ai --db <DB> capabilities --module <addon>
> ```
> Existence in the running registry is the evidence. A primitive listed here but **absent** in the instance may be Enterprise-only, or behind a disabled feature-group — don't assert it from this file alone.

---

## Numbering & sequences

**`ir.sequence`** — gap-aware, per-company, optionally date-ranged auto-numbering.
- **Anti-pattern:** `max(self.search([]).mapped('ref')) + 1` in `create()`; manual UUIDs; counting records.
- **Entrypoint:** `self.env['ir.sequence'].next_by_code('your.code')`; `with_company(c).next_by_code(...)` for per-company; `use_date_range=True` for per-period counters (e.g. `INV/2026/0001` resetting each year).
- **Wire-up:** define the sequence in data XML (`ir.sequence` record), call `next_by_code` from a `_prepare_*`/`create` override.
- **When native isn't enough:** truly bespoke numbering with external reservation — even then, keep `ir.sequence` for the local counter.

---

## Periodic jobs & event reactions

**`ir.cron` (Scheduled Actions)** — run a method on a schedule.
- **Anti-pattern:** a custom daemon, an external cron calling RPC, a polling loop, a `while` in a thread.
- **Entrypoint:** an `ir.cron` data record (`model_id`, `state='code'`, `code` body, `interval_number`/`interval_type`, `numbercall=-1`, `doall`). The code body usually calls one clean method on the model.
- **When native isn't enough:** sub-minute scheduling or heavy distributed jobs — but try `ir.cron` first; it's transactional and survives restarts (`doall` replays missed runs).

**`base.automation` (Automation Rules)** — declarative reactions to record events.
- **Anti-pattern:** overriding `write()` / `create()` purely to fire a side-effect (send a mail, create an activity, set a flag, notify a webhook).
- **Triggers:** on creation, on update (incl. specific field changes / "values updated"), on deletion, on a **time condition** (N days before/after a date field), on email events, on webhook. Linked to a server action for the *what*.
- **Version note:** the model is `base.automation` (stable since v10); the **UI label was renamed "Automated Actions" → "Automation Rules" in v17** (Studio). Requires the `base_automation` module installed.
- **When native isn't enough:** complex, ordered, testable business logic — then write a small method and either call it from a server action or override the flow method with a clear guard. Don't bury real logic in a write-override "because it was quick".

**Computed fields (`@api.depends`) — NOT `@api.onchange` — for derived values.**
- **Anti-pattern:** business logic in `@api.onchange`; recomputing a total inside `write()`.
- **Doctrine (reaffirmed in the v19 tutorials):** `@api.onchange` fires **only in the form view** on a pseudo-record (not saved); it does **not** run on ORM `create`/`write`, API/RPC, import, or cron. So any value that must always be correct belongs in a **computed field** (`@api.depends(...)`, `store=True` if queried/grouped) or a `@api.constrains`. Reserve `onchange` for pure form-time UI assistance.
- **When native isn't enough:** a one-way mirror → use a `related=` field; a side-effect (not a derived value) → that's a flow hook, not a compute.

---

## Records, history & activities

**`mail.thread` / `mail.activity.mixin` (chatter)** — messaging, field tracking, scheduled activities.
- **Anti-pattern:** a custom audit-log model; a custom "history" table; a homegrown reminder/follow-up model.
- **Entrypoint:** `_inherit = ['mail.thread', 'mail.activity.mixin']`; add `tracking=True` to the fields whose changes should log to the chatter; schedule reminders with `record.activity_schedule(act_type_xmlid, user_id=…, date_deadline=…)`. In the form: `<chatter/>` (v17+) / the `message_ids` + `activity_ids` widgets.
- **Version note:** *Activity Plans* (`mail.activity.plan`, e.g. onboarding) exist in 17+ — **verify against the instance** if a requirement looks like "a sequence of activities".
- **When native isn't enough:** a legally distinct ledger with its own retention/immutability — but reuse `mail.message` for ordinary "who changed what".

---

## Standard flows & wizards

**TransientModel wizards** — many native multi-step actions already exist as wizards. Reuse the wizard (or its `_prepare`/`_create` hooks); don't rebuild the flow.
- **Anti-pattern:** re-implementing invoice/payment/backorder creation by hand with `account.move.create(...)` etc.
- **Common ones (confirm per instance):** `sale.advance.payment.inv` (down-payment / advance invoice), `account.payment.register` (register payment across invoices), `stock.backorder.confirmation` (backorder dialog), `account.resequence.wizard`.
- **Version caveat:** **`stock.immediate.transfer` changed across versions** (widely reported removed/reworked around v16 — validating a picking now opens the backorder dialog directly). This is the textbook reason to enumerate from the instance rather than trust a memorized list. `odoo-ai capabilities --module stock` tells you what's actually there.
- **Find them:** `odoo-ai capabilities sale.order` lists the **bound actions** (the Action-menu surface) — that's where native wizards attach to a model.

**Extension hooks: `_prepare_*` / `_action_*` / `_get_*`** — the right override points.
- **Anti-pattern:** overriding the public `action_*` / `button_*` shell, or `create`/`write` wholesale, to inject values.
- **Convention:** `_prepare_*` builds a values dict (override-safe), `_action_*` does the work, `_get_*` retrieves/filters. e.g. add fields to a sale-generated invoice via `_prepare_invoice` / `_prepare_invoice_line`, not by overriding `_create_invoices`.
- **Confirm the exact name per instance** (`odoo-ai brief <model> --methods …`) — hook names are version- and module-variant.

---

## Reporting, config & access

**`ir.actions.report` + `report.paperformat`** — QWeb PDF/HTML reports.
- **Anti-pattern:** hand-rolled PDF generation, hardcoded page geometry.
- **Entrypoint:** an `ir.actions.report` linking a QWeb template + a `paperformat_id`. Extend a report by inheriting its QWeb template and/or overriding `_get_report_values`.

**`res.config.settings` (feature groups / settings)** — toggled behavior.
- **Anti-pattern:** hardcoding a feature check / role check in Python when a setting already gates it.
- **Entrypoint:** `group_*` boolean ↔ `implied_group` → `res.groups`; a config parameter via `ir.config_parameter`. Many native features are simply *off until a setting is enabled* — check before assuming a feature is missing.

**`ir.rule` + `res.groups`** — row-level + model-level access.
- **Anti-pattern:** a `read()` override that filters rows; manual permission checks sprinkled through methods.
- **Entrypoint:** record rules (domain, additive group rules vs subtractive global rules) + ACLs (`ir.model.access`). For "what can THIS user actually see/do", use `odoo-ai security <model> --user …` (Layer G).

**`ir.model.fields` manual / Studio `x_` fields** — real columns without a module.
- **Anti-pattern:** view-only pseudo-fields; computed shadows of data that should be stored.
- **Entrypoint:** manual fields (`x_` / `x_studio_` prefix) are real DB columns; for code, a normal field in a small module is usually cleaner and versionable.

---

## How to use this in a native-check

1. Read the requirement; spot the primitive smell (numbering? schedule? reaction? history? a "create the X document" flow? a derived value?).
2. Match it to a row above → that's a **candidate**.
3. **Confirm it in the instance** with `odoo-ai capabilities …` — get the evidence (xmlid/field/action).
4. Decide: reuse / reject-with-reason / the real gap → then introspect and build the gap at the native hook.
