# CI integration — the odoo-ai evidence gate

This is where enforcement becomes real. The `odoo-ai` tool-chain produces an
**evidence bundle** (JSON artifacts from your running Odoo + the local linters);
`deploy_gate.py` aggregates that bundle into one decision:

| decision | meaning |
|---|---|
| `approve` | no automated blocker, all required evidence present & parseable |
| `needs_human` | high/critical risk, missing evidence, or a sign-off is required |
| `block` | a blocking finding (bad patch, leaked secret, unsatisfied proof) |

`deploy_gate.py` **always exits 0** so its JSON is never suppressed. The **CI
layer** — the composite action below, or the inline GitLab job — is what fails
the build. This keeps the tool honest (it always reports) and the pipeline
strict (it always enforces).

> **Trust boundary.** `approve` means "no automated blocker was found and the
> required evidence is present" — never "safe to ship unreviewed". Human review
> stays mandatory for account / stock / payment / HR / payroll changes.

Reference pipelines in this repo:

- `.github/workflows/odoo-ai-gate.yml` — the **local, no-DB** gates (validate,
  scan-secrets, deploy-gate on a committed bundle). Runs on every PR with zero
  infra. Start here.
- `examples/ci/github-workflow-example.yml` — the **instance-backed** pipeline:
  spins Odoo, produces a live bundle, then calls the composite action.
- `examples/ci/gitlab-ci-example.yml` — the GitLab equivalent single job.

---

## GitHub Action: `.github/actions/odoo-gate`

A composite action that runs the gate, posts a sticky PR comment, and enforces.
Paths resolve relative to the action file (`$GITHUB_ACTION_PATH`), so it works
in-repo **and** when `odoo-ai-skills` is consumed as a plugin checkout.

```yaml
- uses: ./.github/actions/odoo-gate          # or: owner/odoo-ai-skills/.github/actions/odoo-gate@v0.14.0
  id: gate
  with:
    bundle_dir: evidence_bundle
    strict: "true"
    fail_closed_severities: "S3,S4"
    comment: "true"
- run: echo "decision = ${{ steps.gate.outputs.decision }}"
```

### Inputs

| input | default | description |
|---|---|---|
| `bundle_dir` | *(required)* | Directory of evidence JSON artifacts to gate. |
| `strict` | `"true"` | Run Layer L policy v2 (`deploy_gate.py --strict`): the hardened, hostile-agent-resistant core (runtime-path binding, changed-line coverage, scenario satisfaction, test-quality, provenance). |
| `fail_closed_severities` | `"S3,S4"` | Comma-separated severities that fail the build when the decision is not `approve`, even if it is only `needs_human`. Findings without a severity (older reports) are never treated as fail-closed. |
| `comment` | `"true"` | Post/update the sticky PR comment (PR events only). |
| `github_token` | `${{ github.token }}` | Token used to post the comment. Needs `pull-requests: write`. |

### Outputs

| output | description |
|---|---|
| `decision` | `approve` \| `needs_human` \| `block` \| `unknown` |

### Enforcement

The action fails the build when:

1. `decision == block` — **always fatal**, or
2. `decision != approve` **and** any finding carries a severity in
   `fail_closed_severities`.

Severity is parsed defensively: a report whose findings have no `severity` field
(the pre-severity-class shape) simply has nothing to fail-closed on.

### Sticky PR comment

`pr_comment.py` renders the gate report as Markdown and upserts a single comment
per PR, keyed on the hidden marker `<!-- odoo-ai-gate -->`. On every push it
**updates that one comment** instead of stacking new ones. The comment carries
the verdict badge, a findings table (with severity/remediation columns when
present), the missing-evidence list, any required sign-offs, and a
"Reproduce locally" block.

```
┌─────────────────────────────────────────────────────────┐
│ ## Odoo AI evidence gate — ⛔ block                     │   ← screenshot
│ Policy: v2-strict • Risk tier: high                     │     placeholder:
│ ### Findings                                            │     one sticky
│ | Finding                     | Severity | Remediation |│     comment,
│ | validate: 1 blocking …      | S3       | use params  |│     updated in
│ ### Reproduce locally  … deploy-gate --strict …         │     place.
└─────────────────────────────────────────────────────────┘
```

---

## GitLab CI recipe

GitLab has no composite-action equivalent, so `examples/ci/gitlab-ci-example.yml`
inlines the same three moves in one job — run the gate, parse the decision, and
fail on `block` or a fail-closed severity (a defensive Python block, no `jq`):

```yaml
odoo-ai-gate:
  stage: gate
  image: python:3.12-slim
  script:
    - python skills/odoo-introspect/scripts/deploy_gate.py --strict "$BUNDLE_DIR" > gate_report.json
    - python - "$FAIL_CLOSED_SEVERITIES" < enforce.py   # fails on block / S3,S4
  artifacts:
    when: always
    paths: [gate_report.json, evidence_bundle/]
```

The sticky-comment step is GitHub-shaped (`pr_comment.py` talks to the GitHub
API via `gh`). On GitLab, post to the merge request from a follow-up job using
the GitLab MR-notes API if you want the same in-PR verdict.

---

## Odoo.sh guidance

**Odoo.sh runs its own CI on push** (module install/upgrade + your tests). This
gate is **complementary, not a replacement**, and it does **not** try to control
Odoo.sh internals:

- Run this gate in **GitHub before / alongside** Odoo.sh — on the same PR, as a
  required status check. It gates the *merge*; Odoo.sh gates the *build*.
- Produce the instance-backed evidence on an **Odoo.sh staging branch** (you have
  a shell there and a copy of production data): run the shell-bound layers,
  commit `evidence_bundle/` to the branch, and let this gate pick it up.
- Odoo.sh **builds can consume the generated tests** — the scenarios / UAT the
  tool-chain emits are ordinary Odoo tests, so they run inside the Odoo.sh test
  build like any other, giving you the runtime proof this gate then verifies.

Do not pretend this gate has authority over the Odoo.sh pipeline; treat the two
as independent checks that must both be green.

---

## Policy presets

Three postures, selected purely by inputs — no code changes:

| preset | `strict` | `fail_closed_severities` | `comment` | build fails when… |
|---|---|---|---|---|
| **advisory** | `"false"` | `""` | `"true"` | never — comment only, informational gate while you roll it out |
| **strict** | `"true"` | `""` | `"true"` | decision is `block` |
| **regulated** | `"true"` | `"S3,S4"` | `"true"` | decision is `block`, **or** any `S3`/`S4` finding is present without an `approve` (i.e. no human sign-off cleared it) |

Start **advisory** to see verdicts without breaking anyone, then move to
**strict**, then **regulated** for finance/ops repos where S3/S4 must never merge
on an automated pass alone.

### Branch protection

To make the gate binding, add it as a required status check:

1. **Settings → Branches → Branch protection rules** for your default branch.
2. Enable **Require status checks to pass before merging**.
3. Select the gate job (e.g. `gate` from the example workflow).
4. Optionally enable **Require branches to be up to date** so the gate re-runs on
   the merged result.

With the check required, a `block` (or a fail-closed severity under the
*regulated* preset) makes the PR un-mergeable until fixed or explicitly signed
off by a human.

---

## Reproduce locally

Every sticky comment ends with the exact commands to reproduce its verdict on
your machine — no CI round-trip:

```bash
# via the CLI (plugin-relative)
"${CLAUDE_PLUGIN_ROOT:-.}"/skills/odoo-introspect/scripts/odoo-ai deploy-gate --strict evidence_bundle

# or the script directly
python skills/odoo-introspect/scripts/deploy_gate.py --strict evidence_bundle

# render the PR comment markdown from a report you already have
python skills/odoo-introspect/scripts/pr_comment.py build gate_report.json --bundle-dir evidence_bundle
```

`deploy_gate.py` exits 0 and prints the report; read `.decision.decision` for the
verdict. To post the sticky comment yourself (needs `gh` + `GITHUB_TOKEN`):

```bash
python skills/odoo-introspect/scripts/pr_comment.py post gate_report.json \
  --repo owner/name --pr 123 --bundle-dir evidence_bundle
```
