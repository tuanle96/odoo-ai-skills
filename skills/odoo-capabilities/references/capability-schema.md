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

**Leaf probes:**

| `kind` | Fields | Passes when |
|--------|--------|-------------|
| `module_installed` | `module` | that module's state is `installed`. |
| `model_exists` | `model` | the model is in the registry. |
| `field_exists` | `model`, `field` | the field is on that model. |
| `method_exists` | `model`, `method` | that method exists on the model (the right hook is present). |

**Combinators:** `{"any": [ ...leaves ]}` (≥1 passes) and `{"all": [ ...leaves ]}` (all pass). They nest.

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
