# Odoo testing patterns

Runnable skeletons for the cases in `SKILL.md`. Targets Odoo 17/18, through Odoo 19 (current LTS) (`odoo.tests.common`; `odoo.tests` re-exports it). Copy, rename, fill the asserts. Each snippet assumes `self.partner` / `self.product` are set up like the first one.

## TransactionCase skeleton (the default)

Each test method runs in a savepoint and rolls back; shared data goes in `setUpClass` (built once, not per method).

```python
from odoo.tests.common import TransactionCase
from odoo.tests import tagged

@tagged('post_install', '-at_install')        # cross-addon flow → post_install
class TestSaleConfirm(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Acme'})
        cls.product = cls.env['product.product'].create({
            'name': 'Widget', 'type': 'consu',
        })

    def test_confirm_sets_state_and_date(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 2})],
        })
        order.action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertTrue(order.date_order)
```

## Form() — the only way to test @api.onchange

`Form` replays the form view's onchange engine. A plain `create()` / `write()` does **not** fire onchange, so this is how you prove UI-assist logic (and why onchange can't be your sole validation).

```python
from odoo.tests.common import TransactionCase, Form

class TestOrderOnchange(TransactionCase):
    def test_partner_sets_pricelist(self):
        with Form(self.env['sale.order']) as form:
            form.partner_id = self.partner
            # onchange has already fired here, inside the `with` block:
            self.assertTrue(form.pricelist_id)
            # add a line through its own sub-Form:
            with form.order_line.new() as line:
                line.product_id = self.product
                line.product_uom_qty = 3
        order = form.save()
        self.assertGreater(order.amount_total, 0)
```

## with_user — non-admin (test the user who'll actually hit it)

Admin bypasses most record rules. If you touched ACL / record rules / groups / `sudo()`, prove it as a real user.

```python
from odoo.exceptions import AccessError

class TestAccessAsSalesman(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Acme'})
        cls.salesman = cls.env['res.users'].create({
            'name': 'Sam Sales', 'login': 'sam',
            'groups_id': [(6, 0, [cls.env.ref('sales_team.group_sale_salesman').id])],
        })

    def test_salesman_can_confirm_own_order(self):
        order = self.env['sale.order'].with_user(self.salesman).create({
            'partner_id': self.partner.id,
        })
        order.action_confirm()                      # must work for a salesman, not just admin
        self.assertEqual(order.state, 'sale')

    def test_salesman_blocked_from_restricted_model(self):
        with self.assertRaises(AccessError):
            self.env['res.users'].with_user(self.salesman).create({
                'name': 'Hacker', 'login': 'hax',
            })
```

## Multi-company — assert no cross-company leak

```python
class TestMultiCompany(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Acme'})
        cls.company_a = cls.env['res.company'].create({'name': 'CO-A'})
        cls.company_b = cls.env['res.company'].create({'name': 'CO-B'})

    def test_record_stays_in_its_company(self):
        rec_a = self.env['sale.order'].with_company(self.company_a).create({
            'partner_id': self.partner.id, 'company_id': self.company_a.id,
        })
        # Scoped to company B only, company A's record must be invisible.
        visible = self.env['sale.order']\
            .with_context(allowed_company_ids=self.company_b.ids)\
            .with_company(self.company_b)\
            .search([('id', '=', rec_a.id)])
        self.assertFalse(visible, "record leaked across companies")
```

## Batch create/write — prove the override is list-safe

If you overrode `create` / `write`, test with **many** records. A `@api.model_create_multi` override receives a list of vals; a loop-style override silently breaks here.

```python
class TestBatchCreate(TransactionCase):
    def test_create_multi_applies_to_each(self):
        records = self.env['sale.order'].create([
            {'partner_id': self.partner.id},
            {'partner_id': self.partner.id},
            {'partner_id': self.partner.id},
        ])
        self.assertEqual(len(records), 3)
        # every record got the side effect, not just the first:
        self.assertTrue(all(r.name for r in records))

    def test_write_batch(self):
        records = self.env['sale.order'].create(
            [{'partner_id': self.partner.id} for _ in range(3)]
        )
        records.write({'note': 'bulk'})
        self.assertEqual(set(records.mapped('note')), {'bulk'})
```

## HttpCase tour — controllers & OWL front end

Tours replay a recorded click-through in headless Chrome. The Python side just kicks it off; the steps live in a JS tour registered under `web_tour.tours`. Must be `post_install` so every asset is loaded.

```python
from odoo.tests.common import HttpCase
from odoo.tests import tagged

@tagged('post_install', '-at_install')
class TestEstateTour(HttpCase):
    def test_estate_flow(self):
        # "/odoo" is the v18 web root ("/web" still works); 2nd arg is the tour name.
        self.start_tour('/odoo', 'estate_property_tour', login='admin')
```

```javascript
// static/tests/tours/estate_property_tour.js
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("estate_property_tour", {
    url: "/odoo/action-estate.estate_property_action",   // where the browser starts
    steps: () => [
        { trigger: ".o_list_button_add", run: "click" },
        { trigger: "div[name='name'] input", run: "edit My House" },
        { trigger: ".o_form_button_save", run: "click" },
    ],
});
```

Register the JS as a test asset in `__manifest__.py`:

```python
'assets': {
    'web.assets_tests': ['my_module/static/tests/tours/estate_property_tour.js'],
},
```
