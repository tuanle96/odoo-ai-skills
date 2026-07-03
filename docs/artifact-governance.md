# Artifact governance

Evidence and introspection artifacts are **sensitive by design**. The whole point
of the suite is to read ground truth from a running Odoo instance — so its output
carries whatever that instance holds: secrets, tokens, customer PII, proprietary
logic. A leaked dump does not just expose data; it breaks the **local-first trust
narrative** the project is built on. Treat these artifacts accordingly.

This is the operational companion to [`SECURITY.md`](../SECURITY.md) and
[governance.md](governance.md).

---

## What artifacts can contain

- **State dumps** (`state` capture) — runtime args, locals, and `self` field
  *values* at a breakpoint or on exception.
- **Source bodies** — `SOURCE=1` on a model brief includes full method source.
- **Config keys** — `ir.config_parameter` values, server-action code.
- **Domains & record-rule bodies** — can embed IDs, business logic, thresholds.
- **Evidence bundles** — the aggregated inputs to `deploy-gate`, which reference
  the above.

Key-name redaction masks obvious sensitive *keys* (`password`, `token`, …) but
**does not** catch a secret under a benign name, and does **not** redact `--fields`
values or `SOURCE=1` bodies. Those are sensitive until proven otherwise.

## The hard rule

> **Never paste raw state/source JSON into chats, tickets, or PRs. Always run
> `redact` first.**

This is not advisory. Raw introspection output going into an external LLM, a
public issue, a shared channel, or a PR description is a data-handling failure.

## Redaction profiles

Redaction (`redaction.py` / `odoo-ai redact`) has two modes:

| Mode | What it does | When it's mandatory |
|------|--------------|---------------------|
| `external` *(default)* | Strips `source` / `code` / `locals` wholesale, masks PII, redacts sensitive-key values, redacts strings under sensitive-model records. | **Before anything leaves the machine** — external LLM, shared channel, ticket, PR, client hand-off. |
| `local` | Redacts sensitive-key values only; keeps `source` / `locals` intact. | Only on a trusted dev box, for in-loop agent context that never leaves your environment. |

```bash
# Before ANY external hand-off — strip source/locals, mask PII:
odoo-ai redact /tmp/odoo-ai/sale_order.state.json           # external mode (default)

# Trusted dev box, staying local:
odoo-ai redact /tmp/odoo-ai/sale_order.state.json --mode local

# Belt-and-braces: scan for secrets the key-name redactor might miss:
odoo-ai scan-secrets /tmp/odoo-ai/sale_order.state.json
```

`external` mode is the default precisely because "about to share" is the
dangerous moment. If in doubt, run `external` and `scan-secrets`.

## Storage rules

- **Project-local evidence directory**, never in git. Put artifacts under a
  dedicated dir (e.g. `.odoo-ai/evidence/` or `/tmp/odoo-ai/`) and add it to
  `.gitignore`:

  ```gitignore
  # odoo-ai evidence & introspection output — never commit
  .odoo-ai/
  /tmp/odoo-ai/
  *.state.json
  *evidence_bundle*/
  ```

- **Directory permissions `0700`** — evidence dirs readable only by the owner:

  ```bash
  mkdir -p .odoo-ai/evidence && chmod 0700 .odoo-ai .odoo-ai/evidence
  ```

- **Encryption at rest (recommended).** Keep evidence on an encrypted volume
  (FileVault / LUKS / encrypted disk image). For longer-lived bundles, encrypt
  the archive itself.

## Retention

- **Delete after merge + N days** (default policy). Once a PR is merged and the
  evidence has served its purpose, it is liability, not asset. A short default
  retention window (e.g. 7–14 days) keeps just enough for audit/dispute.
- Do not accumulate evidence bundles indefinitely "just in case." Each one is a
  copy of instance state.
- CI-produced bundles: set the artifact retention on the CI side too, not just
  locally.

## Client-safe export (roadmap)

A **Dossier anonymization mode** — producing a client-shareable Dossier with PII
and instance-identifying detail stripped — is planned for **v0.15**. Until it
ships, a client hand-off runs through `redact --mode external` + `scan-secrets`
and a manual review; do not assume a Dossier is safe to forward untouched.

## Incident handling

A leaked dump is a **security incident**, handled per
[`SECURITY.md`](../SECURITY.md): report privately, assess what was exposed
(secrets → rotate; PII → follow the client's breach process), and record the
handling gap. Beyond the direct exposure, a leak damages the local-first trust
narrative — so treat even a "small" leak seriously.

## Related

- [`SECURITY.md`](../SECURITY.md) — the security policy and redaction guarantees.
- [Governance](governance.md) — the poison list and open-standard commitments.
- [Pilot package](pilot-package.md) — where the evidence-artifact review happens.
- [Partner enablement](partner-enablement.md) — artifacts belong to the client.
