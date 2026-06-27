# Sample introspection output (machine-readable)

One realistic, **valid JSON** document per introspection layer — the companions
to [`../sample-output.md`](../sample-output.md) (which annotates the same shapes
in prose). Use these to see the exact structure each `odoo-ai` layer returns
before you run it against your own instance, or as fixtures when building tooling
that consumes the output.

| File | Layer / command | Script |
|------|-----------------|--------|
| `sale_order.brief.json` | A — `odoo-ai brief sale.order` | `model_brief.py` |
| `sale_order.entrypoints.json` | B — `odoo-ai entrypoints sale.order` | `entrypoints.py` |
| `sale_order.metadata.json` | C — `odoo-ai metadata sale.order` | `metadata.py` |
| `sale_order.trace.json` | D — `odoo-ai trace sale.order 42 action_confirm` | `trace_flow.py` |
| `sale_order.refs.json` | E — `odoo-ai refs sale.order commitment_date --resolve-paths` | `field_refs.py` |
| `sale_confirm_guard.preflight.json` | preflight — `odoo-ai preflight sale_confirm_guard` | `preflight.py` |
| `sale_order.security.json` | G — `odoo-ai security sale.order --user 7` | `security_sim.py` |
| `sale_order.state.json` | F — `odoo-ai state sale.order 42 action_confirm --break sale.order._action_confirm --fields state,amount_total` | `state_capture.py` |

These are **illustrative** (values abridged/synthesized from a real Odoo 18
`sale.order` flow), not a live capture. The field/method names, state literals
(`'sale'`, not `'confirmed'`), and structure are accurate; ids, paths, and SQL
counts are representative.

A test (`../../scripts/tests/test_sample_fixtures.py`) parses every file and
asserts it carries the required top-level keys each script emits — so these
fixtures can't silently drift from the code. When a script gains an output key,
update the matching fixture and the test's key set.
