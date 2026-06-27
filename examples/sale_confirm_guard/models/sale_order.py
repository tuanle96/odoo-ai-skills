from odoo import _, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        """Block confirmation until a delivery date is set.

        Why this shape (from the Layer A brief):
          - `action_confirm` resolves in `sale` at the bottom of the MRO
            (has_super=False) and fans out to `_prepare_confirmation_values`
            and stock/invoice flows. Guarding *before* `super()` stops the
            whole flow cleanly — no half-confirmed order, no stock moves.
          - We loop `self` because `action_confirm` is called on recordsets
            (batch), not a single record — the brief's recordset semantics.
          - `commitment_date` is a `sale` field, so `depends = ['sale']` puts
            this override above the base and the `super()` call lands there.
        """
        for order in self:
            if not order.commitment_date:
                raise UserError(_(
                    "Set a Delivery Date before confirming %(name)s.",
                    name=order.display_name,
                ))
        return super().action_confirm()
