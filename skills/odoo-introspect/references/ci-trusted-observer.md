# CI trusted-observation boundary (Layer L)

The strict gate's decisive runtime facts — `runtime_path` (which changed method
actually executed through the live registry), `changed_coverage`, and the
`scenario_satisfaction` observations — are **measured inside the same `odoo-bin`
process that runs the untrusted PR addon/test code.** CI then HMAC-signs the
resulting files. The signature proves *"CI signed these bytes,"* **not** *"the
observer wasn't tampered with."* Without the wiring below, a hostile test can
disable the recorder (`sys.settrace(None)`), overwrite the evidence file, or
monkeypatch the observer, then hand CI a green file to sign.

This runbook closes that gap operationally. The gate enforces two things CI must
provide (`runtime_observer.py` self-reports them; `deploy_gate.py --strict` blocks
if they're wrong):

1. **Seal** — `runtime_path.producer.trace_integrity == "sealed"` (tracer stayed
   installed *the whole run*, hit the targets, output confirmed **not** test-writable).
   Any other value (`tampered` / `incomplete` / `absent`) → **block**. A disable/
   restore of the tracer is caught by a permanent `sys.addaudithook` settrace
   counter (audit hooks can't be removed), so a brief blind window still → `tampered`.
   Unknown output ownership fails closed → `incomplete`.
2. **Pinned digest — MANDATORY.** `ODOO_AI_EXPECTED_PROBE_DIGEST` must be set to the
   observer's `--self-digest`; the gate blocks on a mismatch (swapped/edited observer)
   and **fails closed to `needs_human` when it is NOT set** (an unpinned observer
   identity is untrusted). Pin it in CI — the gate will not strict-auto-approve without it.

## The isolation the gate cannot enforce itself (CI MUST do this)

The seal is only trustworthy if the PR code cannot forge it. That requires OS/CI
isolation the Python gate can't provide from inside the process:

- **Inject the observer from a CI-owned path, never the repo.** Put
  `runtime_observer.py` (and a `sitecustomize.py` that calls `install()` at
  interpreter startup, before any addon imports) in a directory CI controls, and
  prepend it to `PYTHONPATH`. The PR must not be able to edit it. Pin its hash:
  `ODOO_AI_EXPECTED_PROBE_DIGEST=$(python3 runtime_observer.py --self-digest | jq -r .tool_digest)`.
- **Make the evidence output dir NOT writable by the test process.** Write raw
  observer output to a path owned by a different user / a read-only-to-test mount,
  so the test can't overwrite the events/self-report. The observer reports
  `output_test_writable`; if it can tell the dir is test-writable it seals as
  `tampered`.
- **Sign in a separate, trusted step AFTER the process exits.** The signing key
  (`ODOO_AI_ATTEST_KEY`) must never be in the test container's environment. The
  CI host reads the raw outputs, verifies the self-report is sealed, then signs.
- **Run the observer/coverage as pinned tooling.** Coverage must run with
  `coverage run --branch --context=test_function` (so `changed_coverage_gate` can
  tell a real test from setup); the coverage config comes from CI, not the repo.

## Reference CI sequence

```bash
# 0. CI-owned tooling (outside the PR checkout)
export PROBE_DIR=/opt/odoo-ai/observer          # CI-controlled, PR cannot write
export PYTHONPATH="$PROBE_DIR:$PYTHONPATH"       # sitecustomize.py installs the observer
export ODOO_AI_EXPECTED_PROBE_DIGEST=$(python3 "$PROBE_DIR/runtime_observer.py" --self-digest | jq -r .tool_digest)
export ODOO_AI_EXPECTED_HEAD_SHA=$(git rev-parse HEAD)
export OBS_OUT=/opt/odoo-ai/out/$CI_RUN         # NOT writable by the odoo test user
install -d -o ci -g ci -m 0750 "$OBS_OUT"

# 1. CI derives the change targets FROM GIT (not from the agent)
python3 diff_targets.py --base "$BASE" --head HEAD > "$OBS_OUT/diff_targets.json"

# 2. Run Odoo tests under coverage; the injected observer records + seals
COVERAGE_PROCESS_START=$PROBE_DIR/.coveragerc \
OBS_TARGETS="$OBS_OUT/diff_targets.json" OBS_OUT_PATH="$OBS_OUT/runtime_raw.json" \
coverage run --branch --context=test_function \
  ./odoo-bin -d ci_db -u "$MODULE" --test-enable --stop-after-init --test-tags "$TAGS"
coverage json --show-contexts -o "$OBS_OUT/coverage.json"

# 3. CI assembles the artifacts from the RAW observer output + coverage
python3 runtime_path_probe.py --targets "$OBS_OUT/diff_targets.json" \
    --events "$OBS_OUT/runtime_raw.json" --registry "$OBS_OUT/registry.json" \
    > "$OBS_OUT/runtime_path.json"      # CI merges runtime_raw's producer self-report in
python3 changed_coverage_gate.py --targets "$OBS_OUT/diff_targets.json" \
    --coverage "$OBS_OUT/coverage.json" > "$OBS_OUT/changed_coverage.json"
# ... test_quality, scenario_satisfaction, mutation_smoke, red_green_replay, native_check, validate, scan_secrets

# 4. CI verifies the seal, THEN signs (key only on this trusted host)
python3 runtime_observer.py --evaluate "$OBS_OUT/runtime_raw.json" | jq -e '.sealed == true' \
    || { echo "observer not sealed — refusing to sign"; exit 1; }
export ODOO_AI_ATTEST_KEY=$(cat /run/secrets/odoo_ai_attest_key)   # trusted host only
for f in "$OBS_OUT"/*.json; do
  [ "$f" = "${f%.provenance.json}" ] || continue
  python3 provenance.py attest "$f" --name "$(basename "$f" .json)" \
      --head-sha "$ODOO_AI_EXPECTED_HEAD_SHA" > "${f%.json}.provenance.json"
done

# 5. The gate — content-, commit-, and observer-bound
ODOO_AI_EXPECTED_HEAD_SHA="$ODOO_AI_EXPECTED_HEAD_SHA" \
ODOO_AI_EXPECTED_PROBE_DIGEST="$ODOO_AI_EXPECTED_PROBE_DIGEST" \
python3 deploy_gate.py --strict "$OBS_OUT"
```

## Threat model — what this does and does NOT stop

Stops (with the isolation above): a test that calls `sys.settrace(None)` — even
briefly then restores it (→ seal `tampered` via the permanent audit counter), that
never hits the target (→ `incomplete`), that hand-writes a green `runtime_path.json`
(→ content-binding fails, no valid envelope), that swaps or edits the observer
(→ `tool_digest` mismatch, which is now a mandatory check), that overwrites the
output file (→ dir not test-writable / signature fails), or that omits the pin
(→ `needs_human`, fail-closed).

Does NOT stop (honest residual — this is the real boundary): a sufficiently
sophisticated in-process attacker that **mutates the observer's own state**
(`_tracer_state["events"]`, the heartbeat/audit counters) or monkeypatches
`finalize`/`evaluate_integrity` **before** it seals — the observer's data are
ordinary Python objects reachable from the same interpreter, so this is
tamper-EVIDENCE for naive attacks, not a cryptographic boundary. The only real
defense is **process/container isolation**: the observer module, its runtime
state, and the evidence output must live outside the test's import and write
reach (a separate CI process/user; the child writes raw events, the CI parent
owns finalize + signing). And no gate can tell a test that asserts the *wrong*
business rule from a correct one — that is what human review of
accounting/stock/payment/hr/security/controller changes is for. Treat
`--strict = approve` as a strong hardening gate, not an auto-approve guarantee.
```
