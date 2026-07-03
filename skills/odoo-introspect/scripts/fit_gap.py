"""
Fit-Gap alpha (sale/stock/account) — run INSIDE `odoo-bin shell`.

Given a list of REQUIREMENT strings, this answers "for each requirement, does
this instance already ship a known Odoo pattern, or is there a gap?" — by joining
two layers exactly the way native-check does:

  1. Curated capability CARDS (JSON, shipped in the odoo-capabilities skill) —
     the durable expert knowledge (what a native primitive is for, its EN+VN
     intents, when it's NOT enough).
  2. The RUNNING REGISTRY — the objective existence gate. Each matched card's
     `probe` is evaluated against THIS instance; the modules it needs are checked.

It then CLASSIFIES each requirement on a five-rung ladder (native_config →
config_plus_gap → module_available_not_installed → pattern_known_not_present →
no_known_pattern) and attaches a heuristic effort band + risk class. This is
DECISION SUPPORT for a functional consultant — it compares observed instance
facts against known Odoo capability patterns; it does NOT replace functional
analysis, and heuristic rungs need human validation.

Scope is a constrained alpha: only cards whose `domain` is in DOMAINS (default
sale/stock/account) are considered. The pure helpers (classify_requirement /
effort_band / risk_class / detect_gaps / summarize) need no Odoo and are
unit-tested; run() executes only inside the shell (gated on `env` in globals).

Corpus transport
----------------
Like native-check, the `odoo-ai` CLI INJECTS the card corpus as JSON on this
script's own stdin (env var CARDS_JSON) so it reaches the shell wherever odoo-bin
runs (local, Docker, ssh). CARDS_DIR is a filesystem fallback for manual/local
invocation. Requirements ride in the same way: REQUIREMENTS_B64 (a base64 JSON
array, injected on stdin) or REQUIREMENTS_FILE (a path the shell process can read).

Usage
-----
    REQUIREMENTS_B64="$(python3 -c 'import base64,json;print(base64.b64encode(json.dumps([\"auto-number delivery slips\",\"down payment invoice\"]).encode()).decode())')" \
        DOMAINS=sale,stock,account \
        CARDS_DIR=/path/to/odoo-capabilities/references/cards \
        SCRIPTS_DIR=/path/to/odoo-introspect/scripts \
        odoo-bin shell -d <DB> --no-http < fit_gap.py
    # or: REQUIREMENTS_FILE=/path/to/requirements.json (a JSON array of strings)

Output: pure JSON wrapped in ===ODOO_FITGAP_START=== / ===ODOO_FITGAP_END===.
"""
import os
import sys
import json
import base64
from pathlib import Path

WARNINGS = []

# Reuse native-check's pure helpers (recall matching, probe combinator + dispatch,
# card loading, tokenizer). When piped to `odoo-bin shell` there is NO __file__,
# so the CLI exports SCRIPTS_DIR; only trust an ABSOLUTE SCRIPTS_DIR that actually
# holds the sibling (never a cwd-relative dir that could shadow it). Unit tests add
# the scripts dir to sys.path themselves, so the import resolves there too.
_SD = os.environ.get("SCRIPTS_DIR")
if _SD and os.path.isabs(_SD) and os.path.isfile(os.path.join(_SD, "native_check.py")):
    if _SD not in sys.path:
        sys.path.insert(0, _SD)
elif _SD:
    sys.stderr.write(f"fit_gap: ignoring untrusted SCRIPTS_DIR={_SD!r} "
                     "(must be an absolute path with a sibling native_check.py)\n")
try:
    from native_check import (match_cards, eval_probe, make_handlers,
                              dispatch_leaf, load_cards, load_cards_from_json,
                              tokenize, strip_diacritics)
except Exception:  # noqa: BLE001 — keep import-safe; run() fails loudly if unresolved
    match_cards = None


# --- Classification ladder (rung -> decision-support framing) -----------------
CLASSIFICATIONS = (
    "native_config", "config_plus_gap", "module_available_not_installed",
    "pattern_known_not_present", "no_known_pattern",
)

# Effort BANDS are relative buckets, never estimates (see effort_band_note).
_EFFORT = {
    "native_config": "S",
    "config_plus_gap": "M",
    "module_available_not_installed": "M",
    "pattern_known_not_present": "L",
    "no_known_pattern": "L",
}

_ASSUMPTIONS = {
    "native_config": ["A native Odoo capability for this is present AND active on this instance."],
    "config_plus_gap": ["The native capability covers the core; the listed gaps still need config or light dev."],
    "module_available_not_installed": ["A standard/OCA module ships this pattern, but it is NOT installed here."],
    "pattern_known_not_present": ["Odoo has a known pattern for this, but its probe did not confirm it on this instance."],
    "no_known_pattern": ["No curated capability card matched (within the alpha's domain scope) — may still exist in Odoo."],
}

_WHAT_CHANGES = {
    "native_config": ["A requirement detail the standard flow can't express would move this to config_plus_gap."],
    "config_plus_gap": ["Confirming a listed gap is truly out-of-standard-config scope."],
    "module_available_not_installed": ["Installing/evaluating the named module(s), then re-gating, would confirm fit."],
    "pattern_known_not_present": ["Enabling the feature-group/edition or a config, then re-gating, may surface it."],
    "no_known_pattern": ["A functional review, or adding a matching capability card, could reclassify this."],
}

# EN+VN high-risk signals (folded, diacritic-free) — accounting, stock valuation,
# security/permissions, multi-company, payroll. Substring-matched on folded text.
_HIGH_RISK_TERMS = (
    "accounting", "valuation", "security", "permission", "multi-company",
    "multi company", "payroll", "ke toan", "ton kho", "dinh gia", "phan quyen",
    "luong",
)

_DECISION_CONTRACT = (
    "Classifications compare observed instance facts against known Odoo capability "
    "patterns. This is decision support for a functional consultant, not a "
    "replacement. Gated evidence is instance-verified; heuristic items require "
    "human validation.")

_CAVEAT = (
    "Constrained alpha: only cards whose domain is in the configured DOMAINS are "
    "considered (patterns outside them are not seen here). Recall is a wide net; "
    "gaps and risk_class are heuristic keyword signals, not judgements. Gated "
    "existence is THIS instance + version — absent here may mean Community-vs-"
    "Enterprise or a disabled feature-group, not that the capability can't exist. "
    "Effort bands are relative buckets, not estimates.")


def _dedupe(items):
    """Order-preserving de-duplication (skips falsy)."""
    seen, out = set(), []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _card_ref(card):
    """The lightweight card reference surfaced per requirement."""
    return {k: (card or {}).get(k) for k in ("id", "title", "domain", "primitive")}


def _evidence_from(matches, only_found):
    """Collect probe evidence across matches. only_found=True keeps just the
    instance-VERIFIED (found) checks; False keeps the negative gating evidence so
    'we looked, it isn't here' is transparent, not silent."""
    out = []
    for m in matches:
        cid = ((m.get("card") or {}).get("id"))
        for e in (m.get("probe_result") or []):
            if only_found and not e.get("found"):
                continue
            out.append({**e, "card": cid})
    return out


def detect_gaps(requirement, card):
    """PURE: which of a card's `when_not_enough` phrases the requirement text hits.

    Token-overlap heuristic (diacritic-folded, EN+VN): a phrase is a hit when at
    least half its content tokens (min 1) appear in the requirement. Used to build
    each match's `gaps`; deliberately heuristic (gaps always need human review).
    """
    req_tokens = set(tokenize(requirement))
    hits = []
    for phrase in card.get("when_not_enough", []) or []:
        pt = set(tokenize(phrase))
        if pt and len(pt & req_tokens) >= max(1, len(pt) // 2):
            hits.append(phrase)
    return hits


def classify_requirement(matches):
    """PURE: classify one requirement from its gated card matches.

    Each match = {card, gated: bool, probe_result: [...], modules_installed: bool,
    gaps: [...]}. Ladder (first rung that applies wins):
      · any card gated + gaps    -> config_plus_gap   (confidence gated)
      · any card gated, no gaps  -> native_config     (confidence gated)
      · a matched card's modules NOT installed -> module_available_not_installed
      · matched card(s), modules installed, probe failed -> pattern_known_not_present
      · no card matched          -> no_known_pattern  (custom_or_process_change)
    Returns the classification-driven fields; run() adds effort/risk/requirement.
    """
    if not matches:
        cls = "no_known_pattern"
        return {
            "classification": cls, "confidence": "heuristic",
            "evidence": [], "gaps": [], "recommended_modules": [],
            "matched_cards": [], "assumptions": list(_ASSUMPTIONS[cls]),
            "what_would_change_this": list(_WHAT_CHANGES[cls]),
            "recommendation": "custom_or_process_change",
        }

    matched_cards = [_card_ref(m.get("card")) for m in matches]
    gated = [m for m in matches if m.get("gated")]

    if gated:
        gaps = _dedupe([g for m in gated for g in (m.get("gaps") or [])])
        cls = "config_plus_gap" if gaps else "native_config"
        return {
            "classification": cls, "confidence": "gated",
            "evidence": _evidence_from(gated, only_found=True),
            "gaps": gaps, "recommended_modules": [],
            "matched_cards": matched_cards, "assumptions": list(_ASSUMPTIONS[cls]),
            "what_would_change_this": list(_WHAT_CHANGES[cls]),
        }

    not_installed = [m for m in matches if not m.get("modules_installed")]
    if not_installed:
        cls = "module_available_not_installed"
        mods = _dedupe([mod for m in not_installed
                        for mod in ((m.get("card") or {}).get("modules") or [])])
        return {
            "classification": cls, "confidence": "heuristic",
            "evidence": _evidence_from(matches, only_found=False),
            "gaps": [], "recommended_modules": mods,
            "matched_cards": matched_cards, "assumptions": list(_ASSUMPTIONS[cls]),
            "what_would_change_this": list(_WHAT_CHANGES[cls]),
        }

    cls = "pattern_known_not_present"
    return {
        "classification": cls, "confidence": "heuristic",
        "evidence": _evidence_from(matches, only_found=False),
        "gaps": [], "recommended_modules": [],
        "matched_cards": matched_cards, "assumptions": list(_ASSUMPTIONS[cls]),
        "what_would_change_this": list(_WHAT_CHANGES[cls]),
        "recommendation": "custom_or_process_change",
    }


def effort_band(classification):
    """PURE: relative effort bucket S/M/L for a classification (band, not estimate)."""
    return _EFFORT.get(classification, "L")


def risk_class(requirement_text, matched_domains):
    """PURE: 'high' when the requirement text/domain touches a high-risk area
    (accounting, stock valuation, security/permissions, multi-company, payroll —
    EN+VN keyword lists), else 'normal'. The 'account' domain is always high."""
    if "account" in (matched_domains or []):
        return "high"
    folded = strip_diacritics(requirement_text or "")
    return "high" if any(term in folded for term in _HIGH_RISK_TERMS) else "normal"


def summarize(requirements):
    """PURE: per-classification counts (+ total) over the classified requirements."""
    counts = {c: 0 for c in CLASSIFICATIONS}
    for r in requirements or []:
        c = r.get("classification")
        counts[c] = counts.get(c, 0) + 1
    return {"total": len(requirements or []), "by_classification": counts}


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def _load_requirements(b64, path):
    """Decode the requirement list from REQUIREMENTS_B64 (base64 JSON array) or
    REQUIREMENTS_FILE (a JSON-array path). Returns a list of non-empty strings."""
    if b64:
        try:
            data = json.loads(base64.b64decode(b64).decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"fit_gap: REQUIREMENTS_B64 decode failed ({type(e).__name__}: {e})")
    elif path and Path(path).is_file():
        try:
            data = json.loads(Path(path).read_text())
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"fit_gap: REQUIREMENTS_FILE parse failed ({type(e).__name__}: {e})")
    else:
        raise SystemExit("fit_gap: set REQUIREMENTS_B64 (base64 JSON array) or "
                         "REQUIREMENTS_FILE (a JSON-array path).")
    if not isinstance(data, list):
        raise SystemExit("fit_gap: requirements must be a JSON array of strings.")
    return [str(r).strip() for r in data if str(r).strip()]


def run():
    DOMAINS = [d.strip() for d in (os.environ.get("DOMAINS") or "sale,stock,account").split(",") if d.strip()]
    REQUIREMENTS_B64 = os.environ.get("REQUIREMENTS_B64")
    REQUIREMENTS_FILE = os.environ.get("REQUIREMENTS_FILE")
    CARDS_JSON = os.environ.get("CARDS_JSON")
    CARDS_DIR = os.environ.get("CARDS_DIR")
    OUT = os.environ.get("OUT")

    # Fail LOUD (not mid-run) if the sibling helpers didn't import.
    if match_cards is None:
        raise SystemExit("fit_gap: sibling native_check not importable — set "
                         "SCRIPTS_DIR to the odoo-introspect scripts dir.")

    requirements = _load_requirements(REQUIREMENTS_B64, REQUIREMENTS_FILE)
    if not requirements:
        raise SystemExit("fit_gap: no requirements after parsing (empty array).")

    # Corpus transport (issue #3): prefer the INJECTED blob (rides stdin, reaches
    # the shell wherever odoo-bin runs); CARDS_DIR is the local-only fallback.
    if CARDS_JSON:
        cards, warns = load_cards_from_json(CARDS_JSON)
    elif CARDS_DIR:
        cards, warns = load_cards(CARDS_DIR)
    else:
        raise SystemExit("fit_gap: set CARDS_JSON (injected corpus) or CARDS_DIR "
                         "(the odoo-capabilities/references/cards path).")
    WARNINGS.extend(warns)

    # Constrained alpha: keep only cards in the configured domains.
    domain_set = set(DOMAINS)
    cards = [c for c in cards if c.get("domain") in domain_set]

    # Fail LOUDLY on an empty in-scope corpus — almost always means the cards never
    # reached this shell (Docker/remote host path unreadable). A confident all-
    # "no_known_pattern" would be a silent false negative, the worst outcome here.
    if not cards:
        raise SystemExit(
            f"fit_gap: 0 capability cards in domains {','.join(DOMAINS)} — the corpus "
            "is empty, out of scope, or did not reach this shell. If odoo-bin runs in "
            "Docker/remote, a host card path is not visible inside it; upgrade odoo-ai "
            f"so it injects the corpus on stdin (CARDS_JSON). [CARDS_JSON set="
            f"{bool(CARDS_JSON)} CARDS_DIR={CARDS_DIR!r} warnings={WARNINGS}]")

    # Existence-probe handlers + a module-installed checker, bound to this live env.
    handlers = make_handlers(env)  # noqa: F821  (env injected by odoo-bin shell)

    def checker(leaf):
        return dispatch_leaf(leaf, handlers, on_error=WARNINGS.append)

    def module_installed(mod):
        ok, _ = dispatch_leaf({"kind": "module_installed", "module": mod},
                              handlers, on_error=WARNINGS.append)
        return ok

    results = []
    for req in requirements:
        matches, domains_hit = [], set()
        for score, card in match_cards(req, cards):
            domains_hit.add(card.get("domain"))
            probe = card.get("probe")
            if probe:
                gated, probe_result = eval_probe(probe, checker)
            else:
                gated = False
                probe_result = [{"check": "no existence probe defined", "found": False}]
            mods = card.get("modules") or []
            mods_ok = all(module_installed(m) for m in mods) if mods else True
            matches.append({
                "card": card, "gated": gated, "probe_result": probe_result,
                "modules_installed": mods_ok, "gaps": detect_gaps(req, card),
                "score": score,
            })

        cls = classify_requirement(matches)
        classification = cls["classification"]
        entry = {
            "requirement": req,
            "classification": classification,
            "confidence": cls["confidence"],
            "evidence": cls["evidence"],
            "gaps": cls["gaps"],
            "recommended_modules": cls["recommended_modules"],
            "matched_cards": cls["matched_cards"],
            "assumptions": cls["assumptions"],
            "what_would_change_this": cls["what_would_change_this"],
            "effort_band": effort_band(classification),
            "effort_band_note": "band heuristic, not an estimate",
            "risk_class": risk_class(req, sorted(d for d in domains_hit if d)),
        }
        if cls.get("recommendation"):
            entry["recommendation"] = cls["recommendation"]
        results.append(entry)

    out = {
        "domains": DOMAINS,
        "cards_loaded": len(cards),
        "requirements": results,
        "summary": summarize(results),
        "_decision_contract": _DECISION_CONTRACT,
        "_caveat": _CAVEAT,
        "_warnings": WARNINGS,
    }
    payload = json.dumps(out, indent=2, default=str)
    if OUT:
        Path(OUT).write_text(payload)
        print(f"WROTE {OUT}")
    else:
        print("===ODOO_FITGAP_START===")
        print(payload)
        print("===ODOO_FITGAP_END===")


if "env" in globals():
    run()  # noqa: F821 — `env` injected by odoo-bin shell
