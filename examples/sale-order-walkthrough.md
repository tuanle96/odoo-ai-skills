# Worked example: customizing `sale.order` the ground-truth way

A complete pass through the loop the suite enforces — **introspect → plan →
patch → test** — for a small, real change: *block confirming a quotation until a
delivery date is set.* Every decision below is taken from the live registry, not
memory. The runnable module is in [`sale_confirm_guard/`](sale_confirm_guard);
its tests run in CI (`.github/workflows/integration.yml`, the `example` job).

## 1. Introspect — read the registry before touching code

```bash
odoo-ai --db <DB> all sale.order --methods action_confirm
```

Layer A (`model_brief`) returned the facts that shape the change (abridged from a
real Odoo 18 instance):

```jsonc
{
  "methods": {
    "action_confirm": [
      {"addon": "sale_loyalty", "has_super": true,  "super_position": "late …"},
      {"addon": "sale",         "has_super": false,             // bottom of the chain
       "hooks_called": ["_prepare_confirmation_values",
                        "_send_order_confirmation_mail"]}
    ]
  },
  "fields": {
    "commitment_date": {"type": "datetime", "modules": "sale"},
    "state": {"type": "selection",
              "selection": [{"value": "draft"}, {"value": "sent"},
                            {"value": "sale"}, {"value": "cancel"}]}
  },
  "manifest_depends": {
    "by_location": {"core": ["sale", "sale_loyalty", "…"],
                    "enterprise": ["industry_fsm_sale"], "local": ["branch"]}
  }
}
```

Three things this tells us that guessing would get wrong:

- **`action_confirm` is owned by `sale`** (it's the `has_super: false` layer — the
  bottom of the MRO). So a custom module that **depends on `sale`** resolves
  *above* it, and its `super()` lands in the base implementation.
- **`commitment_date` is a `sale` field**, not `sale_stock` — so `depends =
  ['sale']` is sufficient; no extra dependency needed.
- **The confirmed state literal is `'sale'`**, not `'confirmed'`. This is the
  single most common hallucination on this model — the test below asserts the
  real value.

## 2. Plan — smallest correct change

- **Inheritance**: extension of `sale.order` (`_inherit`), no new model.
- **Hook**: override `action_confirm` and guard *before* `super()`. The brief
  shows `super()` fans out into `_prepare_confirmation_values` and the
  stock/invoice flow — guarding first means no half-confirmed order.
- **Recordset-safe**: `action_confirm` runs on batches, so loop `self`.
- **`depends`**: `['sale']` — the owning, core module. We don't depend on the
  `local` addon (`branch`) we saw in the chain; that would be accidental coupling.

## 3. Patch

[`sale_confirm_guard/models/sale_order.py`](sale_confirm_guard/models/sale_order.py):

```python
from odoo import _, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        for order in self:
            if not order.commitment_date:
                raise UserError(_(
                    "Set a Delivery Date before confirming %(name)s.",
                    name=order.display_name,
                ))
        return super().action_confirm()
```

## 4. Test — prove it (fail-before / pass-after)

[`sale_confirm_guard/tests/test_confirm_guard.py`](sale_confirm_guard/tests/test_confirm_guard.py)
follows the `odoo-testing` gate: `post_install`, the real state literal, and
batch behaviour.

```python
def test_confirm_blocked_without_delivery_date(self):
    order = self._new_order()
    with self.assertRaises(UserError):
        order.action_confirm()
    self.assertEqual(order.state, "draft")        # real literal from the brief

def test_confirm_succeeds_with_delivery_date(self):
    order = self._new_order(commitment_date="2030-01-01 10:00:00")
    order.action_confirm()
    self.assertEqual(order.state, "sale")         # NOT 'confirmed'

def test_batch_confirm_is_atomic(self):
    good = self._new_order(commitment_date="2030-01-01 10:00:00")
    bad = self._new_order()
    with self.assertRaises(UserError):
        (good | bad).action_confirm()
    self.assertEqual(good.state, "draft")         # one bad record blocks the batch
```

Run it:

```bash
odoo -d <DB> -i sale_confirm_guard \
     --test-enable --test-tags /sale_confirm_guard --stop-after-init
```

## 5. Review

Before merge, the `odoo-review` checklist applies: no `sudo()`, no data-loss, the
guard respects multi-company (it reads only `commitment_date` on `self`), and the
override sits at the right MRO layer (confirmed in step 1). Done — a four-line
behaviour change, every assumption checked against the running instance.
