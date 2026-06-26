# odoo.sh & Odoo Online — managed hosting

Self-hosted deployment (the rest of `odoo-deploy`) doesn't apply unchanged on
Odoo's managed platforms. This covers what does. Targets Odoo 17/18.

## odoo.sh — git is the deploy mechanism

On odoo.sh you don't run `-u` and restart; you **push to a git branch** and the
platform builds it. Branches map to environments:

| Branch type | Environment | Built on | Use for |
|---|---|---|---|
| **production** | live | the real production DB | the deployed code; merge here last |
| **staging** | prod-like | a **copy of production** (data + filestore) | rehearse `-u`/migrations against real data |
| **development** | throwaway | demo data, fresh DB | feature branches, fast iteration |

### Deploy flow

1. Push your feature branch (a *development* build): fresh DB with demo data,
   modules installed from scratch, tests run. Fast, disposable.
2. Merge into a **staging** branch: odoo.sh builds it on a **fresh copy of
   production data**, runs the module **update** (so your migration scripts run
   against real rows), and runs tests. **This is the migration rehearsal** the
   `odoo-migration` skill insists on — for free, on real data.
3. Read the **build log** (below). Green + correct data → merge into
   **production**, which applies the update to the live DB.

A push to staging/production that changes an installed module triggers `-u` on
that build automatically — you don't pass the flag.

### What to check in a build log

- **Build status** green/red — red blocks promotion.
- The **update phase**: did your `migrations/<version>/` scripts run? (They run
  only if the manifest version bumped — same rule as self-hosted; → `odoo-migration`.)
- **Test results** — odoo.sh runs `--test-enable`; failing tests fail the build.
- **Tracebacks** during install/update — the *first* one is the cause (→ `odoo-debug`).
- On a **staging** build specifically: confirm migrated data looks right, because
  this is the only place you see your migration touch production-shaped data
  before it hits production.

### Introspection on odoo.sh

Each branch has a **web shell** and SSH — so the full engine works:
`odoo-bin shell -d <branch-db>` and the `odoo-ai` CLI run normally against a
branch. Prefer a **staging or development** branch for introspection/tracing,
never production (trace_flow rolls back, but don't run experiments on live).

### odoo.sh gotchas

- **`-u` is implicit on push** — a change to a `noupdate="0"` data record will be
  re-asserted on the staging/production build; protect config data with
  `noupdate="1"` (→ `odoo-data`).
- **Submodules** — third-party/OCA addons are usually git submodules; a missing
  submodule commit makes the build fail with "module not found" though it's "in
  the repo" (→ the self-hosted `addons_path` trap, git-shaped).
- **Build resources** — large DBs make staging builds slow; a migration that does
  `search([])` + a Python loop can time out the build (→ `odoo-migration` batching).
- **Don't hand-edit production data** to fix something a build should fix — the
  next build from git is the source of truth and will overwrite drift.

## Odoo Online (SaaS) — no code, no shell

Odoo Online is the fully-managed SaaS tier:

- **No custom modules**, no `odoo-bin`, no shell, no `odoo.conf`. Customization is
  **Studio** (fields/views/automations) and server actions only.
- **Introspection is RPC-only** — use the fallback in
  `odoo-introspect/references/introspection.md` (fields come back fully over RPC;
  MRO/runtime tracing do not).
- If a task genuinely needs a module (a real override, a controller, an OWL
  widget, a QWeb report parser), the instance must be on **odoo.sh or
  self-hosted** — say so up front instead of writing code that can't be deployed.
- Migrations between Odoo *versions* on Online are run by Odoo's upgrade service
  (upgrade.odoo.com); you don't write `migrations/` scripts there.

## Quick decision

- Need a custom module + want introspection/tests/CI → **odoo.sh** (git push,
  staging rehearsal) or self-hosted.
- Studio-level changes only, no code → **Odoo Online** is fine; introspect via RPC.
- Heavy ops control (workers, proxy, custom infra) → **self-hosted** (rest of
  `odoo-deploy`).
