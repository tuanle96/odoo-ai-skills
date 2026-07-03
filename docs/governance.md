# Governance

odoo-ai-skills is a local-first evidence layer for AI-assisted Odoo delivery.
Its value depends on being **trusted**, and trust depends on the standard being
**open and neutral** — not owned by whoever also sells the enablement around it.

This document states the commitments that keep it that way, and the concrete path
toward neutral, external co-maintenance over the next ~12 months.

---

## What moves toward neutral governance

The goal is not to hand the whole repo to a foundation. It is to move the parts
that must be **independent to be credible** toward neutral, external
co-maintenance (OCA and/or partner co-maintainers):

- **Benchmark cases** — the corpus of classic LLM-Odoo mistakes the `eval`
  harness scores against. If we score ourselves against a benchmark we own in
  secret, the score means nothing.
- **Evidence artifact schema** — the shape of a signed evidence bundle. An open
  schema means anyone can read, validate, and reproduce a verdict.
- **Scenario definitions** — the mapping from introspection to the mandatory
  tests for a change. Open definitions mean the gate is auditable.
- **Fixture patterns** — the generic test-data patterns (partner-specific
  content stays with the partner).

The introspection engine and CLI stay maintained here under LGPL-3; the items
above are what we actively work to make externally governed.

## Release discipline

- **SemVer.** Versions follow semantic versioning; breaking changes bump major.
- **CHANGELOG.** Every release is recorded in [`CHANGELOG.md`](../CHANGELOG.md).
- **Signed tags (aspiration).** Move toward signed release tags so a release can
  be verified, not just trusted. Tracked as a roadmap item (v0.14/v0.15).

## Security process

Security is handled under [`SECURITY.md`](../SECURITY.md): private vulnerability
reporting (GitHub Security advisories or the maintainer email), the redaction
guarantees and their limits, and the "handling introspection output" guidance.
Evidence-artifact handling is detailed in
[artifact-governance.md](artifact-governance.md); a leaked dump is treated as a
security incident under `SECURITY.md`.

## Contribution paths

- **Code / skills / gates** — via `CONTRIBUTING.md` (the import-safe script
  pattern, unit + integration tests). Every script compiles in CI and the pure
  logic is unit-tested without Odoo.
- **Benchmark cases** — new classic-mistake cases for the `eval` corpus are
  especially welcome; they are the standard's evidence base.
- **Scenario / fixture patterns** — generic patterns land upstream; partner
  verticals stay private to the partner (see
  [partner-enablement.md](partner-enablement.md)).

---

## The poison list

Things this project will **never** do, because each one converts an open
evidence standard into a closed rent-seeking one and destroys the trust the whole
thing runs on:

1. **No paywalled safety benchmark.** The benchmark that measures whether the
   gate actually reduces hallucinations stays open. We do not sell access to the
   yardstick.
2. **No self-issued paid "Verified" certification badge.** We do not sell a badge
   that says a partner's work is safe. (See the narrow exception below — which,
   absent neutral rules and external maintainers, means *never*.)
3. **No closed scoring rules.** How a change is scored `approve | needs-human |
   block` is open and reproducible. No secret rubric.
4. **No SaaS-gated verifier.** The core verifier is not put behind a hosted
   service, an API key, or a seat. Local-first is the product.
5. **No vendor-owned "independent" certification.** We do not run a certification
   that calls itself independent while being owned by the same party that sells
   the enablement. That is a conflict of interest by construction.

The business model is **paid enablement around the open standard**
([partner-enablement.md](partner-enablement.md)) — with a fleet evidence
dashboard as a possible *later* addition. Neither requires closing the standard,
and closing it would poison both.

## When could a paid certification ever exist?

Only if **both** hold, permanently:

- The **scoring rules are neutral and open** — anyone can read them, reproduce a
  verdict, and dispute it against a public benchmark.
- **External maintainers** (not the enablement vendor) own the certification
  rules and can overrule the vendor.

Until both are true, the answer is **no** — a certification issued by the party
that profits from passing it is worthless, and worse, it is
"hallucination with authority." Absent neutral rules and independent maintainers,
this never ships.

## Related

- [Partner enablement](partner-enablement.md) — the open-standard business model.
- [Artifact governance](artifact-governance.md) — sensitive-evidence handling.
- [Pilot package](pilot-package.md) — the standard entry engagement.
- [`SECURITY.md`](../SECURITY.md) · [`CHANGELOG.md`](../CHANGELOG.md) · `CONTRIBUTING.md`
