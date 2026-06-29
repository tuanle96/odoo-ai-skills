---
name: odoo-review
description: >-
  Reviewing Odoo code before it merges — your own AI-generated patch or someone
  else's PR. Use after writing or generating an Odoo change and before commit,
  or when asked to review/audit an Odoo module, diff, or addon. Catches the
  Odoo-specific defects AI confidently ships that lint and "it ran for me as
  admin" miss: blanket sudo() / privilege bypass, missing ACL or record rules,
  N+1 recordset loops, incomplete @api.depends on stored computes, patching core
  instead of a separate addon, wrong inheritance mode / MRO layer, deprecated
  v≤16 syntax (attrs/states/<tree>/name_get), guessed field & method names,
  unwired __init__ / manifest, data-loss-on-upgrade renames, and public routes
  with the wrong auth. Pairs with odoo-testing (review finds it, tests prove it).
  Read ground truth from the running instance to confirm a suspicion — don't
  guess whether a field or rule exists. Targets Odoo 17/18/19.
---

# Odoo code review — the second gate

`odoo-testing` proves a change does what it claims; **this skill catches what it
claims that it shouldn't, and what it silently broke elsewhere.** AI-generated
Odoo code is the dangerous case: it's syntactically clean, it "works for admin
on my machine", and it fails later — for a non-admin user, on the second
company, on a list of records, on `-u`, or on a hot list view. Review against
the Odoo-specific contracts a linter can't see.

**The rule: every finding is either confirmed against the running instance
(via `odoo-introspect`) or written down as an explicit assumption to verify —
review never adds new guesses.**

**Version floor: Odoo 17/18, through Odoo 19 (current LTS).** Deprecation checks below assume 17/18+; for older
targets — and for the v18.1 → 19 renames — see `skills/odoo-introspect/references/version-matrix.md`.

## How to review (in order)

1. **Get the diff and the ground truth.** Read the change; run `odoo-ai all <model>`
   for every model it touches (fields, MRO, security, depends). Half of "review"
   is just comparing the patch to what the brief says already exists.
2. **Run the checklist below**, severity-first (security → data loss → silent
   correctness → performance → style).
3. **Confirm, don't assert.** "This field may not exist" → check the brief. "This
   override may not run" → read the MRO / trace it. Turn each suspicion into a
   fact or a flagged assumption.
4. **Hand correctness findings to `odoo-testing`** — a real defect deserves a
   failing test, not just a comment.

## The review checklist (severity-ordered)

### Security (highest stakes — fails open, not loud)
- [ ] **No blanket `sudo()`** — every `sudo()` has a one-line reason and is the
  narrowest sub-call, not a method-wide bypass. A `sudo()` added to silence an
  `AccessError` is almost always hiding a *correct* record rule (→ `odoo-security`).
- [ ] **New model ships `ir.model.access.csv`** — all four `perm_*` set
  deliberately; otherwise non-admins get a silent 403 while admin "works".
- [ ] **Record rules considered** — multi-company / "own records" isolation is a
  rule, not Python filtering. Confirm rules exist in the brief.
- [ ] **Field-level `groups=`** on sensitive fields, not deletion/Python guards.
- [ ] **Public routes** (`@http.route`) use the right `auth=`; no `csrf=False` on
  a session POST; no controller-wide `sudo()` (→ `odoo-web`).
- [ ] **No SQL/domain injection** — user input is parameterized, never f-string'd
  into `cr.execute` or a domain.

### Data loss & upgrade safety
- [ ] **Field/model rename has a pre-migration** — a bare Python rename drops the
  old column and creates an empty one (→ `odoo-migration`). Silent data loss.
- [ ] **Changed stored-compute logic schedules a recompute** — existing rows keep
  stale values otherwise.
- [ ] **New required field on a populated model is backfilled in `pre-`** — not
  `post-` (too late for the NOT NULL).
- [ ] **Edited shipped data respects `noupdate`** — UI-editable config is
  `noupdate="1"`; a protected record needs a migration, not a `-u`.

### Silent correctness
- [ ] **Field & method names exist** — checked against the brief, not memory
  (`account.move` not `account.invoice`, real `partner_id` not invented).
- [ ] **Override lands at the intended MRO layer** — `__manifest__.depends`
  includes the addon that owns the method (depend on `sale_stock`, not just
  `sale`, to extend a stock hook). Confirm against the brief's recommended depends.
- [ ] **`super()` is called (and in the right place)** — a layer that drops
  `super()` cuts the chain; an early `return` skips it. Check the MRO analysis;
  trace big flows.
- [ ] **Right hook, not the public shell** — value-building belongs in a
  `_prepare_*` / `_action_*` / `_get_*` hook, not an override of
  `create`/`write`/`action_*` (→ `odoo-dev`, `odoo-domain-playbooks`).
- [ ] **Built-in, not hand-rolled** — derived value = computed/related field;
  rule = `@api.constrains`; numbering = `ir.sequence`. Procedural code in
  `write()` to compute/validate is a smell.
- [ ] **`create` override is batch-safe** — `@api.model_create_multi`, `vals` is
  a **list**; tested on a multi-record recordset.
- [ ] **Wired in** — every new `.py` imported in its `__init__.py`; every new
  data/view file listed in `__manifest__['data']` in dependency order; new OWL/JS
  in the correct assets bundle. Unwired = dead code, no error.

### Performance
- [ ] **No query in a loop** — no `search`/`browse` per record; search once with
  `in`, then `filtered`/`mapped` (→ `odoo-perf`).
- [ ] **`@api.depends` is exhaustive** on every stored compute (dotted paths
  included) — audit against `model_brief.fields[].depends`. A missing dep silently
  rots the value.
- [ ] **`store=True` is justified** (search/group/report on it), not "just in
  case" — stored computes add write amplification.
- [ ] **Batch `write`/`create`**, not field-by-field in a loop.
- [ ] **New index only where searched/grouped**, not on everything.

### Version & frontend currency
- [ ] **No `attrs=` / `states=`** in v17/18 views — direct `invisible=`/`readonly=`
  Python expressions; `<list>` not `<tree>`; `<chatter/>` (→ `odoo-views`).
- [ ] **No `name_get()`** — `_compute_display_name`; no removed ORM names
  (`fields_view_get`→`get_view`, etc. — → version-matrix).
- [ ] **No superseded v18.1 → 19 APIs** (confirm target version):
  `check_access_rights`/`check_access_rule` → `check_access`/`has_access`/`_filtered_access`;
  `read_group` → `_read_group`/`formatted_read_group`; `group_operator` → `aggregator`;
  `type='json'` → `type='jsonrpc'`; `self._cr`/`._context`/`._uid` → `self.env.*`;
  `from odoo.osv import expression` → `odoo.Domain` (→ version-matrix).
- [ ] **No unmarked public method that should be private (v18.2+)** — public
  model methods are RPC-callable by default; internal helpers want `@api.private`
  (→ `odoo-security`). A privilege/exposure check, not style.
- [ ] **OWL reads `props.record.data[props.name]`** / writes via `.update(...)`;
  correct import paths; template in the bundle (→ `odoo-owl`). Public JS uses the
  right framework (`publicWidget` vs Interactions — → `odoo-web`).

### Dependencies & hygiene
- [ ] **No hallucinated Python packages** — every `external_dependencies['python']`
  / import is a real, maintained package at a pinned version (slopsquatting guard).
- [ ] **No leftover debug** — `breakpoint()`, `print()`, `import pdb`, bare
  `except: pass` swallowing errors.

## The "looks fine, fails later" smells (quick scan)

| Smell in the diff | Likely real defect | Confirm with |
|---|---|---|
| `.sudo()` with no comment | privilege bypass / hidden record rule | `odoo-security` dossier |
| `for r in recs: ...search(/browse` | N+1 | `odoo-perf`, `trace_flow` SQL count |
| `store=True` + short `@api.depends` | stale stored value | `model_brief` depends |
| new model, no `.csv` in the diff | non-admin 403 | `access_rights` in brief |
| Python field renamed, no `migrations/` | data loss on `-u` | `odoo-migration` |
| override of `action_confirm`/`create` | wrong hook / broken `super()` | MRO + `trace_flow` |
| `attrs=` / `<tree>` / `name_get` | hard parse error / no-op on 17/18 | `odoo-views`, version-matrix |
| `auth='public'` reading user data | data exposure | `odoo-web`, record rules |
| new `.py` not in `__init__` | dead override, no error | `odoo-module-scaffold` |

## Output of a review

State, concisely: **blocking** issues (security, data loss, silent correctness)
vs **non-blocking** (perf/style); each with the file/line, why it's wrong *in
Odoo terms*, the fix, and — for correctness — the test that should prove it.
Don't rewrite the whole patch; point to the smallest correct change (the
`odoo-dev` ethos).

## Run the checklist as a linter (Layer I)

Much of the checklist above is now **executable**: `odoo-ai validate <path...>`
(local, no DB) statically flags the high-signal, mechanical defects — `attrs`/
`states`, `<tree>`, `name_get`, `type='json'` on 19+, `create()` without
`@api.model_create_multi`, `search()`/`browse()` in loops, f-string `cr.execute`,
uncommented `sudo()`, `self._cr`/`_uid`/`_context`, fragile xpath, leftover debug.
Run it first to clear the mechanical issues, then spend your judgement on what a
linter can't see (MRO layer, security intent, data-loss, right hook) — confirmed
against the brief. It complements, never replaces, the confirm-against-the-instance
rule. Before sharing any introspection JSON with an external LLM, run
`odoo-ai redact <file>` / `odoo-ai scan-secrets <file>`.

## References & related skills

**This skill's references**
- `references/review-checklist.md` — the checklist expanded dimension by dimension
  (security/sudo, data-loss/migration, silent-correctness/MRO+super+depends,
  performance/N+1, multi-company, version currency), each line with the *why in
  Odoo terms* and the exact `odoo-ai` command that confirms it.
- `references/ai-failure-modes.md` — the specific confident-but-wrong patterns AI
  ships (hallucinated names, memory-era syntax, `sudo()`-to-silence, batch-unsafe
  `create`, N+1, wrong-hook override, bare rename, slopsquatting, default-company
  rule), each with the `odoo-ai` command that catches it.

**Other skills in the loop**
- `odoo-testing` — turn each correctness finding into a failing-then-passing
  test; the PR checklist there is the merge gate this skill feeds.
- `odoo-introspect` — confirm every suspicion (fields, MRO, security, depends)
  against the running instance instead of asserting.
- `odoo-security` · `odoo-perf` · `odoo-migration` · `odoo-dev` · `odoo-views` ·
  `odoo-owl` · `odoo-web` — the per-domain detail behind each checklist line.
- `html-report` — when the review is for a human to read (not just a PR comment),
  render the findings as one consistent, self-contained HTML page.
