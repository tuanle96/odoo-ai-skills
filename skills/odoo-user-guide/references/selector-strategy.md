# Selector strategy — bind to Odoo metadata, not CSS

Odoo's web client is OWL with a highly dynamic DOM; Studio and themes rewrite
markup without touching business logic; translated labels move with the user's
language. Brittle CSS/XPath rots fast. So the manifest records **semantic Odoo
intent** and `odoo_guide_lib.resolve_selectors()` expands it to a prioritized list
the runner resolves at runtime — first candidate that matches wins.

## Priority (most stable first)

1. **Odoo metadata** — the button **method name** is the anchor:
   `button[name="action_confirm"]`, scoped to the statusbar/control panel
   (`.o_statusbar_buttons button[name=...]`, `.o_cp_buttons button[name=...]`).
   Method names survive themes, translations, and most Studio edits.
2. **Accessible role / visible text** — `button:has-text("Confirm")` only as a
   *later* fallback (breaks under translation).
3. **Structural** — `.o_data_row`, `.o_kanban_record` for opening a record.
4. **Raw CSS/XPath** — last resort, avoided in v1.
5. **Vision** — not in v1; reserved for repair mode (v2), never canonical.

## Why method name over text

`Confirm` becomes `Xác nhận` / `Bestätigen` per user language, and Studio can change
a button's caption while keeping its method. `action_confirm` is the same on every
instance running that addon. `odoo-ai all <model>` gives you the method for each
view button — that is what `odoo-guide-init` writes into `button_name`.

## Adding actions later

New step actions extend `resolve_selectors()` with their own ordered candidate list.
Keep the same rule: metadata anchor first, text fallback last. This is the one
function to unit-test when adding an action — see `tests/test_odoo_guide_lib.py`.
