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

⚠️  This EXECUTES the method. Run on a dev/staging DB. By default the work is
wrapped in a SAVEPOINT and rolled back so nothing persists.

⚠️  COMMIT=1 now does a REAL commit: it RELEASEs the savepoint and calls
`env.cr.commit()`, so the side effects PERSIST. Use it only on a throwaway/dev
DB. Flows that commit explicitly or call external systems can cause effects even
under the default rollback — use a throwaway record regardless.

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

ADDON_MOD_RE = re.compile(r"^odoo\.addons\.([^.]+)")

record = env[MODEL].browse(RECORD_ID)   # noqa: F821
if not record.exists():
    raise SystemExit(f"{MODEL},{RECORD_ID} does not exist")

# --- SQL counter: wrap the cursor's execute for the duration -----------------
cr = env.cr                              # noqa: F821
_orig_execute = cr.execute
sql_counter = {"n": 0}


def _counting_execute(query, params=None, **kw):
    sql_counter["n"] += 1
    return _orig_execute(query, params, **kw)


# --- Tracer: keep depth, calls list, and SQL accounting in lockstep ----------
calls, depth, sql_at_call, frame_idx = [], {"n": 0}, {}, {}


def tracer(frame, event, arg):
    code = frame.f_code
    addon = ADDON_MOD_RE.match(frame.f_globals.get("__name__") or "")
    if not addon:                        # only business/addon code, skip core ORM
        return tracer
    if code.co_name.startswith("<"):     # <module>, <listcomp>, <lambda>, <genexpr> — not method calls
        return tracer
    if event == "call":
        if depth["n"] > MAX_DEPTH or len(calls) >= MAX_CALLS:
            return tracer            # not counted -> its return is ignored below
        self = frame.f_locals.get("self")
        model_name = getattr(self, "_name", None) if self is not None else None
        idx = len(calls)
        frame_idx[id(frame)] = idx
        sql_at_call[idx] = sql_counter["n"]
        calls.append({"depth": depth["n"], "addon": addon.group(1), "model": model_name,
                      "method": code.co_name, "line": code.co_firstlineno, "sql_count": None})
        depth["n"] += 1
    elif event == "return":
        idx = frame_idx.pop(id(frame), None)
        if idx is not None:
            calls[idx]["sql_count"] = sql_counter["n"] - sql_at_call[idx]
            depth["n"] = max(0, depth["n"] - 1)
    return tracer


error = None
cr.execute = _counting_execute
cr.execute("SAVEPOINT odoo_trace")
sys.settrace(tracer)
try:
    getattr(record, METHOD)()
except Exception as e:
    error = f"{type(e).__name__}: {e}"
finally:
    sys.settrace(None)
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

result = {
    "root": f"{MODEL}({RECORD_ID}).{METHOD}",
    "committed": COMMIT,
    "error": error,
    "total_addon_calls": len(calls),
    "total_sql": sql_counter["n"],
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
