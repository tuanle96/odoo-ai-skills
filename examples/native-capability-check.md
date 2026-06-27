# Worked example: a native-check that *deletes* the task

The point of Step 0 (`odoo-capabilities`) is that the best Odoo patch is sometimes **no patch**. Here are two short native-checks where reading the instance turns a "write a module" request into "reuse what's already there."

The whole interaction is one command and a decision — no code, no module, no test to maintain.

---

## Case 1 — "Auto-number our delivery slips: DS/0001, DS/0002…"

**The reflex (wrong).** Add a field, override `create()`:

```python
# DON'T — racy, not per-company, not gap-aware, reinvents a core primitive
def create(self, vals):
    last = self.search([], order="id desc", limit=1)
    vals["ref"] = "DS/%04d" % ((int(last.ref.split("/")[1]) + 1) if last else 1)
    return super().create(vals)
```

**The native-check.** Before writing that, enumerate what `stock` already ships:

```bash
odoo-ai --db dev capabilities --module stock | grep -A20 '"sequences"'
```

```jsonc
"sequences": [
  { "name": "Picking DS", "code": "stock.picking.delivery", "prefix": "DS/", "xmlid": "stock.seq_picking_out" },
  ...
]
```

**The decision.**
- **Native candidate:** `ir.sequence` (`stock.picking.delivery`) — evidence: the xmlid `stock.seq_picking_out`, prefix `DS/`, already in this instance.
- **Reused:** the sequence. Numbering is `self.env['ir.sequence'].next_by_code('stock.picking.delivery')` — gap-aware, per-company, transactional, and it already exists.
- **Rejected:** the `create()` override (racy, not multi-company, fights the platform).
- **Gap:** none. The requirement is *configuration of an existing sequence*, not code.

**Result:** zero lines of Python. (See `references/native-primitives.md` → *Numbering & sequences*.)

---

## Case 2 — "When a lead's stage changes, log it and notify the salesperson."

**The reflex (wrong).** A custom `x_stage_history` model + a `write()` override that creates a log row and sends a mail.

**The native-check.**

```bash
odoo-ai --db dev capabilities crm.lead
```

```jsonc
{
  "mode": "model", "model": "crm.lead",
  "mixins": { "mail_thread": true, "activities": true, "portal": false },
  "functional_fields": [ { "name": "stage_id", "type": "many2one", "string": "Stage" }, ... ],
  "automation_rules": [ ... ]
}
```

**The decision.**
- **Native candidates (with evidence):**
  - `mixins.mail_thread = true` → `stage_id` with `tracking=True` already logs stage changes to the chatter. No history model needed.
  - `mixins.activities = true` + `base.automation` present → an **automation rule** "on `stage_id` updated → create activity for the salesperson" covers the notification with no code.
- **Reused:** field tracking (history) + an automation rule (notify).
- **Rejected:** the `x_stage_history` model and the `write()` override — both reinvent `mail.thread` / automation rules.
- **Gap:** if the notification logic is genuinely complex/ordered, write *one* small method and call it from a server action — but only that method, not the whole flow.

**Result:** a config change (tracking + one automation rule), not a module.

---

## The shape of every native-check

1. Spot the primitive smell (numbering, schedule, reaction, history, "create the X document", derived value).
2. `odoo-ai capabilities <model>` / `--module <addon>` — enumerate, get **evidence** (xmlid/field/action).
3. State **candidates / reused / rejected+why / the gap**.
4. Build only the gap — then hand off to `odoo-introspect` → `odoo-dev` for the names and the smallest hook.

> Evidence or silence: only claim "Odoo already does this" when you can point to a real artifact in *this* instance. A false "it's built in" is worse than missing one.
