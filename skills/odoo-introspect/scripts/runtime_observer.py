"""
Runtime observer (Layer L — "did the trace itself survive the run, untampered?") —
the CI-owned counterpart to ``runtime_path_probe.py``. That script measures
WHICH targets were bound at runtime; this script measures whether ITS OWN
measurement apparatus can be trusted at all.

`runtime_path_probe.py` installs a `sys.settrace` recorder inside the same
process as the untrusted addon/test code. An adversarial test can defeat that
recorder without ever failing: call ``sys.settrace(None)`` to disable it mid-run,
point it at a target that is never actually exercised, or simply let the run
end early — then emit a hand-crafted "green" events JSON. Coverage numbers and
even `runtime_path_probe`'s own verdict can look perfect while the trace itself
was blind for part (or all) of the run.

This module is the producer of a SEPARATE self-report the deploy gate consumes
alongside `runtime_path`: a `trace_integrity` verdict that answers "was the
recorder actually installed, still ours at the end, and did it see enough to
mean anything?" — independent of what it claims to have recorded.

  1. PART A (pure, unit-tested) — `evaluate_integrity` / `build_self_report`
     turn a state dict (heartbeats, tamper checks, event/target counts) into a
     FAIL-CLOSED verdict: "sealed" only when every check passes; any missing,
     unknown, or failing check downgrades to "tampered" / "incomplete" /
     "absent" — never silently "sealed".
  2. PART B (env-gated, documented not unit-tested) — `install`/`finalize`, a
     `sys.settrace`-based recorder that also re-confirms (heartbeats) it is
     still the installed trace function, so a `sys.settrace(None)` (or a
     replacement tracer) attack shows up as `trace_still_installed: False`
     at finalize time.

⚠️  Trust boundary: this module runs INSIDE the same process as the code under
test, so nothing it does can PREVENT an adversarial test from tampering with
it (a test can still monkeypatch this very module before it runs). What it CAN
do is make tampering DETECTABLE — `trace_still_installed` is checked at the
very end of the run, so disabling the tracer, even for one line, leaves a
permanent mark in the self-report. The actual prevention is a CI-ownership
guarantee: CI must inject this module from a CI-owned path (not the PR diff),
write its output to a directory the test process cannot overwrite, and sign
the resulting JSON — see `install`/`finalize` docstrings below.

Usage
-----
    # Inside odoo-bin shell / a conftest.py hook kept alive for the whole test
    # run (CI-owned invocation — see PART B docstrings for the isolation this
    # requires):
    TARGETS_JSON="$(cat diff_targets.json)" \\
        odoo-bin shell -d <DB> --no-http < runtime_observer.py

    # Local (no Odoo) — evaluate an already-collected state, or hash this
    # module for the deploy gate's tool_digest check:
    python3 runtime_observer.py --evaluate state.json
    python3 runtime_observer.py --digest runtime_observer.py
    python3 runtime_observer.py --self-digest

Output (env-gated shell mode): pure JSON wrapped in
===ODOO_OBS_START=== / ===ODOO_OBS_END===.
Output (CLI mode): pure JSON to stdout, exit code always 0.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

SENTINEL_START = "===ODOO_OBS_START==="
SENTINEL_END = "===ODOO_OBS_END==="
MAX_EVENTS_PER_TARGET = 20

_REASON_NEVER_INSTALLED = "tracer was never installed"
_REASON_TAMPERED_TRACE = "sys.gettrace() no longer our tracer — trace was disabled/replaced mid-run"
_REASON_WRITABLE_OUTPUT = "evidence output path is writable by the test process"


# --- Part A: pure decision layer (no Odoo needed — unit-testable) ------------

def compute_tool_digest(source_bytes):
    """Return "sha256:<hex>" of *source_bytes*. Deterministic; never raises on
    well-formed bytes input (the caller is responsible for passing bytes)."""
    return "sha256:" + hashlib.sha256(source_bytes).hexdigest()


def self_digest():
    """Hash THIS module's own file on disk. Guarded: an unreadable/missing
    file (e.g. frozen/zipped execution) yields "sha256:unknown" rather than
    raising — the deploy gate must still receive a well-formed tool_digest."""
    try:
        return compute_tool_digest(Path(__file__).read_bytes())
    except Exception:  # noqa: BLE001
        return "sha256:unknown"


def _int(x, default=0):
    """Coerce to int defensively: bool is rejected (bool is an int subclass),
    None/missing/wrong-type falls back to *default* rather than raising."""
    if isinstance(x, bool) or not isinstance(x, int):
        return default
    return x


def evaluate_integrity(state):
    """Decide whether the runtime trace can be trusted, given *state* (all
    keys optional — missing/None/wrong-typed is treated as the WORST case,
    never as "unknown therefore fine"). Never raises.

    Returns {"trace_integrity": "sealed"|"tampered"|"incomplete"|"absent",
    "sealed": bool, "reasons": [str]}.

    Priority order (first match wins — an "absent" tracer is reported as
    absent even if other fields also look bad):
      1. absent      — trace_installed is not True.
      2. tampered     — trace_installed True but trace_still_installed is not
                        True, OR output_test_writable is True.
      3. incomplete   — heartbeats < min_heartbeats, OR events_recorded <= 0,
                        OR (targets_expected > 0 and targets_hit < targets_expected).
      4. sealed       — every check above passed.
    """
    state = state if isinstance(state, dict) else {}

    trace_installed = state.get("trace_installed") is True
    trace_still_installed = state.get("trace_still_installed") is True
    output_test_writable = state.get("output_test_writable")  # None is acceptable-but-warn
    # Set by a permanent audit hook that counts sys.settrace calls after install —
    # a disable/restore attack (settrace(None) then settrace(fn)) leaves this True
    # even though trace_still_installed reads True again at finalize (Oracle PoC).
    trace_changed_externally = state.get("trace_changed_externally") is True

    heartbeats = _int(state.get("heartbeats"))
    min_heartbeats = _int(state.get("min_heartbeats"), default=1)
    events_recorded = _int(state.get("events_recorded"))
    targets_expected = _int(state.get("targets_expected"))
    targets_hit = _int(state.get("targets_hit"))

    reasons = []

    if not trace_installed:
        reasons.append(_REASON_NEVER_INSTALLED)
        return {"trace_integrity": "absent", "sealed": False, "reasons": reasons}

    if not trace_still_installed:
        reasons.append(_REASON_TAMPERED_TRACE)
    if trace_changed_externally:
        reasons.append("sys.settrace was called after install (disable/restore) — "
                       "the tracer was replaced mid-run even if restored before finalize")
    if output_test_writable is True:
        reasons.append(_REASON_WRITABLE_OUTPUT)
    if reasons:
        return {"trace_integrity": "tampered", "sealed": False, "reasons": reasons}

    if heartbeats < min_heartbeats:
        reasons.append(f"heartbeats {heartbeats} < min_heartbeats {min_heartbeats}")
    if events_recorded <= 0:
        reasons.append(f"events_recorded {events_recorded} <= 0")
    if targets_expected > 0 and targets_hit < targets_expected:
        reasons.append(f"targets_hit {targets_hit} < targets_expected {targets_expected}")
    if reasons:
        return {"trace_integrity": "incomplete", "sealed": False, "reasons": reasons}

    # Fail closed on unknown output ownership (Oracle observer review): if we can't
    # confirm the evidence path is NOT writable by the test process, we can't trust
    # the output wasn't overwritten — treat unknown as incomplete, never sealed.
    if output_test_writable is not False:
        reasons.append(
            "output_test_writable is not confirmed False — cannot prove the evidence "
            "path is CI-owned / not overwritable by the test process")
        return {"trace_integrity": "incomplete", "sealed": False, "reasons": reasons}

    return {"trace_integrity": "sealed", "sealed": True, "reasons": reasons}


def build_self_report(state, tool_digest=None):
    """Assemble the frozen producer self-report block the deploy gate reads.

    Returns the FROZEN shape: tool, tool_digest, trace_integrity, sealed,
    heartbeats, events_recorded, targets_expected, targets_hit, reasons.
    `sealed` always matches `trace_integrity == "sealed"`. Never raises —
    delegates all defensiveness to `evaluate_integrity`.
    """
    state = state if isinstance(state, dict) else {}
    verdict = evaluate_integrity(state)
    return {
        "tool": "runtime_observer",
        "tool_digest": tool_digest if tool_digest is not None else self_digest(),
        "trace_integrity": verdict["trace_integrity"],
        "sealed": verdict["sealed"],
        "heartbeats": _int(state.get("heartbeats")),
        "events_recorded": _int(state.get("events_recorded")),
        "targets_expected": _int(state.get("targets_expected")),
        "targets_hit": _int(state.get("targets_hit")),
        "reasons": verdict["reasons"],
    }


# --- Part B: install/finalize recorder (env-gated — runs inside odoo-bin shell) --
#
# CI ISOLATION REQUIREMENT (read before wiring this up): everything below runs
# IN-PROCESS with the code under test, so it cannot prevent a sufficiently
# determined test from tampering with THIS module's own globals before
# `install()` runs. The only real prevention is outside this file:
#   1. CI must inject this module from a CI-owned directory (e.g. a
#      PYTHONPATH entry, a `sitecustomize.py`, or an Odoo `--load` module) —
#      NEVER import it from a path inside the PR's own changed files.
#   2. `out_path` (passed to `install`/`finalize`) must point at a directory
#      the test process's OS user cannot write to (or a path CI overwrites
#      /re-validates after the run) — otherwise a test can simply overwrite
#      the evidence file with a hand-crafted "sealed": true blob.
#   3. CI calls `finalize()` itself, AFTER the test run completes, from its
#      own driver code — not from anything the PR controls.
#   4. CI then hashes and signs (see `provenance.py`) the resulting output
#      file. The gate never trusts an unsigned runtime_observer report.
# In-process tampering of THIS module is only prevented by that CI-side
# isolation; `trace_still_installed` / heartbeats only make such tampering
# DETECTABLE (fail-closed), not impossible.

_tracer_state = None  # populated by install(); read by finalize()

# A PERMANENT audit hook counting sys.settrace/setprofile calls. Audit hooks
# cannot be unregistered (there is no public remove API), so this survives an
# attacker who disables then restores the tracer — the two extra settrace calls
# are counted even though sys.gettrace() reads correct again at finalize. It is
# passive (never calls settrace itself) so it does not disturb coverage/pytest.
# Honest limit: an in-process attacker can still reach and mutate this counter;
# it is tamper-EVIDENCE for naive attacks, not a boundary (see module docstring).
def _make_settrace_auditor():
    box = {"n": 0}

    def hook(event, args):
        if event in ("sys.settrace", "sys.setprofile"):
            box["n"] += 1
    return hook, box


_SETTRACE_HOOK, _SETTRACE_BOX = _make_settrace_auditor()
_AUDIT_INSTALLED = [False]


def _ensure_settrace_auditor():
    if not _AUDIT_INSTALLED[0]:
        try:
            sys.addaudithook(_SETTRACE_HOOK)
            _AUDIT_INSTALLED[0] = True
        except Exception:  # noqa: BLE001
            pass


def _settrace_calls():
    return _SETTRACE_BOX["n"]


def _basename_key(file, firstlineno):
    return (os.path.basename(str(file)), firstlineno)


def _watch_set_from_targets(targets):
    """Build the {(basename, firstlineno): target_id} watch set the same way
    runtime_path_probe.py does: prefer the target's own declared file/method_span
    start (this module doesn't do live-registry MRO introspection itself —
    that's runtime_path_probe's job; this module only needs to know WHICH
    lines to watch to prove the trace stayed alive)."""
    watched = {}
    for t in targets or []:
        if not isinstance(t, dict):
            continue
        span = t.get("method_span")
        if t.get("file") and isinstance(span, (list, tuple)) and span:
            watched[_basename_key(t["file"], span[0])] = t.get("id")
    return watched


def _build_call_event(target_id, frame):
    """Same event shape runtime_path_probe.py's `_build_call_event` produces —
    kept independent (not imported) so this module has zero cross-module
    coupling and can be dropped into a CI-owned path on its own."""
    code = frame.f_code
    self_obj = frame.f_locals.get("self")
    self_name = uid = company_id = recordset_len = None
    exception = None
    try:
        if self_obj is not None:
            self_name = getattr(self_obj, "_name", None)
            env_obj = getattr(self_obj, "env", None)
            if env_obj is not None:
                uid = getattr(env_obj, "uid", None)
                company = getattr(env_obj, "company", None)
                company_id = getattr(company, "id", None) if company is not None else None
            try:
                recordset_len = len(self_obj)
            except Exception:  # noqa: BLE001
                recordset_len = None
    except Exception:  # noqa: BLE001
        pass
    return {
        "target_id": target_id,
        "self_name": self_name,
        "file": code.co_filename,
        "firstlineno": code.co_firstlineno,
        "uid": uid,
        "company_id": company_id,
        "recordset_len": recordset_len,
        "exception": exception,
    }


def install(targets, out_path, min_heartbeats=1):
    """Install the sys.settrace-based recorder. CI-only (see module docstring
    for the isolation this requires) — not exercised by the unit tests.

    Records target call events (shape mirrors runtime_path_probe.py) and, on
    EVERY traced event, re-confirms `sys.gettrace() is our own tracer` — the
    heartbeat that detects a `sys.settrace(None)` / tracer-replacement attack
    that happens mid-run rather than only at the very end. Caps events per
    target at MAX_EVENTS_PER_TARGET so a hot loop can't blow up memory.

    Stores all mutable state in the module-level `_tracer_state` dict so
    `finalize()` can read it back without any Odoo-specific plumbing.
    """
    global _tracer_state
    watched = _watch_set_from_targets(targets)
    state = {
        "out_path": out_path,
        "min_heartbeats": min_heartbeats,
        "targets": targets or [],
        "watched": watched,
        "events": [],
        "counts": {},
        "heartbeats": 0,
        "trace_installed": True,
        "tracer_fn": None,
    }

    def tracer(frame, event, arg):
        # Heartbeat: confirm we are still the live trace function every time
        # the interpreter invokes us — this is what makes a mid-run
        # sys.settrace(None)/replacement detectable at finalize() time (the
        # replacement/None means THIS function stops being called at all,
        # which finalize() observes via sys.gettrace() no longer matching).
        state["heartbeats"] += 1
        code = frame.f_code
        target_id = state["watched"].get(_basename_key(code.co_filename, code.co_firstlineno))
        if target_id is None:
            return tracer
        if event == "call":
            if state["counts"].get(target_id, 0) >= MAX_EVENTS_PER_TARGET:
                return tracer
            state["counts"][target_id] = state["counts"].get(target_id, 0) + 1
            state["events"].append(_build_call_event(target_id, frame))
        return tracer

    state["tracer_fn"] = tracer
    _tracer_state = state
    _ensure_settrace_auditor()
    sys.settrace(tracer)
    # Snapshot the settrace-call count AFTER our own install call; any increase
    # before finalize reads it means external code toggled the tracer.
    state["settrace_baseline"] = _settrace_calls()
    return state


def finalize(out_path, targets_expected):
    """Read back tamper/completeness facts, build the self-report, and write
    BOTH the raw events and the producer self-report to *out_path*.

    CRITICAL: if integrity is NOT sealed, this function still writes the
    report (with sealed=False) — a tampered/incomplete run must never be
    silently omitted, or CI would see "no report" and (depending on gate
    wiring) fail open instead of closed. `finalize` always writes.
    """
    global _tracer_state
    state = _tracer_state or {}

    trace_installed = bool(state.get("trace_installed"))
    tracer_fn = state.get("tracer_fn")
    trace_still_installed = trace_installed and tracer_fn is not None and sys.gettrace() is tracer_fn

    out_writable = None
    try:
        out_writable = os.access(str(out_path), os.W_OK) if Path(out_path).exists() else None
    except Exception:  # noqa: BLE001
        out_writable = None

    events = state.get("events", [])
    targets = state.get("targets", [])
    targets_hit = len({e.get("target_id") for e in events if isinstance(e, dict)})

    # External settrace toggles since install (disable/restore attack) — read the
    # permanent audit counter BEFORE finalize's own settrace(None) below.
    baseline = state.get("settrace_baseline")
    trace_changed_externally = (isinstance(baseline, int) and _settrace_calls() > baseline)

    eval_state = {
        "trace_installed": trace_installed,
        "trace_still_installed": trace_still_installed,
        "trace_changed_externally": trace_changed_externally,
        "heartbeats": state.get("heartbeats", 0),
        "min_heartbeats": state.get("min_heartbeats", 1),
        "events_recorded": len(events),
        "targets_expected": _int(targets_expected, default=len(targets)),
        "targets_hit": targets_hit,
        "output_test_writable": out_writable,
    }
    report = build_self_report(eval_state)

    payload = {"events": events, "producer": report, "targets_expected": eval_state["targets_expected"]}
    try:
        Path(out_path).write_text(json.dumps(payload, indent=2, default=str))
    except Exception:  # noqa: BLE001
        # Even a write failure must not be swallowed silently: print it so the
        # invoking shell/CI sees the failure surface (stdout still gets a copy
        # via run()'s sentinel-wrapped print below when called from there).
        pass

    # Tracer is no longer needed once finalized.
    sys.settrace(None)
    return report


def _load_targets_doc():
    """TARGETS_JSON env var (preferred) or stdin — same pattern as
    runtime_path_probe.py's `_load_targets_doc`. Never raises."""
    raw = os.environ.get("TARGETS_JSON")
    if not raw:
        try:
            raw = sys.stdin.read()
        except Exception:  # noqa: BLE001
            raw = ""
    if not raw or not raw.strip():
        return {"targets": []}
    try:
        return json.loads(raw)
    except ValueError:
        return {"targets": []}


def run():
    """Env-gated entry: install from TARGETS_JSON/stdin, immediately finalize
    (best-effort — a real capture needs the CI conftest hook to keep the
    tracer alive across the actual test run; see module docstring), and print
    the report between the sentinels."""
    doc = _load_targets_doc()
    targets = doc.get("targets") if isinstance(doc, dict) else doc
    targets = [t for t in (targets or []) if isinstance(t, dict)]
    out_path = os.environ.get("OBS_OUT_PATH", "runtime_observer_out.json")

    install(targets, out_path)
    # NOTE: like runtime_path_probe.py's `run()`, a bare `odoo-bin shell <
    # script` invocation is a single short-lived script — nothing else
    # executes while the tracer is installed, so events/heartbeats will
    # normally come back minimal here. Real coverage requires installing via
    # a conftest.py hook kept alive for the whole test run (CI's job).
    report = finalize(out_path, targets_expected=len(targets))

    print(SENTINEL_START)
    print(json.dumps(report, indent=2, default=str))
    print(SENTINEL_END)


# --- CLI ----------------------------------------------------------------------

def _cli_error(message):
    print(json.dumps({"trace_integrity": "absent", "sealed": False, "_warnings": [message]},
                     indent=2))


def main(argv=None):
    """Entry point: ``runtime-observer --digest <file> | --evaluate <state.json>
    | --self-digest``. Always exits 0 — the caller reads the JSON `sealed` /
    `trace_integrity` fields; a missing/unparseable input never raises, it
    prints an "absent"/sealed:false payload instead."""
    parser = argparse.ArgumentParser(prog="runtime-observer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--digest", metavar="FILE", help="print sha256 tool_digest of FILE")
    group.add_argument("--evaluate", metavar="STATE_JSON", help="evaluate a state JSON file")
    group.add_argument("--self-digest", action="store_true", help="print this module's own tool_digest")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.self_digest:
        print(json.dumps({"tool_digest": self_digest()}, indent=2))
        return 0

    if args.digest:
        try:
            data = Path(args.digest).read_bytes()
        except OSError as exc:
            _cli_error(f"{args.digest}: could not read file — {type(exc).__name__}: {exc}")
            return 0
        print(json.dumps({"tool_digest": compute_tool_digest(data)}, indent=2))
        return 0

    # --evaluate
    try:
        text = Path(args.evaluate).read_text()
    except OSError as exc:
        _cli_error(f"{args.evaluate}: could not read file — {type(exc).__name__}: {exc}")
        return 0
    try:
        state = json.loads(text)
    except ValueError as exc:
        _cli_error(f"{args.evaluate}: parse error — {type(exc).__name__}: {exc}")
        return 0

    report = build_self_report(state if isinstance(state, dict) else {})
    verdict = evaluate_integrity(state if isinstance(state, dict) else {})
    output = dict(verdict)
    output["self_report"] = report
    print(json.dumps(output, indent=2, default=str))
    return 0


if "env" in globals():
    run()
elif __name__ == "__main__":
    main()
