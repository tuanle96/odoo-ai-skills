# VHS tape — renders the demo GIF for the README.
# Install VHS (https://github.com/charmbracelet/vhs), then:
#     vhs examples/demo.tape          # writes examples/demo.gif
# Self-contained: it narrates the introspect → patch → test loop without needing
# a live Odoo, so it renders identically anywhere.

Output examples/demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1280
Set Height 720
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 35ms

Type "# odoo-ai: read ground truth from the running instance, THEN change code"
Enter
Sleep 1s

Type "# 1) introspect sale.order.action_confirm"
Enter
Type "odoo-ai --db dev all sale.order --methods action_confirm"
Enter
Sleep 600ms
Type "#  -> action_confirm is owned by 'sale' (bottom of MRO, has_super=false)"
Enter
Type "#  -> commitment_date is a 'sale' field   |   state literals: draft/sent/sale/cancel"
Enter
Sleep 2s

Type "# 2) smallest correct patch: guard BEFORE super(), depend on 'sale'"
Enter
Type "cat examples/sale_confirm_guard/models/sale_order.py"
Enter
Sleep 600ms
Type "#     for order in self:"
Enter
Type "#         if not order.commitment_date:"
Enter
Type "#             raise UserError(_('Set a Delivery Date before confirming ...'))"
Enter
Type "#     return super().action_confirm()"
Enter
Sleep 2s

Type "# 3) prove it — real state literal, not a guessed 'confirmed'"
Enter
Type "odoo -d dev -i sale_confirm_guard --test-enable --test-tags /sale_confirm_guard --stop-after-init"
Enter
Sleep 600ms
Type "#  PASS  confirm blocked without delivery date  (state stays 'draft')"
Enter
Type "#  PASS  confirm succeeds with delivery date     (state == 'sale')"
Enter
Type "#  PASS  batch confirm is atomic"
Enter
Sleep 3s
