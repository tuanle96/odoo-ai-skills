"""
Runtime path probe (Layer — "did the test really call the changed code?") —
PURE DECISION layer is import-safe anywhere; the RECORDER runs INSIDE
`odoo-bin shell` (or, in a real CI wiring, a conftest.py hook kept alive for
the whole test run).

A test can pass while never touching the changed method: it might exercise a
mock, a stub, a helper that shadows the real method, or the wrong class in the
MRO (a `sale.order` override shadowed by another module's `sale.order`
override that runs first). Coverage tools only prove a LINE executed, not that
it executed as a real bound method call on the model's own recordset. This
script closes that gap for the targets `diff_targets.py` emits:

  1. PART A (pure, unit-tested) — `evaluate_target` / `build_report` turn
     recorded call events + a live-registry fact per target into a per-target
     "bound" (proven) / "unbound" (not proven) verdict with reasons.
  2. PART B (env-gated, documented not unit-tested) — a `sys.settrace`-based
     recorder that, run inside `odoo-bin shell`, introspects the LIVE registry
     MRO for each target (does the changed method actually still resolve to
     this class/file?) and records whatever call events pass through the
     watched code objects while the tracer is installed.

⚠️  Process boundary: `odoo-bin shell < runtime_path_probe.py` is a single
short-lived script — nothing else executes while `sys.settrace` is installed,
so `events` will typically come back EMPTY for that invocation. That is
expected, not a bug: real call-event capture requires installing the same
tracer from a conftest.py / pytest hook that stays active while the actual
TransactionCase runs, then feeding the emitted events (plus the registry_facts
this script produces) into `main()`'s LOCAL mode below. CI is responsible for
wiring the conftest hook and assembling the two JSON blobs; this module only
guarantees the DECISION math is correct given whatever events show up.

Usage
-----
    # Inside odoo-bin shell: introspect the live registry + emit whatever
    # events pass through the tracer during THIS script's own execution.
    TARGETS_JSON="$(cat diff_targets.json)" \\
        odoo-bin shell -d <DB> --no-http < runtime_path_probe.py

    # Local (no Odoo) — CI assembly mode: combine a targets file, an events
    # file (collected by a conftest hook during the real test run) and the
    # registry_facts file (produced by the shell run above) into one verdict:
    python3 runtime_path_probe.py --targets diff_targets.json \\
        --events events.json --registry registry_facts.json

    # Or via the CLI wrapper:
    odoo-ai runtime-path --targets diff_targets.json --events events.json \\
        --registry registry_facts.json

Output (shell mode): pure JSON wrapped in
===ODOO_RTPATH_START=== / ===ODOO_RTPATH_END===.
Output (local mode): pure JSON to stdout, no sentinels.
"""
import argparse
import json
import os
import sys
from pathlib import Path

WARNINGS = []

SENTINEL_START = "===ODOO_RTPATH_START==="
SENTINEL_END = "===ODOO_RTPATH_END==="
MAX_EVENTS_PER_TARGET = 20

_REASON_NOT_IN_MRO = "target not in live registry MRO"
_REASON_NOT_CALLED = "no test entered the target method"
_REASON_MOCK = "target never called through a recordset of its own model (mock/stub?)"
_REASON_WRONG_LOCATION = "call did not resolve to the changed source location"


# --- Part A: pure decision layer (no Odoo needed — unit-testable) ------------

def _loc_matches(file, firstlineno, target):
    """Lenient location match: same file BASENAME and firstlineno falls inside
    the target's method_span range (inclusive). Any missing/malformed input
    (None file, non-int line, target without a span) is simply "no match" —
    never raises."""
    if file is None or firstlineno is None:
        return False
    tfile = target.get("file")
    span = target.get("method_span")
    if not tfile or not isinstance(span, (list, tuple)) or not span:
        return False
    lo = span[0]
    hi = span[1] if len(span) > 1 and span[1] is not None else lo
    try:
        lo = int(lo)
        hi = int(hi)
        line = int(firstlineno)
    except (TypeError, ValueError):
        return False
    if os.path.basename(str(file)) != os.path.basename(str(tfile)):
        return False
    return lo <= line <= hi


def _dedupe_lists(lists):
    """De-dupe a list of lists (e.g. allowed_company_ids snapshots) preserving
    first-seen order, without requiring the elements to be hashable individually
    (only the whole list, via tuple(), needs to be)."""
    seen = set()
    out = []
    for lst in lists:
        try:
            key = tuple(lst)
        except TypeError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(list(lst))
    return out


def _observed_from_events(events):
    """Aggregate the runtime facts observed across a list of call events.

    Returns {"uids","max_recordset_len","companies","allowed_company_sets",
    "exceptions"}. Never raises on malformed event dicts — bad fields are
    simply skipped.
    """
    uids, companies, allowed_sets, exceptions = [], [], [], []
    max_len = 0
    for e in events:
        if not isinstance(e, dict):
            continue
        uid = e.get("uid")
        if isinstance(uid, int) and not isinstance(uid, bool):
            uids.append(uid)
        company = e.get("company_id")
        if isinstance(company, int) and not isinstance(company, bool):
            companies.append(company)
        allowed = e.get("allowed_company_ids")
        if isinstance(allowed, list):
            allowed_sets.append(list(allowed))
        rec_len = e.get("recordset_len")
        if isinstance(rec_len, int) and not isinstance(rec_len, bool) and rec_len > max_len:
            max_len = rec_len
        exc = e.get("exception")
        if isinstance(exc, str) and exc:
            exceptions.append(exc)
    return {
        "uids": sorted(set(uids)),
        "max_recordset_len": max_len,
        "companies": sorted(set(companies)),
        "allowed_company_sets": _dedupe_lists(allowed_sets),
        "exceptions": list(dict.fromkeys(exceptions)),
    }


def evaluate_target(target, events, registry_fact):
    """Decide whether `target` was genuinely exercised (bound) by the test run.

    `events` must already be filtered to this target's id. `registry_fact` is
    the live-registry introspection result for this target (or None/missing).

    Returns {"target_id","in_mro","called","self_name_match",
    "covered_changed_lines","bound","reasons","observed"}. Never raises —
    missing/None/malformed inputs simply fail the relevant check.
    """
    target = target if isinstance(target, dict) else {}
    events = [e for e in (events or []) if isinstance(e, dict)]
    registry_fact = registry_fact if isinstance(registry_fact, dict) else None

    reasons = []

    in_mro = bool(registry_fact and registry_fact.get("in_mro") is True)
    if not in_mro:
        reasons.append(_REASON_NOT_IN_MRO)

    called = bool(events)
    if not called:
        reasons.append(_REASON_NOT_CALLED)

    model = target.get("model")
    if model:
        self_name_match = any(e.get("self_name") == model for e in events)
    else:
        self_name_match = True  # nothing to compare against — check not applicable
    if not self_name_match:
        reasons.append(_REASON_MOCK)

    covered = False
    if events:
        covered = any(_loc_matches(e.get("file"), e.get("firstlineno"), target) for e in events)
        if not covered and registry_fact:
            covered = _loc_matches(registry_fact.get("mro_file"),
                                    registry_fact.get("mro_firstlineno"), target)
    if not covered:
        reasons.append(_REASON_WRONG_LOCATION)

    bound = in_mro and called and self_name_match and covered

    return {
        "target_id": target.get("id"),
        "in_mro": in_mro,
        "called": called,
        "self_name_match": self_name_match,
        "covered_changed_lines": covered,
        "bound": bound,
        "reasons": reasons,
        "observed": _observed_from_events(events),
    }


def _merge_observations(events):
    """Merge call-event facts into the shape scenario_satisfaction.py consumes
    (its `observations.json` input): uids_seen, max_recordset_len,
    max_create_vals_len, companies_seen, allowed_company_sets,
    raised_exceptions. `max_create_vals_len` is always 0 — this recorder
    observes recordset length, not create() vals, so it never claims batch
    coverage it didn't see."""
    obs = _observed_from_events(events)
    return {
        "uids_seen": obs["uids"],
        "max_recordset_len": obs["max_recordset_len"],
        "max_create_vals_len": 0,
        "companies_seen": obs["companies"],
        "allowed_company_sets": obs["allowed_company_sets"],
        "raised_exceptions": obs["exceptions"],
    }


def build_report(targets, events, registry_facts):
    """Evaluate every target and assemble the full runtime-path report.

    Returns {"summary":{"targets","bound","unbound"}, "targets":[...],
    "unbound_targets":[ids], "observations":{...merged, see _merge_observations},
    "_warnings":[...]}. Never raises on missing/None/malformed inputs.
    """
    targets = [t for t in (targets or []) if isinstance(t, dict)]
    events = [e for e in (events or []) if isinstance(e, dict)]
    registry_facts = [r for r in (registry_facts or []) if isinstance(r, dict)]

    warnings = []
    registry_by_id = {}
    for rf in registry_facts:
        tid = rf.get("target_id")
        if tid in registry_by_id:
            warnings.append(f"{tid}: duplicate registry_fact — using the last one")
        registry_by_id[tid] = rf

    events_by_id = {}
    for e in events:
        events_by_id.setdefault(e.get("target_id"), []).append(e)

    per_target = []
    bound_count = 0
    unbound_ids = []
    for t in targets:
        tid = t.get("id")
        rf = registry_by_id.get(tid)
        if rf is None:
            warnings.append(f"{tid}: no registry_fact found — assuming not in MRO")
        result = evaluate_target(t, events_by_id.get(tid, []), rf)
        per_target.append(result)
        if result["bound"]:
            bound_count += 1
        else:
            unbound_ids.append(tid)

    return {
        "summary": {
            "targets": len(targets),
            "bound": bound_count,
            "unbound": len(targets) - bound_count,
        },
        "targets": per_target,
        "unbound_targets": unbound_ids,
        "observations": _merge_observations(events),
        "_warnings": warnings,
    }


# --- Part B: recorder (env-gated — runs inside odoo-bin shell) ---------------

def _load_targets_doc():
    """TARGETS_JSON env var (preferred — the diff_targets.py output, injected
    as content) or, failing that, stdin. Never raises; a missing/unparseable
    input warns and yields an empty target list."""
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
    except ValueError as exc:
        WARNINGS.append(f"TARGETS_JSON: parse error — {type(exc).__name__}: {exc}")
        return {"targets": []}


def _introspect_registry_facts(targets, live_env):
    """For each target, walk `live_env[model].__class__.mro()` looking for the
    class whose `__dict__[method]` source file matches the target's file — the
    live proof that the changed method still resolves through this model's
    MRO. Every introspection step is guarded: a lookup failure (model removed,
    method renamed, uninstalled module, ...) becomes a warning, never a crash.
    """
    import inspect
    facts = []
    for t in targets:
        tid = t.get("id")
        model = t.get("model")
        method = t.get("method")
        fact = {"target_id": tid, "in_mro": False, "mro_file": None, "mro_firstlineno": None}
        if model and method:
            try:
                cls = live_env[model].__class__
                for klass in cls.mro():
                    func = klass.__dict__.get(method)
                    if func is None:
                        continue
                    real = getattr(func, "__func__", func)
                    try:
                        src_file = inspect.getsourcefile(real)
                        _, lineno = inspect.getsourcelines(real)
                    except (TypeError, OSError):
                        continue
                    if src_file and t.get("file") and \
                            os.path.basename(src_file) == os.path.basename(t["file"]):
                        fact["in_mro"] = True
                        fact["mro_file"] = src_file
                        fact["mro_firstlineno"] = lineno
                        break
            except Exception as exc:  # noqa: BLE001
                WARNINGS.append(f"{tid}: registry introspection failed ({type(exc).__name__}: {exc})")
        facts.append(fact)
    return facts


def _build_call_event(target_id, frame):
    """Build one call event from a traced frame. `self` (if present in
    f_locals) supplies the model identity/uid/company context; every field is
    best-effort and guarded — a missing attribute just leaves that field None
    rather than aborting the whole event."""
    code = frame.f_code
    self_obj = frame.f_locals.get("self")
    self_name = uid = is_superuser = company_id = allowed_company_ids = recordset_len = None
    try:
        if self_obj is not None:
            self_name = getattr(self_obj, "_name", None)
            env_obj = getattr(self_obj, "env", None)
            if env_obj is not None:
                uid = getattr(env_obj, "uid", None)
                is_superuser = bool(getattr(env_obj, "su", False)) or uid == 1
                company = getattr(env_obj, "company", None)
                company_id = getattr(company, "id", None) if company is not None else None
                companies = getattr(env_obj, "companies", None)
                if companies is not None:
                    allowed_company_ids = list(getattr(companies, "ids", []) or [])
            try:
                recordset_len = len(self_obj)
            except Exception:  # noqa: BLE001
                recordset_len = None
    except Exception as exc:  # noqa: BLE001
        WARNINGS.append(f"{target_id}: event introspection failed ({type(exc).__name__}: {exc})")

    return {
        "target_id": target_id,
        "test": os.environ.get("PYTEST_CURRENT_TEST"),
        "model": self_name,
        "method": code.co_name,
        "file": code.co_filename,
        "firstlineno": code.co_firstlineno,
        "uid": uid,
        "is_superuser": is_superuser,
        "company_id": company_id,
        "allowed_company_ids": allowed_company_ids,
        "recordset_len": recordset_len,
        "exception": None,
        "self_name": self_name,
        "in_registry_mro": True,
    }


def run():
    doc = _load_targets_doc()
    targets = doc.get("targets") if isinstance(doc, dict) else doc
    targets = [t for t in (targets or []) if isinstance(t, dict)]

    registry_facts = _introspect_registry_facts(targets, env)  # noqa: F821

    # Watch set keyed by (file basename, firstlineno): prefer the LIVE registry
    # location (the actual resolved class/file — may differ from the diff's
    # declared file for an inherited override), falling back to the target's
    # own declared file/method_span start when the registry lookup failed.
    watched = {}
    for t, rf in zip(targets, registry_facts):
        key = None
        if rf.get("in_mro") and rf.get("mro_file") and rf.get("mro_firstlineno") is not None:
            key = (os.path.basename(rf["mro_file"]), rf["mro_firstlineno"])
        elif t.get("file") and isinstance(t.get("method_span"), (list, tuple)) and t["method_span"]:
            key = (os.path.basename(t["file"]), t["method_span"][0])
        if key is not None:
            watched[key] = t.get("id")

    events = []
    frame_event_idx = {}
    counts = {}

    def tracer(frame, event, arg):
        code = frame.f_code
        target_id = watched.get((os.path.basename(code.co_filename), code.co_firstlineno))
        if target_id is None:
            return tracer
        if event == "call":
            if counts.get(target_id, 0) >= MAX_EVENTS_PER_TARGET:
                return tracer
            counts[target_id] = counts.get(target_id, 0) + 1
            frame_event_idx[id(frame)] = len(events)
            events.append(_build_call_event(target_id, frame))
        elif event == "exception":
            idx = frame_event_idx.get(id(frame))
            if idx is not None and events[idx].get("exception") is None:
                exc_type = arg[0] if isinstance(arg, tuple) and arg else None
                events[idx]["exception"] = getattr(exc_type, "__name__", None)
        return tracer

    # NOTE: `odoo-bin shell < runtime_path_probe.py` is a single short-lived
    # script — nothing else runs while the tracer is installed here, so
    # `events` will normally come back empty for THIS invocation. That is
    # expected (see module docstring): real capture requires installing this
    # same `tracer` from a conftest.py hook kept alive for the whole test run.
    sys.settrace(tracer)
    sys.settrace(None)

    report = build_report(targets, events, registry_facts)
    output = {
        "targets": targets,
        "registry_facts": registry_facts,
        "events": events,
        "report": report,
        "_warnings": WARNINGS,
    }
    payload = json.dumps(output, indent=2, default=str)
    print(SENTINEL_START)
    print(payload)
    print(SENTINEL_END)


# --- Local (no Odoo) CI-assembly mode -----------------------------------------

def _load_json_file(path, warnings):
    try:
        text = Path(path).read_text()
    except OSError as exc:
        warnings.append(f"{path}: could not read file — {type(exc).__name__}: {exc}")
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        warnings.append(f"{path}: parse error — {type(exc).__name__}: {exc}")
        return None


def main(argv=None):
    """Entry point: ``runtime-path --targets <f> --events <f> --registry <f>``.

    Loads the three CI-produced JSON artifacts and prints `build_report`'s
    verdict as pure JSON (no sentinels — this is the local/CI path, not the
    odoo-bin shell path). Missing/unparseable files never raise — they warn
    and fall through to `build_report`'s safe empty-input defaults.
    """
    parser = argparse.ArgumentParser(prog="runtime-path")
    parser.add_argument("--targets", required=True, help="diff_targets.py output JSON")
    parser.add_argument("--events", required=True, help="call events JSON (from the conftest recorder hook)")
    parser.add_argument("--registry", required=True, help="registry_facts JSON (from the odoo-bin shell run)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    warnings = []
    targets_doc = _load_json_file(args.targets, warnings)
    events_doc = _load_json_file(args.events, warnings)
    registry_doc = _load_json_file(args.registry, warnings)

    targets = targets_doc.get("targets") if isinstance(targets_doc, dict) else targets_doc
    events = events_doc.get("events") if isinstance(events_doc, dict) else events_doc
    registry_facts = registry_doc.get("registry_facts") if isinstance(registry_doc, dict) else registry_doc

    report = build_report(targets, events, registry_facts)
    report["_warnings"] = warnings + report.get("_warnings", [])
    print(json.dumps(report, indent=2, default=str))
    return 0


if "env" in globals():
    run()
elif __name__ == "__main__":
    main()

