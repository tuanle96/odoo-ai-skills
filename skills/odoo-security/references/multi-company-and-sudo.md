# Multi-company isolation & sudo discipline

Read alongside Layer G — `odoo-ai --db <DB> security <model> --user <login|id> [--company <id>] [--allowed-companies <a,b>]`. Every claim here is provable by diffing that command's `record_rules.<mode>.effective_domain` across runs; it's the domain Odoo's own `ir.rule._compute_domain` returns, not a guess. Targets Odoo 17/18, through Odoo 19 (current LTS).

> **v18 → 19 API:** check access in code with **`check_access(op)`** (raises), **`has_access(op)`** (bool), or **`_filtered_access(op)`** (allowed subset) — the v≤17 `check_access_rights()` + `check_access_rule()` pair is superseded (v18) / deprecated (v19). From **v18.2** public model methods are RPC-callable by default; mark internal ones **`@api.private`**.

## How a multi-company rule is actually evaluated

Company isolation is **not** your Python — it's a **global `ir.rule`** whose `domain_force` Odoo re-evaluates per request with these names in scope: `user`, `company_id`, `company_ids`, `time`. The bindings are the whole game:

| Name in `domain_force` | Resolves to | Is |
|---|---|---|
| `company_ids` | `self.env.companies.ids` | the **allowed/active set** — companies toggled ON in the switcher (`--allowed-companies`) |
| `company_id` | `self.env.company.id` | the single **active** company (`--company`) |
| `user.company_id` | the user's **default** company | login-time, fixed — **not** the switcher |
| `user.company_ids` | the companies the user is **assigned** | the menu they *may* toggle |

Odoo's own built-in rule is `['|', ('company_id', '=', False), ('company_id', 'in', company_ids)]` — scoped to the **active set**, so it tracks the switcher (`company_id = False` = shared records, visible to all). The `--allowed-companies` flag exists precisely because `company_ids` resolves to `env.companies`: it lets you evaluate one rule under different switcher states.

## The default-company trap (high-risk playbook #1)

A hand-rolled rule that pins to the user's *default* company:

```python
[('company_id', '=', user.company_id.id)]      # BROKEN — ignores the switcher
```

is wrong for any multi-company user: `user.company_id` is fixed at login while the active set changes as they toggle companies, so the rule both hides legitimate rows and (playbook #1) leaks rows when the default differs from the active company. The fix is the active-set form:

```python
['|', ('company_id', '=', False), ('company_id', 'in', company_ids)]   # CORRECT — tracks env.companies
```

**Prove it, don't reason about it** — evaluate the domain under two switcher states and diff:

```bash
# User C assigned to Company 2, only Company 2 active:
odoo-ai --db <DB> security sale.order --user <user_c> --company 2
# Same user, Company 1 also toggled on:
odoo-ai --db <DB> security sale.order --user <user_c> --company 2 --allowed-companies 1,2
```

A correct rule's `record_rules.read.effective_domain` widens from `('company_id','in',[2])` to the full allowed set (`1,2`) exactly as `--allowed-companies` grows. A broken `= user.company_id` rule shows the **same** domain in both runs — that invariance is the bug. Cross-check `company.simulated_allowed_company_ids` and `company.user_allowed_companies` in the output to confirm which set you actually evaluated.

## with_company / env.company / env.companies

| Call / attr | Effect |
|---|---|
| `self.env.company` | the **active** company — read it; never hardcode a `company_id` |
| `self.env.companies` | the allowed/active **set** (what `company_ids` binds to in a rule) |
| `recs.with_company(c)` | run the chain with `c` active — new records default `company_id = c`, company-dependent fields resolve for `c` |
| context `allowed_company_ids` | the switcher state; drives both of the above |

- `with_company(c)` sets the *active* company but grants **no** access — `c` must already be in the user's allowed set, or the write still trips the isolation rule (Odoo invariant: the active company must be inside `allowed_company_ids`).
- A **stored** compute whose value differs per company needs `@api.depends_context('company')`, or the first company's value is cached and served to all.
- `sudo()` does **not** change company scope — it widens *records*, not companies. Cross-company still needs `with_company`.

## sudo() — legitimate vs security hole (high-risk playbook #2)

`sudo()` runs as superuser and bypasses **both** gates — ACL *and* every record rule. Sometimes right, often a hole:

| Legitimate (narrow, with a one-line reason) | Hole |
|---|---|
| Read an `ir.config_parameter` / bump an `ir.sequence` the user can't touch directly | Silencing an `AccessError` you don't understand — it's usually a *correct* rule |
| Let a portal user advance *their own* order through a step writing a record they can't | Blanket `sudo()` on a whole method "to make it work" |
| Post a `mail.message` / system log on the user's behalf | `sudo()` to read another company's data instead of `with_company` |

**The one-line-comment rule:** every `sudo()` carries a comment naming the privilege reason. `odoo-ai validate <file>` (LOCAL, no DB) flags any uncommented `sudo()`. Then confirm a real restriction is being bypassed:

```bash
odoo-ai validate path/to/models/account_move.py          # flags uncommented sudo()
odoo-ai --db <DB> security account.move --user <user_id>  # is there a rule sudo() skips?
```

A non-empty `record_rules.<mode>.effective_domain` for that user means a rule restricts them — and a `sudo()` in the path silently erases it (playbook #2: every employee can read invoices). Layer G's `_caveat` is explicit: it models the **acting user**, so `sudo()` is its blind spot — grep Layer A source (`--source`) for `sudo(` on the methods you care about, then `odoo-ai trace account.move <id> action_post` to see the frame where `uid` switches to `1`.

## Access checks in code (v18/19) — not the old pair

| Need | v18 / 19 | v≤17 (superseded → deprecated v19) |
|---|---|---|
| Raise if denied (ACL + rules) | `recs.check_access(op)` | `check_access_rights(op)` + `check_access_rule(op)` |
| Boolean, don't raise | `recs.has_access(op)` | `check_access_rights(op, raise_exception=False)` |
| Filter a recordset to the allowed subset | `recs._filtered_access(op)` | manual loop + `check_access_rule` |

`op` is one of `read|write|create|unlink`. AI emits the old two-call pattern constantly from training data — confirm the instance version first. Layer G already runs the version-correct check: `access_rights.odoo_check` is Odoo's own `check_access` verdict, cross-checked against the additive ACL union; a disagreement raises a `_warnings` entry telling you to trust `odoo_check`.

## @api.private — public method = RPC endpoint (v18.2+)

From v18.2 any **public** method (no leading underscore) is callable over RPC via `call_kw` by any user who can reach the model. A helper left public — `def do_charge(self)` instead of `_do_charge` — is now a remote surface, silent until someone scripts it.

```python
@api.private          # blocks RPC; call only from server-side Python
def recompute_totals(self):
    ...
```

A leading underscore is **convention**; `@api.private` is **enforcement**. Mark internal-but-public methods private; keep genuine endpoints public deliberately. `odoo-ai validate <file>` flags risky public methods; pair with `odoo-ai security <model> --user <id>` to confirm who can reach the model at all — no ACL for that user means no RPC surface for them.

## Checklist

- [ ] Company isolation is a **global `ir.rule`** using `('company_id','in',company_ids)`, not hand-rolled `user.company_id`.
- [ ] Diffed `effective_domain` across `--company` / `--allowed-companies` runs — it tracks the allowed set.
- [ ] `company_id` on the model; reads use `env.company` / `env.companies`; cross-company writes use `with_company`.
- [ ] Every `sudo()` has a one-line reason and is the narrowest call; `odoo-ai validate` is clean.
- [ ] `security --user` confirmed the rule any `sudo()` bypasses is actually correct.
- [ ] Access checks use `check_access` / `has_access` / `_filtered_access` (not the v≤17 pair); version confirmed.
- [ ] Internal-but-public methods marked `@api.private` (v18.2+); the RPC surface is deliberate.
