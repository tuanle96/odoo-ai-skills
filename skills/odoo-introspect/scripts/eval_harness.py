"""
Odoo hallucination eval harness (Layer K — measurement) — run INSIDE `odoo-bin shell`.

The closed-loop answer to "did hallucinations actually go DOWN?" Without it, every
other layer is optimizing vibes. This runs a curated benchmark of claims — the
classic LLM Odoo hallucinations (account.invoice, customer_id, fields_view_get,
a 'customer' selection value) alongside stable reals — against the LIVE registry
and scores the verification gate's verdicts vs the curated labels:

  * 'absent'  cases are hallucinations the gate must CATCH  → detection_rate
  * 'present' cases are real facts the gate must CONFIRM     → truth_recall

A sound gate scores 1.0 / 1.0 on a standard instance. A leak (a fake confirmed)
or a miss (a real rejected) is surfaced explicitly — that is a regression signal
you can track per release instead of guessing. It reuses native_check's existence
probes (`make_handlers`/`dispatch_leaf`) so the eval measures the SAME machinery
the suite gates with — not a parallel re-implementation that could drift.

It can also score an arbitrary CLAIMS benchmark (an agent's captured output, or
your own fixture) the same way: feed `{cases:[{probe, expected, ...}]}`.

The benchmark ships at odoo-introspect/references/eval-benchmark.json and is
injected on stdin by the CLI (so it works under a Docker/remote odoo-bin, issue
#3). `requires_module` skips a case when its addon isn't installed, so the base
benchmark never false-fails.

Config (env):
    BENCHMARK_JSON  (opt)  the benchmark, injected as content (preferred)
    BENCHMARK_FILE  (opt)  fallback path to a benchmark JSON (local shell)
    OUT             (opt)  write JSON to this path instead of stdout sentinels

Pure helpers (classify_case, score_cases, compute_metrics) are module-level /
unit-testable; run() executes only inside odoo-bin shell.

Output: pure JSON wrapped in ===ODOO_EVAL_START=== / ===ODOO_EVAL_END===.
"""
import os
import sys
import json
from pathlib import Path

# native_check is a sibling; when piped to `odoo-bin shell` there is NO __file__,
# so the CLI exports SCRIPTS_DIR. Only trust an ABSOLUTE SCRIPTS_DIR that holds
# the sibling (never a cwd-relative dir that could shadow it). Unit tests add the
# dir to sys.path themselves, so the import still resolves there.
_SD = os.environ.get("SCRIPTS_DIR")
if _SD and os.path.isabs(_SD) and os.path.isfile(os.path.join(_SD, "native_check.py")):
    if _SD not in sys.path:
        sys.path.insert(0, _SD)
elif _SD:
    sys.stderr.write(f"eval_harness: ignoring untrusted SCRIPTS_DIR={_SD!r} "
                     "(must be an absolute path with a sibling native_check.py)\n")
try:
    from native_check import make_handlers, dispatch_leaf
except Exception:  # noqa: BLE001 — keep import-safe for pure unit tests
    make_handlers = dispatch_leaf = None

WARNINGS = []


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def classify_case(expected, found):
    """Map (curated label, gate verdict) → an outcome string.

    truth_confirmed   : present & gate found it       (good)
    truth_missed      : present but gate said absent   (gate miss / module gap)
    hallucination_caught : absent & gate said absent   (good — the whole point)
    hallucination_leaked : absent but gate found it    (DANGER — false-native)
    """
    if expected == "present":
        return "truth_confirmed" if found else "truth_missed"
    if expected == "absent":
        return "hallucination_leaked" if found else "hallucination_caught"
    return "unlabeled"


def score_cases(evaluated):
    """Aggregate a list of evaluated cases into a confusion dict.

    Each case dict must carry `applicable` (bool) and `verdict` (from
    classify_case). Skipped (not applicable) cases are counted aside, never as
    pass/fail. Pure: no env.
    """
    conf = {"truth_confirmed": 0, "truth_missed": 0,
            "hallucination_caught": 0, "hallucination_leaked": 0, "skipped": 0}
    for c in evaluated or []:
        if not c.get("applicable", True):
            conf["skipped"] += 1
            continue
        conf[c.get("verdict", "unlabeled")] = conf.get(c.get("verdict", "unlabeled"), 0) + 1
    return conf


def compute_metrics(conf):
    """Headline metrics from a confusion dict. Pure.

    detection_rate = hallucinations caught / total applicable hallucinations
    truth_recall   = reals confirmed / total applicable reals
    gate_sound     = no leaks AND no misses (the release-gate boolean)
    """
    halluc = conf.get("hallucination_caught", 0) + conf.get("hallucination_leaked", 0)
    reals = conf.get("truth_confirmed", 0) + conf.get("truth_missed", 0)
    applicable = halluc + reals
    correct = conf.get("hallucination_caught", 0) + conf.get("truth_confirmed", 0)
    return {
        "detection_rate": round(conf.get("hallucination_caught", 0) / halluc, 4) if halluc else None,
        "truth_recall": round(conf.get("truth_confirmed", 0) / reals, 4) if reals else None,
        "accuracy": round(correct / applicable, 4) if applicable else None,
        "applicable": applicable,
        "gate_sound": conf.get("hallucination_leaked", 0) == 0 and conf.get("truth_missed", 0) == 0,
    }


def per_category(evaluated):
    """Break detection/recall down by claim category (model/field/method/...)."""
    cats = {}
    for c in evaluated or []:
        if not c.get("applicable", True):
            continue
        cat = c.get("category", "other")
        b = cats.setdefault(cat, {"truth_confirmed": 0, "truth_missed": 0,
                                  "hallucination_caught": 0, "hallucination_leaked": 0})
        b[c.get("verdict", "unlabeled")] = b.get(c.get("verdict", "unlabeled"), 0) + 1
    return {cat: {**b, **compute_metrics(b)} for cat, b in sorted(cats.items())}


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def _load_benchmark():
    raw = os.environ.get("BENCHMARK_JSON")
    if not raw and os.environ.get("BENCHMARK_FILE"):
        try:
            raw = Path(os.environ["BENCHMARK_FILE"]).read_text()
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"benchmark file unreadable ({type(e).__name__}: {e})")
    if not raw:
        raise SystemExit("No benchmark: set BENCHMARK_JSON (injected) or BENCHMARK_FILE")
    data = json.loads(raw)
    return data.get("cases", data if isinstance(data, list) else [])


def run():
    OUT = os.environ.get("OUT")
    cases = _load_benchmark()
    handlers = make_handlers(env)  # noqa: F821

    def _module_installed(name):
        ok, _ = handlers["module_installed"]({"module": name})
        return ok

    evaluated = []
    for c in cases:
        probe = c.get("probe") or {}
        expected = c.get("expected")
        req = c.get("requires_module")
        applicable = True
        verdict = "unlabeled"
        found = None
        evidence = None
        if req and not _module_installed(req):
            applicable = False
            verdict = "skipped"
        else:
            found, evidence = dispatch_leaf(probe, handlers, on_error=WARNINGS.append)
            verdict = classify_case(expected, bool(found))
        evaluated.append({
            "id": c.get("id"), "category": c.get("category", "other"),
            "claim": c.get("claim"), "expected": expected,
            "requires_module": req, "applicable": applicable,
            "found": found, "verdict": verdict,
            "evidence": (evidence or {}).get("check") if evidence else None,
        })

    conf = score_cases(evaluated)
    metrics = compute_metrics(conf)
    leaked = [c for c in evaluated if c["verdict"] == "hallucination_leaked"]
    missed = [c for c in evaluated if c["verdict"] == "truth_missed"]

    result = {
        "mode": "eval",
        "odoo_version": _odoo_version(),
        "benchmark_size": len(cases),
        "confusion": conf,
        "metrics": metrics,
        "by_category": per_category(evaluated),
        "hallucinations_leaked": [{"id": c["id"], "claim": c["claim"]} for c in leaked],
        "reals_missed": [{"id": c["id"], "claim": c["claim"]} for c in missed],
        "cases": evaluated,
        "_advice": ("gate_sound=true means the gate caught every classic hallucination "
                    "and confirmed every real fact on THIS instance. Track detection_rate "
                    "/ truth_recall across releases — a drop is a real regression, not a "
                    "vibe. A leak (hallucination_leaked) is the dangerous one: a fake the "
                    "gate confirmed → fix the probe or the registry assumption."),
        "_caveat": ("Labels are curated + version-stable; a 'leaked' case on a heavily "
                    "customized instance may be a genuine custom model/field (not a gate "
                    "bug) — inspect it. This measures the existence GATE; behavioural "
                    "correctness (does the flow do the right thing) is trace/security/test "
                    "territory."),
        "_warnings": WARNINGS,
    }
    _emit(result, OUT)


def _odoo_version():
    try:
        import odoo
        return ".".join(str(x) for x in odoo.release.version_info[:2])
    except Exception:  # noqa: BLE001
        return None


def _emit(result, OUT):
    payload = json.dumps(result, indent=2, default=str)
    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}")
    else:
        print("===ODOO_EVAL_START===")
        print(payload)
        print("===ODOO_EVAL_END===")


if "env" in globals():
    run()
