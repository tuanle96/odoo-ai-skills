# Sustainability

## License and openness

odoo-ai-skills is **LGPL-3.0-or-later**.  The engine — every introspection layer, every enforcement gate, the CLI, the skill files, the curated capability cards — is open and complete.  There are no "community" vs "enterprise" tiers, no gated features, and no proprietary fork intended.  The full suite ships in this repo.

## What we do not sell

We do not sell seats.  We do not sell API access to a hosted version of this tool.  We do not sell a "pro" version with extra gates.  If we did, the incentive would be to make the open version just good enough to upsell — the opposite of what this project is trying to do.

## How the project sustains itself

The engine is the open core.  Sustainability comes from **services that reduce the risk of AI-written Odoo changes breaking client systems** — not from charging for the tool itself.

**Partner support subscriptions.**  Odoo integrators and development shops that stake their delivery quality on this suite can subscribe for priority bug reports, version-compatibility SLAs, and direct maintainer access.  The cost covers the maintenance work that keeps the suite current across Odoo 17/18/19 and beyond.

**Enterprise implementation.**  On-premises deployments with custom redaction policies (e.g., sector-specific PII rules beyond the defaults), evidence-bundle customization for internal compliance workflows, or integration with internal CI/CD pipelines beyond the bundled GitHub Actions template.  These require scoped engagement work; they are billed as implementation, not as a license.

**Sponsored compatibility tracks.**  When a new Odoo major version ships, an integrator or ISV that needs tested compatibility on day one can sponsor that work.  The result lands in the open repo — the sponsor gets the outcome sooner and with a contractual commitment; the community gets it shortly after.  Example: "Odoo 20 support sponsored by [Partner]."

**Training and certification.**  Structured training (for both developers and team leads) on using this suite as part of an Odoo AI development workflow — covering the introspection → gate → review cycle, reading the evidence bundle, and operating the enforcement gates in a production CI pipeline.

**Maintainer retainers.**  Organizations that depend on this suite for regulated or high-stakes deployments (accounting, payroll, public health, logistics) can retain a maintainer directly to ensure the suite tracks their specific Odoo configuration, module set, and compliance requirements.

## The principle

> We keep the engine open and complete.  You pay to reduce the risk of AI-written Odoo changes breaking client systems — not for seats.

The tool's value is in what it verifies: that an AI-generated patch is safe against the *real* instance, the *real* users, the *real* upgrade path.  That value only exists if the verification is honest — meaning open, auditable, and not incentivized to understate what the gates find.  Keeping the full suite open is not a concession; it is the product.
