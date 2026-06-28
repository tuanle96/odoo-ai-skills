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

## Running against a Docker container

When Odoo runs in a container (the common dev setup), point the `odoo-ai` CLI at
a wrapper that runs the shell *inside* the container. `docker exec -i` forwards
the piped script on stdin; the env vars the scripts read must be forwarded
explicitly with `-e`:

```sh
# /usr/local/bin/odoo-docker  (chmod +x)
#!/bin/sh
EFLAGS=""
for v in MODEL METHODS SOURCE CODE CODE_PREVIEW OUT VIEWS VIEW_ID VIEW_XMLID FIELD RESOLVE_PATHS MODULE AS_USER AS_COMPANY AS_ALLOWED_COMPANIES \
         RECORD_ID METHOD COMMIT BREAK_AT BREAK_LINE FIELDS MAX_HITS ON_EXCEPTION REQUIREMENT \
         NO_REDACT REDACT_EXTRA MAX_DEPTH MAX_STRING MAX_RECORDS DATA_LIMIT; do
  EFLAGS="$EFLAGS -e $v"
done
exec docker exec -i $EFLAGS <container-name> odoo "$@"
```

```sh
odoo-ai --db <DB> --conf /etc/odoo/odoo.conf --odoo-bin /usr/local/bin/odoo-docker \
    all res.partner --methods write,create
```

The `--conf` / `--db` are resolved *inside* the container, so use the container's
paths. `--odoo-bin` only needs `odoo` (or `odoo-bin`) on the container's PATH.

`native-check` is the one command that needs **data files** (the capability-card
corpus), not just scalar args. The CLI ships that corpus — and any learned
mappings — as content **on the script's stdin**, so it reaches the container the
same way the script does: the host `CARDS_DIR` / `LEARN_FILE` paths are *not* read
inside the container and do **not** need to exist there or be forwarded. (Earlier
versions passed `CARDS_DIR` as a host path, which a container couldn't read —
yielding a silent "0 matched". That now either works via the injected corpus, or
fails loudly if the corpus is genuinely empty.)

## Integration smoke test

`scripts/tests/integration_smoke.py` runs Layers A/B/C (+ F if you pass a record
id) against a real instance and asserts structural invariants — selection
literals, the manifest `by_location` split, the view `inheritance_chain`, seeded
`noupdate` records, and Layer F redaction. It's opt-in: with no `ODOO_DB` it
skips, so the pure-function CI is unaffected.

```sh
# against a dev container (via the wrapper above):
ODOO_DB=<DB> ODOO_CONF=/etc/odoo/odoo.conf ODOO_BIN=/usr/local/bin/odoo-docker \
    SMOKE_RECORD_ID=<id> python skills/odoo-introspect/scripts/tests/integration_smoke.py

# or inside the container directly (odoo + python3 on PATH):
docker exec -e ODOO_DB=<DB> -e ODOO_CONF=/etc/odoo/odoo.conf <container> \
    python3 /path/to/scripts/tests/integration_smoke.py
```

CI runs the same script against the official `odoo:17.0` / `18.0` / `19.0`
images (`.github/workflows/integration.yml`) on a clean `base`-only DB.
