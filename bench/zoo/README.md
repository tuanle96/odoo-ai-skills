# Adversarial Zoo — living corpus

The **zoo** is the living counterpart to the frozen `bench/tasks/v0/` suite. It
collects unsafe-change cases *before* they are stable enough to freeze:
community submissions, bugs seen in real projects, and — most importantly —
every gate **miss** from a published run.

Frozen suites make results comparable over time; the zoo is where new failure
modes accumulate. Periodically maintainers curate a batch of zoo cases into the
next frozen suite version (`v1`, `v2`, …).

---

## The regression rule (non-negotiable)

> **Every gate MISS in a published run MUST land here as a regression case.**

A miss is an unsafe change that escaped (`unsafe_merged=true, detected=false`) in
a mode that was *supposed* to catch it (`agent_gate` / `agent_context_gate`).
When we publish that miss, we also add it to the zoo — so the same class of
failure can never silently return. Embarrassing results are the fuel: a bench
that only keeps its wins is a marketing asset, not a benchmark.

---

## Submission format

A zoo case is a **superset of the standard task schema** (see
`../task-schema.md`) plus three provenance fields. Copy `TEMPLATE.json` to
`bench/zoo/<short-slug>.json` and fill it in.

Extra fields beyond the task schema:

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `reported_by` | string | who found it (handle / org, or `"bench-run:<date>"` for a published miss) |
| `seen_in_wild` | bool | `true` if it occurred in a real project, `false` if it is a constructed adversarial case |
| `provenance` | string | short note: the ticket, the run, the CVE class, or the reasoning that produced it |

All the normal task rules still apply:

- `context_files` minimal (one model or view, < 80 lines), self-contained.
- `broken_patch` internally consistent with `context_files` and *plausible* —
  code an LLM would actually write, not a contrived trap.
- `severity_class` + `weight` from the table in `../README.md`.
- `expected_detection.check` phrased in the suite's vocabulary, with an
  `artifact` filename pattern.
- `fixed_patch` = the correct minimal change (or `broken_patch` if the case is a
  safe false-positive probe with `"safe": true`).

---

## How to contribute

1. `cp bench/zoo/TEMPLATE.json bench/zoo/<short-slug>.json`
2. Fill in the schema + provenance fields.
3. Verify it parses and scores:
   ```bash
   python3 -c "import json; json.load(open('bench/zoo/<short-slug>.json'))"
   ```
   (The scorer accepts any directory of task JSONs via `--tasks-dir`, so you can
   dry-run a case against a hand-written result record.)
4. Open a PR. Maintainers reproduce it in the pinned environment
   (`../RUNBOOK.md`). Accepted cases live here; strong, stable ones are promoted
   into the next frozen suite.

---

## Promotion to a frozen suite

When a zoo case is promoted:
- it is re-`id`'d to the frozen convention (`vN-NNN-slug`),
- its provenance fields are preserved in `notes`,
- and it becomes immutable in that suite version.

The zoo file may then be removed or kept as a pointer — either way the case is
now permanently part of a comparable, versioned suite.
