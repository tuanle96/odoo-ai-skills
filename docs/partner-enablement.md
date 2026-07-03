# Partner enablement — service catalog

Paid enablement around an **open standard**. The verifier, the gate, the
evidence schema, and the scenario/fixture patterns are open (LGPL-3) and free.
What partners pay for is standing it up on their instances, tuning it to their
verticals, and training their teams to run
`Inspect → Verify → Gate → Report` themselves.

> This is the **primary business model**: paid partner enablement on top of an
> open tool. A fleet evidence dashboard is a possible *later* addition, not the
> pitch. The [poison list](governance.md#the-poison-list) is what we will never
> do — read it before quoting any service.

New engagements usually start with the [Pilot](pilot-package.md).

---

## Delivery principles

- **Everything is built on the open tool.** No private fork of the verifier, no
  closed scoring rules, no SaaS-gated core. If a partner needs a capability the
  open tool lacks, it lands upstream.
- **Artifacts belong to the client.** Dossiers, evidence bundles, policy configs
  — the client owns them and they stay in the client's environment. Handling
  follows [artifact-governance.md](artifact-governance.md).
- **The open evidence standard stays open.** Benchmark cases, the evidence
  artifact schema, and scenario definitions move toward neutral governance (see
  [governance.md](governance.md)). We do not sell a "Verified" badge and we do
  not paywall a safety benchmark.
- **Local-first, always.** No metadata leaves the client's box as part of any
  service. Redaction is mandatory before anything is shared externally.

---

## Services

### 1. Gate-in-a-Week

Stand up the CI-bound evidence gate on one repo and prove it on real PRs.

- **Outcome:** advisory gate live, then strict on high-risk models; team can
  reproduce a failed gate locally.
- **Duration:** ~2 weeks (the [Pilot](pilot-package.md) is this service).
- **Prerequisites:** staging DB with shell (self-hosted or Odoo.sh), GitHub/GitLab CI.
- **Deliverables:** Instance Dossier, wired CI gate, evidence samples, committed
  policy config, training session.

### 2. CI integration

Wire the gate into an existing pipeline beyond the first repo.

- **Outcome:** gate running across the partner's repos / branches, with the
  sticky PR comment and `approve | needs-human | block` verdict.
- **Duration:** 2–5 days per pipeline, depending on existing CI complexity.
- **Prerequisites:** GitHub Actions / GitLab CI (or an Odoo.sh-adjacent runner
  that has shell + git). Odoo.sh itself is the prime target — SSH/CI available.
- **Deliverables:** CI config, evidence storage/retention wired per
  [artifact-governance.md](artifact-governance.md), runbook.

### 3. Custom fixture factories

Build test-data factories for the partner's verticals so scenario tests reflect
real business shapes, not toy records.

- **Outcome:** reusable factories (e.g. multi-company invoices, staged
  manufacturing orders, subscription lifecycles) the team runs in scenario tests.
- **Duration:** 3–8 days per vertical.
- **Prerequisites:** access to representative (non-production) data shapes.
- **Deliverables:** fixture modules + docs; patterns contributed back upstream
  where generic.

### 4. Private scenario packs

Curated scenario definitions for a partner's recurring project types (retail
POS rollouts, manufacturing, field service, etc.).

- **Outcome:** a scenario pack that turns introspection into the *mandatory*
  tests for that project type.
- **Duration:** 1–2 weeks per pack.
- **Prerequisites:** a defined project type and at least one reference project.
- **Deliverables:** scenario pack + policy tuned to that project type. Pack
  *definitions* stay in the open scenario schema; a partner's private domain
  content stays private to that partner.

### 5. Takeover / upgrade Dossier audits

An Instance Dossier for an inherited or pre-upgrade instance — what's really
installed, what's customised, what breaks on `-u`.

- **Outcome:** a read-you-can-act-on: module drift, Studio fields, rename-vs-drop
  data-loss risks, security surprises before you touch anything.
- **Duration:** 3–10 days depending on instance size.
- **Prerequisites:** shell (or Odoo.sh) access to the instance, or RPC-only for
  Odoo Online advisory (see [odoo-online-advisory.md](odoo-online-advisory.md)).
- **Deliverables:** Dossier (HTML+JSON), `upgrade-check` findings, prioritised
  risk list.

### 6. Team training for AI-assisted Odoo delivery

Teach the team to run the loop: read ground truth first, build the smallest
correct change, prove it with a test, gate it before merge.

- **Outcome:** the team uses Claude Code / Codex with the suite as the safety net
  and can read every gate verdict.
- **Duration:** 1–3 sessions.
- **Prerequisites:** the suite installed; a staging DB to demo against.
- **Deliverables:** session(s), a reproducible worked example, an internal runbook.

---

## Pricing guidance

Fixed fee per service, **set per market**. No numbers are committed in this repo
— pricing is a partner/market decision, not a property of the open tool. Anchor
on: instance size, number of repos/pipelines, verticals in scope, and whether the
client is self-hosted, Odoo.sh, or Odoo Online (advisory-only narrows scope).

No usage fees, no per-seat fees, no license fee for the tool itself — it's LGPL-3.

## Related

- [Pilot package](pilot-package.md) — the standard entry engagement.
- [Governance](governance.md) — the open-standard commitments and the poison list.
- [Artifact governance](artifact-governance.md) — how client artifacts are handled.
- [Odoo Online advisory](odoo-online-advisory.md) — scope when there's no custom code.
