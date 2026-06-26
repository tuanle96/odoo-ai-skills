# Introspection without shell access

The four layer scripts need Python on the server (`odoo-bin shell`). When you only have RPC — Odoo Online / SaaS, no custom modules, or a remote box you can't shell into — use these fallbacks. Fields come back fully; MRO needs a tiny helper; runtime tracing (Layer D) is shell-only.

## Fields over RPC — fully available

Field metadata is reachable through the ORM over XML-RPC / JSON-RPC, so this works even on locked-down SaaS. It reproduces most of Layer A's field inventory.

```python
# Human-readable (type, string, relation, required, selection):
model.fields_get()                      # or execute_kw(db, uid, pw, MODEL, 'fields_get', [])

# store / compute / related / depends / which-modules-touched-it:
env['ir.model.fields'].search_read(
    [('model', '=', MODEL)],
    ['name', 'ttype', 'store', 'compute', 'related', 'depends', 'relation', 'modules'],
)
```

`ir.model.fields.modules` is the reliable "which addons touched this field" signal — the same data Layer A reports.

Layer B/C data is partly reachable too: `ir.actions.act_window`, `ir.actions.report`, `ir.ui.menu`, `ir.model.data`, `ir.model.access`, and `ir.rule` are ordinary models you can `search_read` over RPC. What you **can't** get over plain RPC is the inheritance-resolved view arch (`get_view` is callable, but postprocessing context differs) and — the big one — the MRO.

## MRO over RPC — needs a tiny server-side helper

The MRO and `super()` chain require `inspect` on the server; **plain RPC cannot read it.** Two options:

### Option A — you control the server (self-host): add one method

Drop this into any custom module and it becomes callable via `execute_kw`:

```python
# models/introspect.py
import inspect
from odoo import api, models

class IrModel(models.AbstractModel):
    _inherit = 'ir.model'   # available on every DB

    @api.model
    def introspect_mro(self, model_name, method_name):
        mcls = type(self.env[model_name])
        out = []
        for k in mcls.__mro__:
            if method_name in k.__dict__:
                fn = k.__dict__[method_name]
                try:
                    line = inspect.getsourcelines(fn)[1]
                    src = inspect.getsourcefile(k)
                except Exception:
                    line, src = None, None
                out.append({'module': k.__module__, 'class': k.__qualname__,
                            'file': src, 'line': line})
        return out   # index 0 runs first; super() descends
```

Call it:

```python
env['ir.model'].introspect_mro('sale.order', 'action_confirm')
```

### Option B — wire it into mcp-odoo as a tool

Since `introspect_mro` is just another model method, expose it as an MCP tool in `tuanle96/mcp-odoo` so any agent gets the call order without leaving the assistant:

```python
@mcp.tool()
def get_model_mro(model: str, method: str) -> list:
    """Return the super() chain (call order, top-first) for MODEL.METHOD."""
    return odoo.execute_kw('ir.model', 'introspect_mro', [model, method])
```

Pair it with `get_model_fields` (the `ir.model.fields.search_read` above) and you've turned the whole "agent guesses Odoo internals" class of bug into two deterministic tool calls. This is the highest-leverage place to put the fix.

### No server access at all (true SaaS lockdown)

You can't install Option A. Fall back to: fields via RPC (works), and MRO inferred from **pinned source** — clone `odoo/odoo` at tag `17.0` or `18.0` (+ enterprise if applicable) and walk the `_inherit` graph by hand. Less exact than the live registry, but correct for the standard addon set. Layer D (runtime trace) has no RPC equivalent — reason from the MRO and the addon graph instead, and verify on any non-SaaS copy you can shell into.
