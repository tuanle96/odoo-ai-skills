# Odoo Online: advisory only

**Odoo Online (the SaaS hosting on odoo.com) does not allow custom modules.**
There is no shell, no custom addons, no CI hooks. Therefore there is **no code
gate on Odoo Online** — and anyone who tells you otherwise is wrong.

The code path — `Inspect → Verify → Gate → Report` on AI-written custom changes
— targets **self-hosted** and **Odoo.sh**, where code can actually run. On Odoo
Online the suite is **read-only advisory**: it can describe and assess the
instance, but it cannot verify custom code because there is no custom code to
verify.

---

## What DOES work on Odoo Online (read-only advisory)

All of these are read-only and run over Odoo's RPC surface:

- **Instance Dossier** — installed apps, edition, configuration, entrypoint
  surface, effective per-role security. *(Delivered via an RPC-limited mode where
  no shell is available — RPC mode is roadmap **v0.15**; until then, Dossier depth
  is reduced on Online vs. a shell-backed instance.)*
- **Configuration audit** — settings, users/groups, company setup, obvious
  misconfigurations and risky toggles.
- **End-user guides** — the `odoo-user-guide` skill drives the real UI on a
  sandbox and asserts backend state; it works against Online for documenting
  standard flows.
- **Fit-gap analysis** — what the standard apps cover vs. what the client needs,
  and where a customisation would be required (which then implies leaving Online).
- **Migration-readiness assessment** — a structured read on what it takes to move
  toward **Odoo.sh / on-premise** once custom code becomes necessary.

## What does NOT work on Odoo Online, and why

- **No custom addons.** Odoo Online only runs standard + Odoo-approved apps. You
  cannot install a bespoke module, so there is nothing bespoke to verify.
- **No shell.** No `odoo-bin shell`, so the deep introspection layers (runtime
  `trace`, `state` capture, full registry MRO) that need a shell don't run —
  RPC gives a shallower read.
- **No CI hooks.** No git-push deploy, no test runner you control, no place to
  wire the gate. The CI-bound evidence gate has nothing to bind to.

The gate **never claims to verify custom code on Odoo Online.** If a client
needs custom code gated, the honest answer is: move that workload to Odoo.sh or
self-hosted.

## Decision table — hosting × capability

| Capability | Odoo Online (SaaS) | Odoo.sh | Self-hosted |
|---|---|---|---|
| **Instance Dossier** | Advisory (RPC, reduced depth — RPC mode v0.15) | Yes (shell) | Yes (shell) |
| **Facts / context** (fields, registry, security) | Advisory (RPC, shallow) | Yes | Yes |
| **Code gate** (verify AI-written custom code) | **No** (no custom code) | Yes | Yes |
| **CI evidence** (CI-bound signed evidence bundle) | **No** (no CI hooks) | Yes | Yes |

- **Odoo.sh = prime target for the full gate.** It has shell/SSH, git-push
  deploy, staging branches, and CI — the whole `Inspect → Verify → Gate → Report`
  loop runs end to end.
- **Self-hosted = full power**, same as Odoo.sh, plus whatever CI you already run.
- **Odoo Online = advisory ceiling.** Useful, honest, read-only — and clear about
  its limits.

## How this maps to engagements

- A pilot on an Odoo Online client narrows to the advisory deliverables (Dossier,
  config audit, fit-gap, migration-readiness) — see
  [pilot-package.md](pilot-package.md).
- The moment a client needs custom code, the recommendation is Odoo.sh or
  self-hosted, at which point the code gate applies. This is often the natural
  output of the **migration-readiness assessment**.

## Related

- [Pilot package](pilot-package.md) — advisory-only scope for Online clients.
- [Partner enablement](partner-enablement.md) — takeover/Dossier audits, migration.
- [`README.md`](../README.md) — "Odoo hosting reality" section.
- [Artifact governance](artifact-governance.md) — even RPC Dossiers carry PII.
