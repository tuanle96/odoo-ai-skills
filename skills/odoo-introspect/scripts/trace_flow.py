"""
Odoo runtime flow tracer (Layer D) — run INSIDE `odoo-bin shell`.

MRO tells you the *potential* super() chain of ONE method. A real business flow
(confirm sale -> procurement -> stock move -> picking -> invoice hooks ->
automation -> computes) is a GRAPH across many models. This actually executes
the method on a real record and records the call sequence through addon code,
so you see what really runs and in what order.

It traces with sys.settrace filtered to ADDON frames — detected by MODULE NAME
(`odoo.addons.<module>...` via frame.f_globals['__name__']), NOT by the source
file path. A path filter like "/addons/" misses custom/enterprise addons mounted
at e.g. /mnt/extra-addons/<repo>/<module>/ (no "/addons/" segment), while the
module name is "odoo.addons.<module>" for every addon wherever it lives on disk;
core ORM plumbing (odoo.models / odoo.api / odoo.fields) is naturally excluded.
It attaches the recordset model to each frame, and counts SQL per call
(cumulative, including children).

Beyond the raw `calls`, a `summary` block surfaces the things you actually scan
for: the SQL hotspots by SELF cost (cumulative minus children, so a parent
doesn't mask its callee), the most-invoked (model, method) pairs (N+1 loops),
writes/creates aggregated by model + the field NAMES touched (names only, never
values — no leak), and, when the flow raised, the innermost addon frame the
exception passed through.

⚠️  This EXECUTES the method. Run on a dev/staging DB. By default the work is
wrapped in a SAVEPOINT and rolled back so no DB changes persist — but a DB
rollback does NOT undo emails, webhooks, HTTP/API calls, queued jobs, or files
the flow already sent/wrote (see the COMMIT note below). "Instance untouched"
means the DB, not the outside world.

⚠️  COMMIT=1 now does a REAL commit: it RELEASEs the savepoint and calls
`env.cr.commit()`, so the side effects PERSIST. Use it only on a throwaway/dev
DB. Flows that commit explicitly or call external systems can cause effects even
under the default rollback — use a throwaway record regardless.

The env-dependent work lives in run(); the pure helpers (summarize_calls,
compute_self_sql, aggregate_writes) are module-level so they import without
Odoo. run() executes only when `env` is present (inside odoo-bin shell).

Usage
-----
    MODEL=sale.order RECORD_ID=123 METHOD=action_confirm \
        odoo-bin shell -d <DB> --no-http < trace_flow.py

    # actually persist instead of rolling back (throwaway/dev DB only):
    MODEL=sale.order RECORD_ID=123 METHOD=action_confirm COMMIT=1 odoo-bin shell -d <DB> < trace_flow.py

    MAX_DEPTH=12 OUT=/tmp/trace.json ...   # optional knobs

Output: pure JSON wrapped in ===ODOO_TRACE_START=== / ===ODOO_TRACE_END===.
"""
import os
import re
import sys
import json
from collections import Counter

ADDON_MOD_RE = re.compile(r"^odoo\.addons\.([^.]+)")


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def compute_self_sql(calls):
    """Self SQL per frame = cumulative − sum(direct children cumulative).

    `calls` is the preorder, depth-annotated trace each carrying a cumulative
    `sql_count` (includes children). Returns a list parallel to `calls`. A
    single O(n) pass over a depth-ordered preorder list: a stack holds open
    ancestors; each frame subtracts its cumulative from its immediate parent.
    """
    self_sql = [(c.get("sql_count") or 0) for c in calls]
    stack = []   # indices of currently-open ancestor frames
    for i, c in enumerate(calls):
        d = c.get("depth", 0)
        while stack and calls[stack[-1]].get("depth", 0) >= d:
            stack.pop()
        if stack:
            self_sql[stack[-1]] -= (c.get("sql_count") or 0)
        stack.append(i)
    return self_sql


def summarize_calls(calls, top_n=10):
    """Compact, scan-first summary of a raw trace.

    - call_counts: most-invoked (model, method, addon) — N+1 / loop smell.
    - top_self_sql: frames doing the most SQL THEMSELVES (cumulative − children),
      so a thin parent doesn't hide the expensive callee. Each carries
      cumulative too, for context.
    - max_depth: deepest addon frame reached.
    """
    if not calls:
        return {"call_counts": [], "top_self_sql": [], "max_depth": 0}
    counts = Counter((c.get("model"), c.get("method"), c.get("addon")) for c in calls)
    call_counts = [{"model": m, "method": me, "addon": a, "count": n}
                   for (m, me, a), n in counts.most_common(top_n)]
    self_sql = compute_self_sql(calls)
    ranked = sorted(
        ({"model": c.get("model"), "method": c.get("method"), "addon": c.get("addon"),
          "line": c.get("line"), "self_sql": s, "cumulative_sql": c.get("sql_count") or 0}
         for c, s in zip(calls, self_sql)),
        key=lambda x: x["self_sql"], reverse=True)
    top_self_sql = [r for r in ranked if r["self_sql"] > 0][:top_n]
    max_depth = max((c.get("depth", 0) for c in calls), default=0)
    return {"call_counts": call_counts, "top_self_sql": top_self_sql, "max_depth": max_depth}


def aggregate_writes(write_events):
    """Group create/write events by model with the union of field NAMES touched.

    `write_events` is a list of {model, method ('create'|'write'), fields:[...]}.
    Returns {model: {creates, writes, fields:[sorted names]}}. Field names only —
    callers must never put values in here.
    """
    out = {}
    for e in write_events or []:
        model = e.get("model") or "<unknown>"
        rec = out.setdefault(model, {"creates": 0, "writes": 0, "_fields": set()})
        if e.get("method") == "create":
            rec["creates"] += 1
        else:
            rec["writes"] += 1
        rec["_fields"].update(e.get("fields") or [])
    return {m: {"creates": v["creates"], "writes": v["writes"],
                "fields": sorted(v["_fields"])} for m, v in sorted(out.items())}


def _vals_field_names(vals):
    """Field NAMES from a create/write `vals` (dict or list of dicts). Names
    only — never values. Returns a sorted list."""
    names = set()
    if isinstance(vals, dict):
        names.update(vals.keys())
    elif isinstance(vals, (list, tuple)):
        for v in vals:
            if isinstance(v, dict):
                names.update(v.keys())
    return sorted(str(k) for k in names)


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    RECORD_ID = os.environ.get("RECORD_ID")
    METHOD = os.environ.get("METHOD")
    if not (MODEL and RECORD_ID and METHOD):
        raise SystemExit("Set MODEL, RECORD_ID and METHOD")

    RECORD_ID = int(RECORD_ID)
    MAX_DEPTH = int(os.environ.get("MAX_DEPTH", "20"))
    MAX_CALLS = int(os.environ.get("MAX_CALLS", "4000"))
    COMMIT = os.environ.get("COMMIT") in ("1", "true", "yes")
    OUT = os.environ.get("OUT")

    record = env[MODEL].browse(RECORD_ID)   # noqa: F821
    if not record.exists():
        raise SystemExit(f"{MODEL},{RECORD_ID} does not exist")

    # --- SQL counter: wrap the cursor's execute for the duration -------------
    cr = env.cr                              # noqa: F821
    _orig_execute = cr.execute
    sql_counter = {"n": 0}

    def _counting_execute(query, params=None, **kw):
        sql_counter["n"] += 1
        return _orig_execute(query, params, **kw)

    # --- Tracer: keep depth, calls list, and SQL accounting in lockstep ------
    calls, depth, sql_at_call, frame_idx = [], {"n": 0}, {}, {}
    write_events = []
    exc_origin = {}

    def _model_of(frame):
        self = frame.f_locals.get("self")
        return getattr(self, "_name", None) if self is not None else None

    def tracer(frame, event, arg):
        code = frame.f_code
        addon = ADDON_MOD_RE.match(frame.f_globals.get("__name__") or "")
        if not addon:                        # only business/addon code, skip core ORM
            return tracer
        if code.co_name.startswith("<"):     # <module>, <listcomp>, <lambda>, <genexpr>
            return tracer
        if event == "call":
            if depth["n"] > MAX_DEPTH or len(calls) >= MAX_CALLS:
                return tracer            # not counted -> its return is ignored below
            model_name = _model_of(frame)
            idx = len(calls)
            frame_idx[id(frame)] = idx
            sql_at_call[idx] = sql_counter["n"]
            calls.append({"depth": depth["n"], "addon": addon.group(1), "model": model_name,
                          "method": code.co_name, "line": code.co_firstlineno, "sql_count": None})
            # Capture create/write field NAMES (never values) for the write map.
            if code.co_name in ("create", "write"):
                # Modern create is `@api.model_create_multi def create(self,
                # vals_list)`, so the local is `vals_list`, not `vals`; fall back
                # to it so multi-create field names aren't silently dropped.
                f_locals = frame.f_locals
                raw_vals = (f_locals["vals"] if "vals" in f_locals
                            else f_locals.get("vals_list"))
                write_events.append({"model": model_name, "method": code.co_name,
                                     "fields": _vals_field_names(raw_vals)})
            depth["n"] += 1
        elif event == "return":
            idx = frame_idx.pop(id(frame), None)
            if idx is not None:
                calls[idx]["sql_count"] = sql_counter["n"] - sql_at_call[idx]
                depth["n"] = max(0, depth["n"] - 1)
        elif event == "exception" and not exc_origin:
            # First exception event we see is the innermost ADDON frame the
            # exception passes through (core ORM frames are filtered out above).
            exc_type = arg[0] if isinstance(arg, tuple) and arg else None
            exc_origin.update({
                "model": _model_of(frame), "method": code.co_name,
                "addon": addon.group(1), "line": frame.f_lineno,
                "exc_type": getattr(exc_type, "__name__", str(exc_type)),
            })
        return tracer

    error = None
    warnings = []
    # Best-effort SQL counting: wrapping the cursor's execute can fail if the
    # cursor wrapper changed or the attribute can't be assigned in this
    # environment. If so, we disable counting and keep tracing the call graph
    # rather than failing the whole tool. sql_count fields then report 0 and we
    # flag it in warnings.
    sql_count_enabled = False
    try:
        cr.execute = _counting_execute
        sql_count_enabled = True
    except Exception as exc:
        warnings.append({"code": "sql_counter_disabled",
                         "message": f"{type(exc).__name__}: {exc}"})

    cr.execute("SAVEPOINT odoo_trace")
    sys.settrace(tracer)
    try:
        getattr(record, METHOD)()
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    finally:
        sys.settrace(None)
        if sql_count_enabled:
            cr.execute = _orig_execute
        if COMMIT:
            cr.execute("RELEASE SAVEPOINT odoo_trace")
            env.cr.commit()             # real persist — throwaway/dev DB only  # noqa: F821
        else:
            cr.execute("ROLLBACK TO SAVEPOINT odoo_trace")

    # fill any still-open sql_count (frames unwound by an exception get no return)
    for c in calls:
        if c["sql_count"] is None:
            c["sql_count"] = 0

    # compact summary: distinct (model, method, addon) in first-seen order
    seen, order = set(), []
    for c in calls:
        key = (c["model"], c["method"], c["addon"])
        if key not in seen:
            seen.add(key)
            order.append({"model": c["model"], "method": c["method"], "addon": c["addon"]})

    summary = summarize_calls(calls)
    summary["writes_by_model"] = aggregate_writes(write_events)
    summary["_writes_caveat"] = (
        "writes_by_model counts create/write field NAMES observed in traced "
        "ADDON frames (odoo.addons.*) only. A record.write(vals) on a model that "
        "does NOT override write in an addon runs in core odoo.models and is not "
        "captured here — so treat this as 'writes seen in addon code', not 'every "
        "ORM write the flow performed'. Confirm side effects on a model with "
        "Layer A / a targeted Layer F breakpoint when completeness matters.")
    summary["exception_origin"] = exc_origin or None

    result = {
        "root": f"{MODEL}({RECORD_ID}).{METHOD}",
        "committed": COMMIT,
        "error": error,
        "sql_count_enabled": sql_count_enabled,
        "warnings": warnings,
        "total_addon_calls": len(calls),
        "total_sql": sql_counter["n"] if sql_count_enabled else None,
        "summary": summary,
        "distinct_steps": order,
        "calls": calls,
    }

    payload = json.dumps(result, indent=2, default=str)
    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}  ({len(calls)} calls, {sql_counter['n']} SQL)")
    else:
        print("===ODOO_TRACE_START===")
        print(payload)
        print("===ODOO_TRACE_END===")


# `env` is injected by `odoo-bin shell`; absent (e.g. a unit-test import) → run()
# is skipped and only the pure helpers above are exposed.
if "env" in globals():
    run()
