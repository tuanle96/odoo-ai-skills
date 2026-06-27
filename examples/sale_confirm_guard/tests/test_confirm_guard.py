from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestConfirmGuard(TransactionCase):
    """Prove the guard at install time, the way odoo-testing prescribes:
    fail-before / pass-after, the real state literal, and batch behaviour."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Guard Test Co"})
        cls.product = cls.env["product.product"].create(
            {"name": "Widget", "type": "consu"})

    def _new_order(self, **vals):
        return self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "order_line": [(0, 0, {
                "product_id": self.product.id, "product_uom_qty": 1})],
            **vals,
        })

    def test_confirm_blocked_without_delivery_date(self):
        order = self._new_order()
        self.assertFalse(order.commitment_date)
        with self.assertRaises(UserError):
            order.action_confirm()
        # Real literal from the Layer A selection dump: draft/sent/sale/cancel.
        self.assertEqual(order.state, "draft")

    def test_confirm_succeeds_with_delivery_date(self):
        order = self._new_order(commitment_date="2030-01-01 10:00:00")
        order.action_confirm()
        # NOT 'confirmed' — the brief showed the confirmed state is 'sale'.
        self.assertEqual(order.state, "sale")

    def test_batch_confirm_is_atomic(self):
        # action_confirm runs on recordsets; one bad record must block the batch.
        good = self._new_order(commitment_date="2030-01-01 10:00:00")
        bad = self._new_order()
        with self.assertRaises(UserError):
            (good | bad).action_confirm()
        self.assertEqual(good.state, "draft")
