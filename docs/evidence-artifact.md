# Evidence Artifact v1

The **Evidence Artifact** is the stable, public, machine-validatable record of an
AI-written Odoo change — the single JSON document a partner shows a client. It
answers three questions in a form a dashboard, PR bot, or auditor can parse
without knowing anything about the tool-chain internals:

1. **What was checked** — `checks[]`, one row per inspect/verify/gate/report probe.
2. **How sure we are** — each check's `severity` (S0–S4) and `cache_provenance`.
3. **Whether a human still has to sign** — `decision` + `human_signoffs`.

- **Schema version:** `1.0`
- **Producer:** `evidence_schema.py build <bundle_dir>` (assembled from a deploy-gate bundle)
- **Validator:** `evidence_schema.py validate <artifact.json>` → `{"ok", "errors"}`
- **Dependencies:** Python 3.12 stdlib only (validation is hand-rolled — no `jsonschema`).

---

## Trust boundary

Evidence Artifact v1 is a **CI-bound evidence gate with an explicit trust
boundary**: CI produces the artifact and binds it to the diff, while a local run
only *advises*.

- **CI produces and binds evidence to the diff.** The signing key
  (`ODOO_AI_ATTEST_KEY`) and the expected head (`ODOO_AI_EXPECTED_HEAD_SHA`) live
  only on the CI host — never in the agent's environment. So an artifact whose
  checks all `pass` is trustworthy *because CI, not the agent, produced and signed
  it against the commit under review*.
- **Local runs advise.** `build_artifact` off a developer box still emits a valid
  artifact, but `git.commit_sha` may be `null` and no CI attestation binds it — a
  reviewer treats it as a draft, not a merge authorization.
- **A `decision` of `approve` is never a substitute for the required human
  sign-off.** When `human_signoffs` are required (see `decision.required_approvals`),
  the merge is not authorized until those roles have signed, regardless of the
  automated verdict.

---

## The warm-cache invariant

> **A warm cache read can never support an `approve` decision.**

`validate_artifact` **rejects** any artifact where `decision.decision == "approve"`
and *any* check has `cache_provenance == "warm"` **and** `layer` in
(`gate`, `verify`) — with the error:

```
warm-cache evidence cannot support an approve decision
```

Rationale: gate/verify checks decide whether the change is safe to merge. A
`warm` (possibly-stale) read of that evidence is not fresh enough to auto-approve —
it must be re-run `cold`, or the decision must fall back to `needs_human`. A
`warm` read on an `inspect` or `report` check is advisory and does **not** trip the
invariant.

| `cache_provenance` | meaning |
|---|---|
| `cold` | freshly computed for this run (or read from a CI-produced bundle) |
| `warm` | served from a cache; may predate the current change |
| `stale-rejected` | cache entry was found but rejected as too old / mismatched, and recomputed |

---

## Severity classes

Each check carries a severity class. A change's automated risk score is the
weighted sum of its **failing** checks.

| class | weight | meaning |
|---|---|---|
| `S0` | ×1 | cosmetic (label / help text / whitespace) |
| `S1` | ×2 | functional annoyance (recoverable, no data impact) |
| `S2` | ×4 | install / upgrade breakage |
| `S3` | ×8 | business data corruption |
| `S4` | ×10–12 | silent failure / security / multi-company breach |

When the severity of a synthesized check is unknown (the deploy-gate report does
not carry per-check severity), `build_artifact` defaults it to **`S2`**.

---

## Field reference

| field | type | required | notes |
|---|---|---|---|
| `schema_version` | string | ✅ | must be `"1.0"` |
| `generated_at` | string (ISO-8601) | ✅ | when the artifact was assembled |
| `git` | object | ✅ | commit binding |
| `git.commit_sha` | string \| null | ✅ | `git rev-parse HEAD`; `null` off a repo |
| `git.diff_hash` | string \| null | ⬜ | optional hash of the diff under review |
| `git.branch` | string \| null | ⬜ | `git rev-parse --abbrev-ref HEAD` |
| `instance` | object | ⬜ | live-instance fingerprints (all optional strings) |
| `instance.db_fingerprint` | string | ⬜ | |
| `instance.odoo_version` | string | ⬜ | e.g. `"18.0"` |
| `instance.module_graph_hash` | string | ⬜ | |
| `addons` | object | ⬜ | |
| `addons.addon_hash` | string | ⬜ | |
| `checks` | array | ✅ | one row per probe (see below) |
| `checks[].id` | string | ✅ | non-empty; the probe's stable id |
| `checks[].layer` | enum | ✅ | `inspect` \| `verify` \| `gate` \| `report` |
| `checks[].status` | enum | ✅ | `pass` \| `fail` \| `skip` |
| `checks[].severity` | enum | ✅ | `S0` \| `S1` \| `S2` \| `S3` \| `S4` |
| `checks[].cache_provenance` | enum | ✅ | `cold` \| `warm` \| `stale-rejected` |
| `checks[].summary` | string | ✅ | human-readable one-liner |
| `checks[].logs_path` | string \| null | ⬜ | pointer to raw logs, if any |
| `decision` | object | ✅ | the gate verdict |
| `decision.decision` | enum | ✅ | `approve` \| `needs_human` \| `block` |
| `decision.blocking_findings` | array of string | ✅ | reasons a `block` was issued |
| `decision.required_approvals` | array of string | ✅ | human roles that must sign |
| `human_signoffs` | array | ✅ | may be empty |
| `human_signoffs[].role` | string | ✅ | e.g. `"senior-dev"` |
| `human_signoffs[].name` | string | ⬜ | optional |
| `human_signoffs[].at` | string (ISO-8601) | ✅ | when the sign-off happened |
| `redaction` | object | ✅ | external-share safety |
| `redaction.mode` | enum | ✅ | `external` \| `local` |
| `redaction.scanned` | bool | ✅ | whether a redaction pass ran |

**Layer mapping.** `build_artifact` maps deploy-gate artifact names onto the four
public layers — Inspect (`native_check`, `env_diff`, `diff_targets`), Verify
(`trace`, `runtime_path`, `changed_coverage`, `scenario_satisfaction`,
`mutation_smoke`, `red_green_replay`), Gate (`validate`, `scenarios`, `security`,
`upgrade`, `scan_secrets`, `test_quality`, `provenance`), Report (`evidence`).
Anything unmapped defaults to the conservative `gate` bucket.

---

## Full example

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-07-03T12:00:00+00:00",
  "git": {
    "commit_sha": "998e7d17f092c1a4b0f3e2d1c9a8b7654321fedc",
    "diff_hash": null,
    "branch": "feat/sale-margin-fix"
  },
  "instance": {
    "db_fingerprint": "sha256:1f3c…",
    "odoo_version": "18.0",
    "module_graph_hash": "sha256:9ab2…"
  },
  "addons": { "addon_hash": "sha256:c0ffee…" },
  "checks": [
    {
      "id": "validate",
      "layer": "gate",
      "status": "pass",
      "severity": "S2",
      "cache_provenance": "cold",
      "summary": "validate: evidence present and parseable"
    },
    {
      "id": "scenarios",
      "layer": "gate",
      "status": "pass",
      "severity": "S2",
      "cache_provenance": "cold",
      "summary": "scenarios: risk tier normal"
    },
    {
      "id": "native_check",
      "layer": "inspect",
      "status": "pass",
      "severity": "S2",
      "cache_provenance": "cold",
      "summary": "native_check: evidence present and parseable"
    },
    {
      "id": "scan_secrets",
      "layer": "gate",
      "status": "pass",
      "severity": "S2",
      "cache_provenance": "cold",
      "summary": "scan_secrets: 0 potential secrets"
    }
  ],
  "decision": {
    "decision": "approve",
    "blocking_findings": [],
    "required_approvals": []
  },
  "human_signoffs": [],
  "redaction": { "mode": "external", "scanned": true }
}
```

---

## How CI binds the artifact to the diff

The `.github/workflows/odoo-ai-gate.yml` workflow is what makes the evidence
trustworthy rather than self-asserted:

1. **Checkout with full history** (`fetch-depth: 0`) so a `git diff` against the PR
   base branch is reliable.
2. **Resolve the changed files** — on `pull_request`, the workflow diffs
   `origin/<base>...HEAD` for `.py`/`.xml` paths (excluding test fixtures, whose
   example secrets are intentional), then runs the local gates (`validate`,
   `scan-secrets`, `deploy-gate`) against exactly those files.
3. **Bind to *this* commit.** In strict mode the deploy gate verifies HMAC
   provenance envelopes whose `subject.head_sha` must equal
   `ODOO_AI_EXPECTED_HEAD_SHA` — a value set only by CI. A replayed bundle signed
   against an older commit is rejected, so the evidence is bound to the diff under
   review and cannot be reused across commits.
4. **Stamp the artifact.** `build_artifact` records `git.commit_sha` /
   `git.branch` from `git rev-parse` at the bundle location, tying the emitted
   Evidence Artifact to the same commit the gate evaluated.

Because the signing key and the expected head live only on the CI host, an agent
cannot forge a green artifact: it can produce the JSON, but it cannot make CI bind
it to the commit. That is the whole point of the trust boundary.

See also: `skills/odoo-introspect/scripts/deploy_gate.py` (the gate that produces
`checks` + `decision`) and `skills/odoo-deploy/references/ci-integration.md` (the
odoo.sh / Docker recipe for producing the shell-bound evidence a bundle needs).
