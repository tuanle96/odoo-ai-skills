"""
Odoo Execution Surface Graph sampler (Layer K — ESG) — run INSIDE `odoo-bin shell`.

The honest answer to "make the agent understand the overall process." NOT a
static process map (Odoo's real flow is a runtime-trace distribution — conditional
on group/company/automations/Studio — so a stored map goes stale and makes the
agent confidently wrong). Instead, process understanding **emerges from sampled
traces**:

  1. discover the top entrypoint roots (entrypoint_surface ranking),
  2. for each, find a real record and TRACE it (light, rollback by default),
  3. keep only the SKELETON — which models it touched, the cross-model call edges,
     the cross-APP edges, and the write-map,
  4. merge the skeletons into one graph the agent can read before diving micro.

This is the bridge `surface.top_trace_seeds` points at. It reuses trace_flow's
pure write helpers and a lighter tracer (no SQL self-cost — that's trace_flow's
job for a single deep flow; ESG is breadth-first orientation).

⚠️  Like trace_flow this EXECUTES methods on real records. Default is a SAVEPOINT
rolled back, but a rollback does NOT undo emails / webhooks / external calls a
flow already made. Run on a dev/staging DB. Keep the seed count small.

Config (env):
    MODEL / MODULE  (opt)  scope discovery; omit both for instance-wide
    ESG_SEEDS       (opt)  max object-button flows to sample   (default 6)
    COMMIT          (opt)  "1" persists the traces (dev DB only; default rollback)
    OUT             (opt)  write JSON to this path instead of stdout sentinels

Pure helpers (merge_skeletons, summarize_esg) are module-level / unit-testable;
run() executes only inside odoo-bin shell.

Output: pure JSON wrapped in ===ODOO_ESG_START=== / ===ODOO_ESG_END===.
"""
import os
import re
import sys
import json
from collections import Counter

# ESG auto-fires the methods it discovers (unlike trace_flow, where YOU name the
# one method). A DB rollback can't undo an email / webhook / payment / SMS / print
# a method already sent, so we never auto-trace a method whose NAME signals an
# external or destructive effect. Set ESG_ALLOW_UNSAFE=1 to override on a truly
# throwaway DB. (Name-based, so it can't catch an effect hidden behind an innocent
# name — the _caveat still says: throwaway record on a dev/staging DB.)
UNSAFE_METHOD_RE = re.compile(
    r"(send|mail|email|sms|notify|message_post|webhook|payment|_pay\b|"
    r"capture|refund|print|export|import_|unlink|delete|purge|action_cancel)",
    re.IGNORECASE)


def is_unsafe_to_autotrace(method):
    """True if a method name signals an irreversible external/destructive effect
    that a DB rollback won't undo — ESG must not auto-fire it. Pure/testable."""
    return bool(UNSAFE_METHOD_RE.search(method or ""))

# Import sibling pure helpers (trace_flow, entrypoint_surface). When piped to
# `odoo-bin shell` there is NO __file__, so the CLI exports SCRIPTS_DIR; only
# trust an ABSOLUTE SCRIPTS_DIR that actually holds the siblings (never a
# cwd-relative dir that could shadow them). Unit tests add the dir to sys.path
# themselves, so the import still resolves there.
_SD = os.environ.get("SCRIPTS_DIR")
if _SD and os.path.isabs(_SD) and os.path.isfile(os.path.join(_SD, "trace_flow.py")):
    if _SD not in sys.path:
        sys.path.insert(0, _SD)
elif _SD:
    sys.stderr.write(f"esg_sample: ignoring untrusted SCRIPTS_DIR={_SD!r} "
                     "(must be an absolute path with a sibling trace_flow.py)\n")
try:
    from trace_flow import ADDON_MOD_RE, aggregate_writes, _vals_field_names
    from entrypoint_surface import (is_action_method, is_technical_model,
                                     module_centrality, rank_entrypoints)
except Exception:  # noqa: BLE001 — keep import-safe for unit tests that stub these
    ADDON_MOD_RE = None

WARNINGS = []


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def merge_skeletons(seed_results):
    """Merge per-seed trace skeletons into one Execution Surface Graph.

    Each seed result is a dict with (when traced): touched_models [..],
    model_edges [[from,to],..], app_edges [[from,to],..], writes {model:{...}}.
    Returns {models:[{model,touched_by}], edges:[{from,to,weight}],
    app_edges:[{from,to,weight}], writes:{model:{creates,writes,fields}}}.
    Deterministic ordering so output is stable and diffable.
    """
    touched = {}                 # model -> set(seed labels)
    edge_w = Counter()           # (from_model, to_model) -> weight
    app_w = Counter()            # (from_addon, to_addon) -> weight
    write_events = []
    for r in seed_results or []:
        if not r or not r.get("traced"):
            continue
        label = r.get("label") or r.get("method") or "?"
        for m in r.get("touched_models") or []:
            touched.setdefault(m, set()).add(label)
        for a, b in r.get("model_edges") or []:
            if a and b:
                edge_w[(a, b)] += 1
        for a, b in r.get("app_edges") or []:
            if a and b:
                app_w[(a, b)] += 1
        for model, w in (r.get("writes") or {}).items():
            # re-expand to write events so aggregate_writes can union field names
            for _ in range(w.get("creates", 0)):
                write_events.append({"model": model, "method": "create",
                                     "fields": w.get("fields") or []})
            for _ in range(w.get("writes", 0)):
                write_events.append({"model": model, "method": "write",
                                     "fields": w.get("fields") or []})
    models = [{"model": m, "touched_by": sorted(s)} for m, s in sorted(touched.items())]
    edges = [{"from": a, "to": b, "weight": w}
             for (a, b), w in sorted(edge_w.items(), key=lambda kv: (-kv[1], kv[0]))]
    app_edges = [{"from": a, "to": b, "weight": w}
                 for (a, b), w in sorted(app_w.items(), key=lambda kv: (-kv[1], kv[0]))]
    writes = aggregate_writes(write_events) if ADDON_MOD_RE else {}
    return {"models": models, "edges": edges, "app_edges": app_edges, "writes": writes}


def summarize_esg(graph, seed_results):
    """Top-line counts for the ESG summary line."""
    traced = [r for r in (seed_results or []) if r and r.get("traced")]
    failed = [r for r in (seed_results or []) if r and not r.get("traced")]
    return {
        "seeds_considered": len(seed_results or []),
        "seeds_traced": len(traced),
        "seeds_skipped": len(failed),
        "models_touched": len(graph.get("models", [])),
        "model_edges": len(graph.get("edges", [])),
        "cross_app_edges": len(graph.get("app_edges", [])),
        "writes_models": len(graph.get("writes", {})),
    }


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    MODULE = os.environ.get("MODULE")
    MAX_SEEDS = int(os.environ.get("ESG_SEEDS", "6") or "6")
    MAX_CALLS = int(os.environ.get("ESG_MAX_CALLS", "4000"))
    MAX_DEPTH = int(os.environ.get("ESG_MAX_DEPTH", "25"))
    COMMIT = os.environ.get("COMMIT") in ("1", "true", "yes")
    ALLOW_UNSAFE = os.environ.get("ESG_ALLOW_UNSAFE") in ("1", "true", "yes")
    OUT = os.environ.get("OUT")

    # Fail LOUD (not mid-trace) if the sibling helpers didn't import — the tracer
    # needs ADDON_MOD_RE; without it we'd crash on the first frame.
    if ADDON_MOD_RE is None:
        raise SystemExit("esg_sample: sibling trace_flow/entrypoint_surface not "
                         "importable — set SCRIPTS_DIR to the scripts dir.")

    all_models = sorted(env.registry.models.keys())  # noqa: F821
    skipped_unsafe = []

    # --- scope → candidate object-button entrypoints -------------------------
    if MODEL:
        scope = {"mode": "model", "model": MODEL}
        target_models = [MODEL] if MODEL in env else []  # noqa: F821
    elif MODULE:
        scope = {"mode": "module", "module": MODULE}
        target_models = [m for m in all_models
                         if getattr(env[m], "_module", None) == MODULE  # noqa: F821
                         and not is_technical_model(m)]
    else:
        scope = {"mode": "instance"}
        target_models = [m for m in all_models if not is_technical_model(m)]

    candidates = []
    for name in target_models:
        try:
            model = env[name]  # noqa: F821
        except Exception:  # noqa: BLE001
            continue
        if getattr(model, "_abstract", False) or getattr(model, "_transient", False):
            continue
        module = getattr(model, "_module", None)
        n_rel = sum(1 for f in model._fields.values()
                    if f.type in ("many2one", "one2many", "many2many"))
        for attr in dir(model):
            if not is_action_method(attr):
                continue
            # getattr on the CLASS (not the instance) returns the function object
            # without evaluating any field descriptor — safe, no side effects.
            fn = getattr(type(model), attr, None)
            if not callable(fn):
                continue
            if not ALLOW_UNSAFE and is_unsafe_to_autotrace(attr):
                skipped_unsafe.append(f"{name}.{attr}")
                continue
            candidates.append({"type": "object_button", "model": name, "method": attr,
                               "ref": attr, "module": module, "n_relations": n_rel,
                               "active": True, "label": f"{name}.{attr}"})

    ranked, _ = rank_entrypoints(candidates, module_centrality, limit=0)

    # --- sample: find a record, trace the method, keep the skeleton ----------
    seed_results = []
    picked = 0
    for c in ranked:
        if picked >= MAX_SEEDS:
            break
        model_name, method = c["model"], c["method"]
        rec = _first_record(model_name)
        if rec is None:
            seed_results.append({"label": c["label"], "model": model_name, "method": method,
                                 "traced": False, "reason": "no record to trace"})
            continue
        picked += 1
        seed_results.append(_trace_skeleton(model_name, rec, method, c["label"],
                                            MAX_DEPTH, MAX_CALLS, COMMIT))

    graph = merge_skeletons(seed_results)
    result = {
        "mode": "esg",
        "scope": scope,
        "committed": COMMIT,
        "seeds": [{k: v for k, v in s.items() if k not in ("model_edges", "app_edges")}
                  for s in seed_results],
        "graph": graph,
        "summary": summarize_esg(graph, seed_results),
        "skipped_unsafe": sorted(set(skipped_unsafe)),
        "_advice": ("Read this as ORIENTATION before diving micro: which models a flow "
                    "really touches and where it crosses app boundaries. Then `odoo-ai "
                    "trace <model> <id> <method>` the one flow you're changing for the "
                    "deep call+SQL detail, and `odoo-ai brief` the models on its edges."),
        "_caveat": ("Sampled, not exhaustive. Each edge came from ONE record under the "
                    "shell user; a different group/company/record — or an automation/cron "
                    "that didn't fire here — yields different edges. Absence of an edge is "
                    "NOT proof the flow can't reach there. Re-trace to confirm. Methods whose "
                    "name signals an external/destructive effect (send/mail/payment/print/"
                    "unlink/…) are NOT auto-traced (see skipped_unsafe; override with "
                    "ESG_ALLOW_UNSAFE=1 on a throwaway DB only) — and even a 'safe' name can "
                    "send mail/webhooks under rollback, so run against a throwaway record."),
        "_warnings": WARNINGS,
    }
    _emit(result, OUT)


def _first_record(model_name):
    try:
        rec = env[model_name].search([], limit=1)  # noqa: F821
        return rec if rec else None
    except Exception as e:  # noqa: BLE001
        WARNINGS.append(f"{model_name} search failed ({type(e).__name__}: {e})")
        return None


def _trace_skeleton(model_name, record, method, label, max_depth, max_calls, commit):
    """Execute one method under a light tracer; return its flow skeleton.

    Captures touched models, model→model and addon→addon call edges, and the
    write-map (field NAMES only). Wrapped in a savepoint; rolled back unless
    COMMIT. Never raises out — a flow that errors still yields its partial
    skeleton (that's often the interesting part).
    """
    cr = env.cr  # noqa: F821
    touched, model_edges, app_edges, write_events = set(), [], [], []
    stack, n_calls = [], {"n": 0}
    err = {}

    def _model_of(frame):
        slf = frame.f_locals.get("self")
        return getattr(slf, "_name", None) if slf is not None else None

    def tracer(frame, event, arg):
        m = ADDON_MOD_RE.match(frame.f_globals.get("__name__") or "")
        if not m:
            return None  # don't trace into core ORM frames; children still seen
        name = frame.f_code.co_name
        if name.startswith("<"):
            return tracer
        if event == "call":
            addon, model = m.group(1), _model_of(frame)
            if model:
                touched.add(model)
            if stack:
                pa, pm = stack[-1]
                if pm and model and pm != model:
                    model_edges.append((pm, model))
                if pa and addon and pa != addon:
                    app_edges.append((pa, addon))
            stack.append((addon, model))
            if name in ("create", "write") and n_calls["n"] < max_calls:
                fl = frame.f_locals
                raw = fl["vals"] if "vals" in fl else fl.get("vals_list")
                write_events.append({"model": model, "method": name,
                                     "fields": _vals_field_names(raw)})
            n_calls["n"] += 1
            if n_calls["n"] > max_calls or len(stack) > max_depth:
                return None
        elif event == "return":
            if stack:
                stack.pop()
        elif event == "exception" and not err:
            et = arg[0] if isinstance(arg, tuple) and arg else None
            err.update({"model": _model_of(frame), "method": name,
                        "addon": m.group(1), "exc_type": getattr(et, "__name__", str(et))})
        return tracer

    error = None
    cr.execute("SAVEPOINT odoo_esg")
    sys.settrace(tracer)
    try:
        getattr(record, method)()
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        sys.settrace(None)
        if commit:
            cr.execute("RELEASE SAVEPOINT odoo_esg")
            env.cr.commit()  # noqa: F821 — throwaway/dev DB only
        else:
            cr.execute("ROLLBACK TO SAVEPOINT odoo_esg")

    writes = aggregate_writes(write_events)
    return {
        "label": label, "model": model_name, "method": method, "record_id": record.id,
        "traced": True, "calls": n_calls["n"], "error": error,
        "exception_origin": err or None,
        "touched_models": sorted(touched),
        "model_edges": model_edges, "app_edges": app_edges,
        "writes": writes,
        "cross_app": sorted({a for a, _ in app_edges} | {b for _, b in app_edges}),
    }


def _emit(result, OUT):
    payload = json.dumps(result, indent=2, default=str)
    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}")
    else:
        print("===ODOO_ESG_START===")
        print(payload)
        print("===ODOO_ESG_END===")


if "env" in globals():
    run()
