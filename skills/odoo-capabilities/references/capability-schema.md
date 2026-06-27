# Capability card schema

Curated capability cards are the durable, expert half of the Native Capability Atlas. They live as JSON arrays in `references/cards/*.json` (one file per domain: `universal`, `sale`, `stock`, `account`, `mrp`, `purchase`, `hr`). `native_check.py` recall-matches them against a requirement, then **existence-gates** each against the running instance via its `probe`.

A card describes *one* native capability: what it's for, how to recognise the need (intents), the right way to reuse/extend it, and how to prove it exists here.

## Fields

| Field | Type | Purpose |
|-------|------|---------|
| `id` | string | Unique, `domain.slug` (e.g. `sale.down_payment_invoice`). |
| `title` | string | One-line human description. |
| `domain` | string | `universal` or an app: `sale`/`stock`/`account`/`mrp`/`purchase`/`hr`. |
| `primitive` | string | The kind of native thing: `sequence`/`cron`/`automation`/`computed_field`/`related_field`/`mixin`/`feature_group`/`record_rule`/`report`/`wizard`/`hook`/`field`. |
| `intents` | string[] | Business phrases that should recall this card — **include Vietnamese**. Matching is diacritic-folded, so write them naturally (`đặt cọc`). ≥3 required. |
| `modules` | string[] | The addon(s) that ship it (documentation; the `probe` does the real gating). |
| `models` | string[] | The models involved. |
| `hooks` | string[] | The right extension point(s) — `_prepare_*` / `_action_*` / method names. |
| `reuse_advice` | string | What to do: reuse X, extend at hook Y, and the anti-pattern to avoid. |
| `when_not_enough` | string[] | When custom code IS justified — keeps the card honest. |
| `probe` | object | The **existence gate** (below). |

## The `probe` — the existence gate

A probe is evaluated against the live registry; a card only becomes a `confirmed_candidate` (one the agent may recommend) if its probe passes **here**. This is what keeps native-check evidence-grounded rather than a memorised list.

**Leaf probes** (the full grammar — `native_check.PROBE_KINDS`):

| `kind` | Fields | Passes when |
|--------|--------|-------------|
| `module_installed` | `module` | that module's state is `installed`. |
| `model_exists` | `model` | the model is in the registry. |
| `field_exists` | `model`, `field` | the field is on that model. |
| `method_exists` | `model`, `method` | that method exists on the model (the right hook is present). |
| `xmlid_exists` | `xmlid` | `env.ref(xmlid)` resolves (an action/view/record/group seeded here). |
| `action_window_exists` | `model` | a window action (`ir.actions.act_window`) targets that `res_model`. |
| `group_exists` | `xmlid` | that xmlid resolves to a `res.groups` (a feature/security group is present). |
| `cron_exists` | `model` | a scheduled action (`ir.cron`) is bound to that model. |
| `sequence_exists` | `code` | an `ir.sequence` with that `code` exists. |
| `selection_has_value` | `model`, `field`, `value` | that selection field actually offers that literal (kills guessed `state` values). |
| `mixin_inherited` | `model`, `mixin` | the model's MRO includes that mixin (`mail.thread`, `mail.activity.mixin`, `portal.mixin`, …). |
| `edition` | `edition` (`enterprise`/`community`) | the instance matches (Enterprise detected via `web_enterprise`). |

**Combinators:** `{"any": [ ...leaves ]}` (≥1 passes) and `{"all": [ ...leaves ]}` (all pass). They nest — e.g. prove both a hook *and* the canonical state literal:

```json
{"all": [{"kind": "method_exists", "model": "sale.order", "method": "_action_confirm"},
         {"kind": "selection_has_value", "model": "sale.order", "field": "state", "value": "sale"}]}
```

> Prefer the **most precise** probe available: `selection_has_value` over a bare `field_exists` for a state-machine card; `mixin_inherited` over `model_exists` when the card is "inherit this mixin"; `edition` to gate an Enterprise-only capability so it lands in `unconfirmed_candidates` (not a false recommendation) on Community.

```json
{
  "id": "sale.down_payment_invoice",
  "title": "Down-payment / advance invoice from a sales order",
  "domain": "sale",
  "primitive": "wizard",
  "intents": ["down payment", "advance payment", "đặt cọc", "thu tiền trước"],
  "modules": ["sale"],
  "models": ["sale.advance.payment.inv", "sale.order"],
  "hooks": ["_create_invoices", "_prepare_invoice"],
  "reuse_advice": "Use the sale.advance.payment.inv wizard; override _prepare_invoice for extra header data. Don't build account.move by hand.",
  "when_not_enough": ["non-standard revenue recognition"],
  "probe": {"kind": "model_exists", "model": "sale.advance.payment.inv"}
}
```

## Authoring guidance

- **Probe = the most precise available evidence.** Prefer `method_exists` for a hook card (proves the exact hook is here), `model_exists` for a wizard, `field_exists` for a field-reuse card, `module_installed` for a feature toggle. A framework primitive that's always present (e.g. `ir.sequence`) probes its own model — it's then always confirmed, which is correct.
- **Recall is a wide net; the agent ranks precision.** Generous, natural-language `intents` (EN + VN) improve recall. Don't try to make the probe or intents do the agent's relevance judgement.
- **`when_not_enough` is mandatory and honest.** Native-first is not anti-customization — say when custom code is the right call.
- **Cards are validated in CI** (`tests/test_native_check.py`, `test_pure_functions.py`): every card must have all fields, a unique id, ≥3 intents, and only valid probe kinds. Add a card → those tests guard it.

## Recall and the learning loop

`native-check` ranks cards against the requirement with **TF-IDF cosine** over the card text (intents + title + domain) plus an **intent-phrase bonus** (a full intent appearing verbatim, diacritic-folded). This is a deliberately dependency-free, offline vector-space recall — *not* dense neural embeddings, which would need a model at runtime (a heavy dependency or a network/API call from inside `odoo-bin shell`) and aren't warranted at this corpus size; the agent already does the final relevance ranking.

**Learned mappings** grow recall from real usage. `odoo-ai native-learn "<phrase>" --card <id>` appends to a learned file (`~/.odoo-ai/learned.json` by default):

```json
[ { "id": "universal.mail_activity", "learned_intents": ["gọi điện chăm sóc khách hàng sau bán"] } ]
```

At check time these are merged into the corpus: an entry whose `id` matches a shipped card **augments its intents**; an entry carrying the full card fields (`intents` + `probe`) is added as a **new learned card**. So a phrasing that recalled nothing today will recall its card tomorrow — the practical, model-free path to semantic improvement (and the seam where a dense embedder could later plug in, scoring the same merged corpus).
