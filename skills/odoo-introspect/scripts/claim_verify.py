"""
BYO-index claim verifier (Layer I) — run INSIDE `odoo-bin shell`.

External knowledge — a hosted Odoo index, an OCA doc, a `grep`, the local
doc-index (Layer J), or another coding agent — produces CLAIMS about Odoo
("sale.order has field commitment_date", "action_confirm is a safe override
point"). This tool treats each claim as a *hypothesis* and VERIFIES it against
THIS running instance, returning one verdict per claim:

  confirmed    — the artifact exists here (existence probe passed)
  contradicted — the claim asserts something this instance does NOT have
  needs_shell  — exists, but the claim's SAFETY/behaviour part (override / MRO /
                 super / runtime / depends) needs a deeper layer — run brief/trace
  needs_human  — subjective claim with no objective probe
  absent       — nothing probeable in the claim

"Static indexes suggest; the running instance disposes." This is how the suite
buys ecosystem breadth from ANY upstream source without trusting it: bring claims
you are authorised to use; we verify locally. It REUSES native_check's existence
probes (make_handlers + dispatch_leaf) so a verified claim is gated identically
to a native-check.

Input — env CLAIMS_FILE=<path.json> (or CLAIMS=<json>): a list of claims, each
  {"source": "...", "model": "...", "field"?: "...", "method"?: "...",
   "xmlid"?: "...", "claim"?: "free text", "evidence"?: [...],
   "probe"?: {...an explicit native_check leaf...}}

The pure helpers (claim_target / claim_to_probe / needs_runtime / classify /
recommend_for) need no Odoo and are unit-tested; run() executes only inside the
shell. Output: pure JSON between ===ODOO_CLAIMS_START=== / ===ODOO_CLAIMS_END===.
"""
import os
import re
import sys
import json

# native_check is a sibling script; when piped to `odoo-bin shell` there is no
# __file__, so the CLI exports SCRIPTS_DIR. Only trust an ABSOLUTE SCRIPTS_DIR
# that actually contains a sibling native_check.py — never a relative path (a
# hostile cwd-relative dir could shadow the real module). The unit test puts the
# scripts dir on sys.path itself, so the import still resolves there.
_SD = os.environ.get("SCRIPTS_DIR")
if _SD and os.path.isabs(_SD) and os.path.isfile(os.path.join(_SD, "native_check.py")):
    if _SD not in sys.path:
        sys.path.insert(0, _SD)
elif _SD:
    sys.stderr.write(f"claim_verify: ignoring untrusted SCRIPTS_DIR={_SD!r} "
                     "(must be an absolute path with a sibling native_check.py)\n")
import native_check  # noqa: E402  (reuse make_handlers / dispatch_leaf / eval_probe / PROBE_KINDS)

WARNINGS = []
# Words that signal the claim asserts BEHAVIOUR/safety, not mere existence —
# existence alone can't settle those; route to a deeper layer.
BEHAVIOUR_RE = re.compile(
    r"\b(override|hook|super|mro|runtime|depend|order|safe|react|"
    r"side.?effect|compute|onchange|constrain|trigger|flow)", re.I)


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def claim_target(claim):
    """A short human label for what the claim is about."""
    m = claim.get("model")
    if m and claim.get("field"):
        return f"{m}.{claim['field']}"
    if m and claim.get("method"):
        return f"{m}.{claim['method']}()"
    if m:
        return m
    if claim.get("xmlid"):
        return claim["xmlid"]
    return (claim.get("claim") or "<unspecified>")[:60]


def claim_to_probe(claim):
    """Map a claim to a native_check existence-probe leaf, or None.

    An explicit `probe` (a valid leaf) wins. Otherwise infer from the most
    specific identifier present: field > method > xmlid > model.
    """
    p = claim.get("probe")
    if isinstance(p, dict) and p.get("kind") in native_check.PROBE_KINDS:
        return p
    m = claim.get("model")
    if m and claim.get("field"):
        return {"kind": "field_exists", "model": m, "field": claim["field"]}
    if m and claim.get("method"):
        return {"kind": "method_exists", "model": m, "method": claim["method"]}
    if claim.get("xmlid"):
        return {"kind": "xmlid_exists", "xmlid": claim["xmlid"]}
    if m:
        return {"kind": "model_exists", "model": m}
    return None


def needs_runtime(claim, probe=None):
    """True if the claim asserts behaviour/safety beyond mere existence.

    An explicit ``claim_type`` wins: ``existence`` → no; ``hook_safety`` /
    ``runtime_behavior`` / ``migration`` / ``security`` → yes. Otherwise a
    method/hook probe is treated as a behaviour claim by default (existence of a
    method doesn't prove it's the right/safe hook), falling back to a keyword
    scan of the free-text claim.
    """
    ct = (claim.get("claim_type") or "").lower()
    if ct == "existence":
        return False
    if ct in ("hook_safety", "runtime_behavior", "migration", "security"):
        return True
    if isinstance(probe, dict) and probe.get("kind") == "method_exists":
        return True
    return bool(BEHAVIOUR_RE.search(claim.get("claim") or ""))


def classify(claim, probe, passed, eval_ok=True):
    """Verdict from (claim, probe, probe-passed, could-the-probe-be-evaluated).

    ``eval_ok=False`` (a malformed/unknown probe that could not be evaluated) is
    NOT a contradiction — it maps to ``needs_human`` so the agent never tells the
    user "the upstream source is wrong" when the verifier simply couldn't tell.
    """
    if probe is None:
        return "needs_human" if claim.get("claim") else "absent"
    if not eval_ok:
        return "needs_human"
    if not passed:
        return "contradicted"
    if needs_runtime(claim, probe):
        return "needs_shell"
    return "confirmed"


def recommend_for(verdict, claim):
    """The next action the agent should take for this verdict (or None)."""
    if verdict == "needs_shell":
        m = claim.get("model", "<model>")
        meth = claim.get("method")
        tail = f" --methods {meth}" if meth else ""
        return (f"existence confirmed; the safety/behaviour part is unproven — "
                f"run `odoo-ai brief {m}{tail}` (and trace it if it's a business flow)")
    if verdict == "contradicted":
        return "do NOT use this claim here; introspect for the real name/hook on this instance"
    if verdict == "needs_human":
        return "subjective claim — a human (or a failing-then-passing test) must decide"
    return None


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    raw = os.environ.get("CLAIMS")
    path = os.environ.get("CLAIMS_FILE")
    if path and os.path.isfile(path):
        raw = open(path).read()
    if not raw:
        raise SystemExit("Set CLAIMS_FILE=<path.json> or CLAIMS=<json> (a list of claims).")
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"claims JSON parse failed: {e}")
    claims = data if isinstance(data, list) else data.get("claims", [])

    handlers = native_check.make_handlers(env)  # noqa: F821  (env from odoo shell)

    def checker(leaf):
        return native_check.dispatch_leaf(leaf, handlers, on_error=WARNINGS.append)

    results, counts = [], {}
    for c in claims:
        if not isinstance(c, dict):
            continue
        probe = claim_to_probe(c)
        passed, evidence = (False, [])
        eval_ok = True
        if probe is not None:
            passed, evidence = native_check.eval_probe(probe, checker)
            # A leaf that couldn't be evaluated (malformed/unknown) carries a
            # status marker — don't let that masquerade as a contradiction.
            eval_ok = not any(isinstance(e, dict)
                              and e.get("status") in ("error", "unknown_kind")
                              for e in evidence)
        verdict = classify(c, probe, passed, eval_ok)
        counts[verdict] = counts.get(verdict, 0) + 1
        entry = {
            "source": c.get("source"),
            "target": claim_target(c),
            "claim": c.get("claim"),
            "verdict": verdict,
            "evidence": ([e for e in evidence if e.get("found")] if passed else evidence),
        }
        if probe is not None and not eval_ok:
            entry["recommend"] = ("probe could not be evaluated (malformed/unknown kind) "
                                  "— verify this claim manually against the instance")
        else:
            rec = recommend_for(verdict, c)
            if rec:
                entry["recommend"] = rec
        results.append(entry)

    out = {
        "verified": results,
        "summary": counts,
        "total": len(results),
        "_contract": (
            "confirmed = exists on THIS instance · contradicted = the upstream source is "
            "WRONG here (do not use it) · needs_shell = exists but the safety/behaviour claim "
            "needs brief/trace · needs_human = subjective · absent = nothing probeable. "
            "Trust nothing marked contradicted; re-introspect."),
        "_caveat": (
            "Existence is THIS instance + version. A confirmed name is real here; a confirmed "
            "*hook* is not proof the override lands at the right MRO layer or runs — that is "
            "needs_shell. BYO-claims must be data you are authorised to use (don't paste a "
            "paywalled index's output if its terms forbid it)."),
        "_warnings": WARNINGS,
    }
    print("===ODOO_CLAIMS_START===")
    print(json.dumps(out, indent=2, default=str))
    print("===ODOO_CLAIMS_END===")


if "env" in globals():
    run()
