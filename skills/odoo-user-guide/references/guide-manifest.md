# `guide.yaml` — the manifest schema

The manifest is the **durable artifact** of a guide. `odoo-guide-init` writes it
from ground truth; `odoo-guide-run` replays it; `odoo-guide-render` renders it. The
recording and screenshots are derived — commit the manifest, regenerate the rest.

## Shape

```yaml
guide:
  id: confirm_a_sale_order      # slug derived from the intent (folder name)
  title: confirm a sale order   # shown as the guide heading
  model: sale.order             # the model the flow lives on
  role: salesperson@acme.com    # effective login the guide is valid for (or null)
  company: null                 # acting company id/name (or null)
  preconditions:
    demo_db_only: true          # this guide mutates state -> sandbox only
    create_test_record: true    # the runner makes its own throwaway record
  steps:
    - action: open_menu
      label: "Sales > Orders > Quotations"
      selector_strategy: odoo_menu_text
      screenshot: true
      narration: "Mở menu Sales > Orders > Quotations."
    - action: open_record
      record_ref: guide_test_record
      selector_strategy: odoo_record_row
      screenshot: true
      narration: "Mở bản ghi cần thao tác."
    - action: click_button
      model: sale.order
      button_name: action_confirm   # the Odoo button METHOD name (stable selector)
      visible_text: Confirm          # human label, from the view (for the caption + fallback)
      mutates: true                  # marks this as a state-changing step (gate input)
      selector_strategy: odoo_button_method
      screenshot: true
      narration: "Nhấn nút Confirm."
      expected:                      # the BACKEND proof for the run
        field: state
        equals: sale
```

## Step actions (v1)

| `action` | Meaning | Mutating? |
|---|---|---|
| `open_menu` | navigate via a menu label (leaf text) | no |
| `open_record` | open the owned test record's form | no |
| `click_button` | click a statusbar/header button by method name | **yes** |

Any step with `action: click_button` or an explicit `mutates: true` is treated as
state-changing by `odoo-guide-doctor` (see `safety-gates.md`).

## `expected` — what makes it a proof

The first step carrying an `expected: {field, equals}` is asserted at the backend
via XML-RPC after the run (`odoo-guide-run` reads the field off the owned record).
The run's exit code and `run.json.proof.pass` reflect that assertion — a guide that
"looks right" but didn't actually transition state fails loudly.

## Authoring by hand

You can edit `guide.yaml` directly (add steps, fix a menu label, change the
`expected` value). Keep `button_name` equal to the real Odoo method — `odoo-ai all
<model>` lists the view buttons and their methods.
