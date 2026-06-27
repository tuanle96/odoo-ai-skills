# Examples

- **[`native-capability-check.md`](native-capability-check.md)** — Step 0 in
  action: two native-checks (`odoo-ai capabilities …`) where reading the instance
  turns a "write a module" request into "reuse the `ir.sequence` / `mail.thread` /
  automation rule Odoo already ships" — the best patch is sometimes no patch.

- **[`sale-order-walkthrough.md`](sale-order-walkthrough.md)** — a complete
  introspect → plan → patch → test pass for a real `sale.order` change, with
  every decision taken from the live registry. The runnable module is in
  [`sale_confirm_guard/`](sale_confirm_guard); its tests run in CI
  (`.github/workflows/integration.yml`, the `example` job).

- **[`demo.tape`](demo.tape)** — the source for the README demo GIF. Render it
  with [VHS](https://github.com/charmbracelet/vhs):

  ```bash
  vhs examples/demo.tape      # writes examples/demo.gif
  ```

  The tape is self-contained (it narrates the loop without needing a live Odoo),
  so it renders identically on any machine and in CI.
