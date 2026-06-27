---
name: odoo-capabilities
description: >-
  Check what Odoo ALREADY ships natively BEFORE writing any custom code — the
  step that comes before introspection. Use whenever a task would add a field,
  model, wizard, report, scheduled job (cron), or automation, or would override a
  core flow method — any time you'd otherwise reinvent something Odoo provides out
  of the box (auto-numbering → ir.sequence; periodic job → ir.cron; react-to-change
  → automation rule / computed field; audit log / reminders → mail.thread /
  activities; "create the invoice/payment" → an existing wizard). Describe the
  requirement to `odoo-ai native-check "<requirement>"` — it matches curated
  capability cards and existence-gates them against the live instance (returning
  candidates with cited evidence) — or enumerate the full native surface with
  `odoo-ai capabilities <model>` / `--module <addon>` (wizards, actions, crons,
  automations, sequences, mixins, feature groups, fields). So the agent reuses
  native Odoo instead of building custom. Pairs with `odoo-introspect` (which
  answers "where do I extend?"). Targets Odoo 17/18/19.
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

**Start with `native-check`** — describe the requirement; it recall-matches the curated capability cards, then **existence-gates** each against THIS instance and hands back candidates with cited evidence (gate-then-rank: the script does the objective gate, you do the relevance ranking).

```bash
# Does Odoo already ship this? (the primary tool)
odoo-ai --db <DB> native-check "tự động đánh số phiếu giao hàng"
odoo-ai --db <DB> native-check "block sale confirmation without a delivery date" --model sale.order
```

It returns JSON with two buckets:
- **`confirmed_candidates`** — cards whose existence probe **passed here**, each with `evidence` (module installed / model / field / hook present), `reuse_advice`, `hooks`, `when_not_enough`. These are the only ones you may recommend.
- **`unconfirmed_candidates`** — cards that matched the requirement but are **not active in this instance** (with `why_absent` — e.g. `base_automation` not installed, Community vs Enterprise). Surface as "exists in Odoo but not here", never as a recommendation.

If `confirmed_candidates` is empty (the cards don't cover it), fall back to the full surface scan:

```bash
# Full native surface AROUND a model, or everything an addon shipped (v0.5):
odoo-ai --db <DB> capabilities sale.order        # feature-map: mixins, fields, actions, bound wizards, crons…
odoo-ai --db <DB> capabilities --module sale      # models, wizards, actions, crons, automations, sequences…
```

Both read straight from the running registry; `capabilities` **never reads server-action/cron code bodies** (pure surface map). No shell (Odoo Online/SaaS)? The same `ir.model`/`ir.actions` reads work over the RPC fallback in `skills/odoo-introspect/references/introspection.md`.

## The output contract (what a native-check must conclude)

Don't just dump the dossier — decide on it. Before writing code, state:

1. **Native candidates found** — from `confirmed_candidates` (each carries instance evidence). Add any extra you spotted in the `capabilities` surface.
2. **Reused** — which you'll build on, and at which hook.
3. **Rejected + why** — which you ruled out (e.g. "matched but `unconfirmed` — `base_automation` not installed here", "doesn't cover the multi-currency case").
4. **The gap** — what genuinely needs custom code, and the smallest hook for it. Then hand off to `odoo-introspect` → `odoo-dev`.

> **Evidence or silence.** Only claim *"Odoo already does this"* for a `confirmed_candidate` — it has a real artifact in **this** instance. A memorized "Odoo has a wizard for that" is exactly the guessing this suite exists to stop, and a **false** "it's already built in" is worse than missing a native feature: prefer under-claiming.

## The cards and the anti-pattern → native-primitive map

`native-check` matches against curated **capability cards** in `references/cards/*.json` (one file per domain + `universal`) — see `references/capability-schema.md` for the format and how to add one. The narrative catalogue of *"stop hand-rolling X; Odoo ships Y"* — auto-numbering, periodic jobs, trigger reactions, computed-vs-onchange, chatter/activities, the standard wizards, `_prepare_*`/`_action_*` hooks, reports, feature groups, record rules — is in `references/native-primitives.md`.

## Reuse, don't avoid — customize the gap at the native hook

Native-first is **not** anti-customization. The goal is: don't rewrite what Odoo does well; when native covers 80%, extend the native flow at its proper hook (a `_prepare_invoice` override, an automation rule, a computed field) rather than rebuilding the other 20% from scratch. Once you've decided what to build, hand off to **`odoo-introspect`** for the exact names/MRO/super-chain, then **`odoo-dev`** / the relevant build skill.

## References

- `references/cards/*.json` — the curated capability cards `native-check` matches against (one file per domain + `universal`; ~34 cards). CI-validated.
- `references/capability-schema.md` — the card format (fields + the existence `probe`) and how to author a new card.
- `references/native-primitives.md` — the anti-pattern → native primitive catalogue (Odoo 17/18/19), with the canonical entrypoint and version notes for each.
