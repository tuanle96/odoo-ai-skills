# Safety gates

Clicking a button in Odoo is **not** a read-only act. "Confirm" on a sale order can
fire server actions, send emails, create procurements / deliveries, post analytic
entries, start subscriptions, and trigger custom automations. So this skill treats
every mutating guide as potentially high-impact and gates it like the rest of the
suite's enforcement layer: **prove it's safe before the browser touches anything.**

## The gate: `odoo-guide-doctor`

Run before `odoo-guide-run` (the runner also re-checks the sandbox gate itself).
Returns a JSON verdict and exits non-zero on block:

```bash
odoo-guide-doctor guides/<id>/guide.yaml --db DEMO --user salesperson@x.com --sandbox
```

It **hard-fails (exit 2)** when:

- the guide has a mutating step **and** the DB is not marked sandbox;
- the effective role lacks **write** access for a mutating flow (from `odoo-ai
  security`);
- the effective role lacks **read** access on the model.

Logic lives in `odoo_guide_lib.doctor_verdict()` and is unit-tested
(`tests/test_odoo_guide_lib.py`) — no browser or DB needed.

## Sandbox is explicit, never guessed

A DB is **production until you say otherwise**. Mark a throwaway/demo DB as safe with
either:

- `--sandbox` on the command, or
- `ODOO_GUIDE_SANDBOX=1` in the environment.

There is deliberately **no auto-detection** of "is this demo?" — Odoo has no reliable
flag for it, and guessing wrong is exactly the failure we refuse to risk.

## Owned, disposable test data

`odoo-guide-run` never drives existing records. It:

- creates its **own** test record (the `sale.order` recipe picks a partner + product
  and makes a draft) with mail/tracking disabled in the create context
  (`tracking_disable`, `mail_create_nosubscribe`, `mail_notrack`);
- drives only that record;
- **archives** it in a `finally` block, even if the run fails.

## What v1 deliberately will NOT do

- mutate a production DB (no override exists in v1);
- run a recipe for any model other than `sale.order`;
- disable outgoing mail servers for you — run against a DB where mail is already
  caught/disabled, or one with no real `ir.mail_server`.

These are intentional stops, not omissions. Loosen them only with an explicit,
reviewed change.
