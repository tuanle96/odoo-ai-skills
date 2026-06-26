# Reading Odoo tracebacks & logs

Companion to the SKILL decision tables. Goal: go from a wall of log to the one frame that matters, fast.

## The log-reading recipe

1. **Find the first real traceback, not the last.** Odoo re-raises through ORM/loading layers, so the bottom of the console is often a generic wrapper. Scroll up to the **earliest** `Traceback (most recent call last):` in the failing request/boot — that origin frame names the addon and line.
2. **Read the request line above it.** `werkzeug`/`http` logs the `model`, `method`, and `uid` of the call — that pins which user and record reproduced it (re-run as that user in shell).
3. **Find the deepest `/addons/<your_module>/` frame.** Core `odoo/...` frames are plumbing; the lowest frame under an addon path is where business code went wrong.
4. **Match the exception class** to the table below.

```bash
# Scope noise to one addon, keep SQL out unless hunting N+1:
odoo-bin -d DB --log-handler=odoo.addons.estate:DEBUG --log-level=warn
# Boot failure — watch the loader:
odoo-bin -d DB -u estate --log-handler=odoo.modules.loading:DEBUG --stop-after-init
```

## Error class → cause → fix

### `KeyError: 'field_name'` / "Field 'x' does not exist"
The field isn't in the **runtime registry**. Causes, in order of likelihood:
- the module that defines it isn't in your `__manifest__.py` `depends` (so it never loaded);
- a typo / rename (check `model_brief.fields` for the real name);
- referenced in a view/domain but the defining addon is uninstalled.
Fix: add the dependency or correct the name. Confirm with `odoo-ai brief <model>` → is it in `fields`, and what `modules` touched it.

### `MissingError: Record does not exist or has been deleted`
The record was valid, then wasn't, by the time you used it:
- deleted/unlinked earlier in the same flow (trace it — `odoo-ai trace`);
- rolled back in a nested savepoint;
- **hidden by the multi-company global rule** — it exists but `company_id` isn't in `company_ids`, so the ORM treats it as missing. Re-check with `sudo()` *only to diagnose*, then fix the company scope (see `odoo-security`).

### `AccessError`
ACL or record rule denied. Distinguish:
- "You are not allowed to **{read/write/create/unlink}** ... **(Operation)**" → ACL: no group grants that perm. Check `access_rights`.
- message names the document / "records ... rule" → a **record rule** narrowed the rows. Check `record_rules`.
Admin not reproducing it is the tell: run as the real user (`with_user`). Never paper over with `sudo()` before you know which gate fired.

### `ValidationError`
A constraint rejected the value:
- `@api.constrains('a','b')` — Python check; grep the message;
- `_sql_constraints` — DB-level (unique/check); the message is the constraint's third element.
`model_brief` lists each method's `constrains` decorator so you can find it.

### `UserError`
Not a framework error — a deliberate business guard (`raise UserError(_("..."))`). Grep the exact message string across addons to find the raising line; the *condition* above it is the real subject.

### `CacheMiss`
The ORM expected a cached value and it was gone:
- a field read after its dependency was invalidated but not recomputed;
- raw `cr.execute` changed rows without `invalidate_recordset` / `modified`, so the cache and DB diverged.
Fix the flush/invalidate ordering (see `odoo-perf`), don't retry-loop.

### "Invalid view definition" / "Element ... cannot be located" / blank form
A view failed to assemble at render:
- an `xpath` in an inheriting view no longer matches (the base arch changed under you);
- an `invisible=` / `readonly=` / `groups` expression references a field that isn't in the view or registry.
Run `odoo-ai entrypoints <model>` to see the **inheritance-resolved** arch (what actually renders), and `--dev=xml` to edit-reload without `-u`. In v17/18 these are plain Python attribute expressions (`invisible="state == 'done'"`), not the old `attrs="{...}"`.

### Registry / loading failure at boot
The server won't start or a module won't install:
- Python import error in a model file → first traceback names the file/line;
- XML parse / bad `ref=` to a missing external id → loader logs the file and record id;
- circular or missing `depends` → "module X depends on Y which is not found".
Always read top-down from the **first** error; later ones cascade.

## pdb & shell — reproduce in seconds

```python
# Drop a breakpoint in suspect code, then exercise the path; remove before commit.
def action_confirm(self):
    breakpoint()          # pdb prompt in the server console
    return super().action_confirm()
```

```bash
# Reproduce without the UI — same env the request used:
odoo-bin shell -d DB
>>> rec = env['sale.order'].browse(42)
>>> rec.with_user(env.ref('base.user_demo')).action_confirm()   # repro as a real user
>>> env.cr.rollback()    # undo side effects
```

Useful pdb: `pp self`, `pp self._fields.keys()`, `pp self.env.context`, `w` (stack), `u`/`d` (frames), `c` (continue).

## SQL & performance crossover

```bash
odoo-bin -d DB --log-handler=odoo.sql_db:DEBUG --log-level=debug_sql
```
Logs every query with params and timing. A burst of near-identical `SELECT`s differing only by id is an N+1 — hand off to `odoo-perf`. For a structured count without log-grepping, `odoo-ai trace <model> <id> <method>` reports `total_sql` and per-call `sql_count`.

## Quick reference

| Want | Command |
|------|---------|
| Scope DEBUG to one addon | `--log-handler=odoo.addons.<mod>:DEBUG` |
| See every SQL | `--log-handler=odoo.sql_db:DEBUG` |
| Iterate views fast | `--dev=xml` (serve arch from files) |
| Auto-reload on save | `--dev=reload` (or `=all`) |
| Live ORM repro | `odoo-bin shell -d DB` |
| Resolved view arch | `odoo-ai entrypoints <model>` |
| Real call order + SQL | `odoo-ai trace <model> <id> <method>` |
