---
name: odoo-capabilities
description: >-
  Check what Odoo ALREADY ships natively BEFORE writing any custom code — the
  step that comes before introspection. Use whenever a task would add a field,
  model, wizard, report, scheduled job (cron), or automation, or would override a
  core flow method — any time you'd otherwise reinvent something Odoo provides out
  of the box (auto-numbering → ir.sequence; periodic job → ir.cron; react-to-change
  → automation rule / computed field; audit log / reminders → mail.thread /
  activities; "create the invoice/payment" → an existing wizard). Enumerates the
  live native surface (wizards, actions, crons, automations, sequences, mixins,
  feature groups, functional fields) straight from the running instance with
  `odoo-ai capabilities <model>` / `--module <addon>`, so the agent reuses native
  Odoo instead of building custom. Pairs with `odoo-introspect` (which answers
  "where do I extend?"). Targets Odoo 17/18/19.
---

# Odoo native capabilities — discover before you customize

The suite's whole rule is *read ground truth from the running instance, don't guess.* This skill applies that rule **one step earlier** than the rest: before asking *"where do I extend?"*, ask *"should I extend at all, or does Odoo already do this?"*

Odoo is enormous. Huge amounts of business behavior already ship as native primitives — sequences, scheduled actions, automation rules, mail/activity mixins, transient-model wizards, computed fields, feature groups — scattered across installed addons. Left to memory, an AI agent reinvents them: a `max(x)+1` numberer instead of `ir.sequence`, a `write()` override for a side-effect instead of an automation rule or computed field, a custom audit-log model instead of `mail.thread`. The result is more code, more bugs, and a fight with the platform on every upgrade.

**The move: enumerate the native surface from the live registry, reuse what fits, and only then customize the gap.**

## Native first → introspect → plan → code

This is **Step 0** of the suite's workflow. It does not replace `odoo-introspect` — it precedes it.

```
0. native-check   (this skill)  — does Odoo already ship this? what's the native surface here?
1. introspect     (odoo-introspect) — for the gap you DO build: fields, MRO, super(), security, trace
2. plan the smallest extension at the right hook
3. code → 4. test → 5. review
```

## When this gate fires (and when it stays silent)

Run a native-check **before** proposing code for an *additive or core-overriding* intent:

| Gate **fires** — run native-check first | Gate **silent** — skip it |
|---|---|
| add a new field · new model · new wizard | fixing a bug in your own custom code |
| new cron / scheduled job · new report | pure introspection / reading |
| new automation · auto-numbering · status history | a view-only tweak, a test, a refactor |
| **overriding a core flow method** (`create`/`write`/`action_*`) | work already inside your own module's logic |

Keep it cheap: a native-check is one command and a short judgement. Don't let it become ceremony on every edit.

## Run it

```bash
# What's the native capability surface AROUND a model (feature-map)?
odoo-ai --db <DB> capabilities sale.order
odoo-ai --db <DB> feature-map sale.order        # alias of the above

# What did a whole addon ship (models, wizards, actions, crons, automations, sequences…)?
odoo-ai --db <DB> capabilities --module sale
```

It enumerates straight from the running registry (the `ir.model.data` xmlid registry for a module; the live `_fields` + actions/crons/automations for a model). It **never reads server-action or cron code bodies** — it's a pure surface map, so there's nothing sensitive to gate. Output is JSON: mixins, functional fields, window/server/report actions, the bound Action-menu surface (where native wizards attach), crons, automation rules, sequences, feature groups — each with its **xmlid as evidence**.

No shell (Odoo Online/SaaS)? Use the RPC fallback in `skills/odoo-introspect/references/introspection.md` — the same `ir.model`/`ir.actions`/`ir.cron` reads work over RPC.

## The output contract (what a native-check must conclude)

Don't just dump the surface — decide on it. Before writing code, state:

1. **Native candidates found** — the primitives/features that could serve the requirement, each with its evidence (the field/action/wizard/xmlid you saw in the instance).
2. **Reused** — which you'll build on.
3. **Rejected + why** — which you considered and ruled out (e.g. "Enterprise-only and this is Community", "feature-group disabled", "doesn't cover the multi-currency case").
4. **The gap** — what genuinely needs custom code, and the smallest hook for it.

> **Evidence or silence.** Only claim *"Odoo already does this"* when you can point to a real artifact in **this** instance (a field, an action, a wizard, an xmlid). Capability facts drift across versions and editions — a memorized "Odoo has a wizard for that" is exactly the guessing this suite exists to stop. A **false** "it's already built in" is worse than missing a native feature: prefer under-claiming.

## The anti-pattern → native-primitive map

The durable catalogue of *"stop hand-rolling X; Odoo ships Y"* lives in `references/native-primitives.md` — auto-numbering, periodic jobs, trigger-based reactions, computed-vs-onchange doctrine, chatter/activities, the standard wizards, the `_prepare_*`/`_action_*` extension hooks, reports, feature groups, record rules. Read it when the requirement smells like infrastructure the platform probably already provides.

## Reuse, don't avoid — customize the gap at the native hook

Native-first is **not** anti-customization. The goal is: don't rewrite what Odoo does well; when native covers 80%, extend the native flow at its proper hook (a `_prepare_invoice` override, an automation rule, a computed field) rather than rebuilding the other 20% from scratch. Once you've decided what to build, hand off to **`odoo-introspect`** for the exact names/MRO/super-chain, then **`odoo-dev`** / the relevant build skill.

## References

- `references/native-primitives.md` — the anti-pattern → native primitive catalogue (Odoo 17/18/19), with the canonical entrypoint and version notes for each.
