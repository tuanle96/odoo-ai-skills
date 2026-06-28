# VHS tape — renders the demo GIF for the README + landing hero.
# Install VHS (https://github.com/charmbracelet/vhs), then:
#     vhs examples/demo.tape          # writes examples/demo.gif
#
# Self-contained: the `odoo-ai` / `odoo` / `cat` commands are shadowed by shell
# functions (defined in the hidden setup block) that print canned, on-brand
# output — so it narrates the introspect → patch → prove loop without a live
# Odoo and renders identically anywhere. Palette matches docs/index.html.
#
# Two VHS gotchas this works around:
#   • Only the FIRST Hide/Show pair hides — so all setup lives in one block and
#     each beat's context line is baked into its function (not a hidden printf).
#   • A single Type drops characters past ~250 chars — so one short printf per line.

Output examples/demo.gif

Set Shell "bash"
Set FontSize 21
Set Width 1280
Set Height 720
Set Padding 40
Set Margin 34
Set MarginFill "#0d0b12"
Set BorderRadius 12
Set WindowBar Colorful
Set WindowBarSize 40
Set LineHeight 1.35
Set TypingSpeed 28ms
Set Theme { "background": "#0a0810", "foreground": "#ece9f2", "cursor": "#39d4b4", "selection": "#2b2438", "black": "#15121d", "red": "#ff6b6b", "green": "#39d4b4", "yellow": "#e0c879", "blue": "#5b8cff", "magenta": "#8f6aa6", "cyan": "#39d4b4", "white": "#ece9f2", "brightBlack": "#6f6880", "brightRed": "#ff6b6b", "brightGreen": "#39d4b4", "brightYellow": "#e0c879", "brightBlue": "#5b8cff", "brightMagenta": "#b890c9", "brightCyan": "#39d4b4", "brightWhite": "#ffffff" }

# ── hidden setup: clean prompt + shadow functions (context line baked in) ─────
Hide
Set TypingSpeed 6ms
Type "export PS1='$ '"
Enter

Type "odoo-ai() {"
Enter
Type `printf '\033[34m▸\033[0m \033[90mINTROSPECT · what the running instance actually says\033[0m\n'`
Enter
Type `printf '  \033[32m✓\033[0m \033[1mbrief\033[0m            \033[35msale.order\033[0m  \033[90m91 fields · 7 overridden methods\033[0m\n'`
Enter
Type `printf '  \033[32m✓\033[0m \033[1maction_confirm\033[0m   \033[90mresolves in\033[0m \033[35msale\033[0m \033[90m· bottom of MRO (has_super=false)\033[0m\n'`
Enter
Type `printf '  \033[32m✓\033[0m \033[1mstate\033[0m literals    \033[35mdraft / sent / sale / cancel\033[0m   \033[33mnot the guessed “confirmed”\033[0m\n'`
Enter
Type `printf '  \033[32m✓\033[0m \033[1mcommitment_date\033[0m  is a \033[35msale\033[0m field \033[90m— so @api.depends targets it\033[0m\n'`
Enter
Type "}"
Enter

Type "cat() {"
Enter
Type `printf '\033[34m▸\033[0m \033[90mPATCH · the smallest correct change — guard before super()\033[0m\n'`
Enter
Type `printf '\033[34mdef\033[0m \033[1maction_confirm\033[0m(self):\n'`
Enter
Type `printf '    \033[34mfor\033[0m order \033[34min\033[0m self:\n'`
Enter
Type `printf '        \033[34mif not\033[0m order.commitment_date:\n'`
Enter
Type `printf '            \033[34mraise\033[0m UserError(_(\033[32m"Set a Delivery Date before confirming."\033[0m))\n'`
Enter
Type `printf '    \033[34mreturn\033[0m \033[35msuper\033[0m().action_confirm()      \033[90m# then the sale layer confirms\033[0m\n'`
Enter
Type "}"
Enter

Type "odoo() {"
Enter
Type `printf '\033[34m▸\033[0m \033[90mPROVE · a real test, against real state literals\033[0m\n'`
Enter
Type `printf '  \033[32m✓\033[0m confirm blocked without a delivery date    \033[90mstate stays\033[0m \033[35mdraft\033[0m\n'`
Enter
Type `printf '  \033[32m✓\033[0m confirm succeeds with a delivery date      \033[90mstate ==\033[0m \033[35msale\033[0m\n'`
Enter
Type `printf '  \033[32m✓\033[0m batch confirm stays atomic\n'`
Enter
Type `printf '  \033[90m──────────────────────────────\033[0m\n'`
Enter
Type `printf '  \033[1m\033[32m✓  3 / 3 PASS\033[0m   \033[90msmallest correct patch   ·   \033[0m\033[1m\033[35mmerge gated\033[0m\n'`
Enter
Type "}"
Enter

Set TypingSpeed 28ms
Type "clear"
Enter
Show
Sleep 700ms

# ── 1 · INTROSPECT — read ground truth from the running instance ─────────────
Type "odoo-ai --db dev all sale.order --methods action_confirm"
Sleep 400ms
Enter
Sleep 2600ms

# ── 2 · PATCH — the smallest correct change ──────────────────────────────────
Type "cat sale_confirm_guard/models/sale_order.py"
Sleep 400ms
Enter
Sleep 3000ms

# ── 3 · PROVE — a real test, not a guess ─────────────────────────────────────
Type "odoo -d dev -i sale_confirm_guard --test-enable --test-tags /sale_confirm_guard"
Sleep 400ms
Enter
Sleep 4200ms
