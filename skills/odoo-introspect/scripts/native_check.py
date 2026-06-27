"""
Native-capability check (Layer H, gate-then-rank) — run INSIDE `odoo-bin shell`.

Given a free-text REQUIREMENT (+ optional MODEL), this answers "does Odoo already
ship something for this, here, now?" by joining two layers:

  1. Curated capability CARDS (JSON, shipped in the odoo-capabilities skill) —
     the durable expert knowledge: what a native primitive is for, its intents
     (incl. Vietnamese), the reuse advice, the right hook, when it's not enough.
  2. The RUNNING REGISTRY — the objective existence gate. Each matched card
     carries a `probe`; we evaluate it against THIS instance and attach the
     evidence (module installed? model/wizard present? field there?).

This script does the OBJECTIVE half — recall-filter the cards by the requirement,
then EXISTENCE-GATE each against the live registry. It deliberately does NOT rank
final relevance or decide: that subjective judgement is the agent's job, over the
`confirmed_candidates` (which carry instance evidence). Evidence or silence — a
card only becomes a candidate the agent may recommend if its probe passed here.

The pure helpers (tokenize / strip_diacritics / recall_score / match_cards /
eval_probe) need no Odoo and are unit-tested; run() executes only inside the
shell (gated on `env` in globals).

Usage
-----
    REQUIREMENT="auto-number our delivery slips" MODEL=stock.picking \
        CARDS_DIR=/path/to/odoo-capabilities/references/cards \
        odoo-bin shell -d <DB> --no-http < native_check.py

Output: pure JSON wrapped in ===ODOO_NCHECK_START=== / ===ODOO_NCHECK_END===.
"""
import os
import re
import json
import math
import unicodedata
from pathlib import Path

WARNINGS = []

# Tiny EN+VN stopword set — drop connective noise so overlap reflects intent.
STOPWORDS = frozenset({
    "the", "a", "an", "to", "of", "for", "when", "if", "is", "are", "on", "in",
    "and", "or", "with", "our", "we", "i", "it", "this", "that", "be", "do",
    "khi", "neu", "cho", "va", "la", "mot", "co", "cac", "tu", "theo", "thi",
    "se", "cua", "nay", "muon", "can",
})


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def strip_diacritics(s):
    """Lowercase + remove diacritics, Vietnamese-aware (đ→d, NFD strip)."""
    if not s:
        return ""
    s = s.replace("đ", "d").replace("Đ", "D")
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if not unicodedata.combining(c)).lower()


def tokenize(s):
    """Diacritic-folded alphanumeric tokens, stopwords + 1-char noise removed."""
    folded = strip_diacritics(s or "")
    toks = re.split(r"[^a-z0-9]+", folded)
    return [t for t in toks if len(t) > 1 and t not in STOPWORDS]


def card_tokens(card):
    """Union of tokens from a card's title, intents, domain, primitive."""
    parts = [card.get("title", ""), card.get("domain", ""), card.get("primitive", "")]
    parts += list(card.get("intents", []) or [])
    out = set()
    for p in parts:
        out.update(tokenize(p))
    return out


def phrase_bonus(requirement, card):
    """Strong precision signal: a full intent phrase appearing verbatim
    (diacritic-folded) in the requirement — so "down payment" / "đặt cọc" land
    hard even amid other words."""
    req_norm = strip_diacritics(requirement or "")
    bonus = 0.0
    for intent in card.get("intents", []) or []:
        folded = strip_diacritics(intent)
        if folded and folded in req_norm:
            bonus += 2.0
    return bonus


def recall_score(requirement, card):
    """Lexical baseline: token overlap + phrase bonus (corpus-free, per-card).

    match_cards ranks with the corpus-aware TF-IDF cosine below; this stays the
    simple signal, exposed for clarity and unit-tested.
    """
    overlap = len(set(tokenize(requirement)) & card_tokens(card))
    return overlap + phrase_bonus(requirement, card)


# --- TF-IDF vector-space recall (dependency-free "embeddings") ---------------
# Dense neural embeddings would need a model at runtime (a heavy dep, or a
# network/API call from inside odoo-bin shell) — against this tool's offline
# design, and the agent already does the final semantic ranking. So recall uses
# classical sparse TF-IDF cosine over the card corpus: IDF down-weights tokens
# common to many cards (create/order/value) and up-weights the distinctive ones.
def corpus_idf(cards):
    """Smoothed inverse document frequency per token over the card corpus."""
    n = len(cards) or 1
    df = {}
    for c in cards:
        for t in card_tokens(c):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((1 + n) / (1 + d)) + 1.0 for t, d in df.items()}


def tfidf_vector(tokens, idf):
    """Sparse TF-IDF vector {token: weight} for a token iterable."""
    tf = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    return {t: c * idf.get(t, 0.0) for t, c in tf.items()}


def cosine(a, b):
    """Cosine similarity of two sparse vectors (dicts). 0 when either is empty."""
    if not a or not b:
        return 0.0
    dot = sum(a[t] * b[t] for t in (set(a) & set(b)))
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def match_cards(requirement, cards, top_k=8, min_score=0.0):
    """Vector-space recall: rank cards by TF-IDF cosine + intent-phrase bonus.

    A wide net (the agent ranks final precision) — keep every card with any
    signal (score > min_score), best top_k first.
    """
    if not cards:
        return []
    idf = corpus_idf(cards)
    qv = tfidf_vector(tokenize(requirement), idf)
    out = []
    for c in cards:
        score = cosine(qv, tfidf_vector(card_tokens(c), idf)) + phrase_bonus(requirement, c)
        if score > min_score:
            out.append((round(score, 4), c))
    out.sort(key=lambda sc: sc[0], reverse=True)
    return out[:top_k]


def merge_learned(cards, learned):
    """Fold learned mappings into the corpus (the learning loop).

    Each learned entry `{id, learned_intents:[...]}` augments that card's intents
    (so a real-world phrasing recalls it next time); an entry carrying full card
    fields (intents + probe) is added as a new learned card. Returns
    (cards, learned_card_count). Mutates the freshly-loaded card dicts in place.
    """
    by_id = {c.get("id"): c for c in cards}
    added = 0
    for entry in learned or []:
        cid = entry.get("id")
        if not cid:
            continue
        extra = entry.get("learned_intents") or []
        if cid in by_id:
            card = by_id[cid]
            existing = set(card.get("intents", []))
            card["intents"] = (card.get("intents", [])
                               + [p for p in extra if p and p not in existing])
        elif entry.get("intents") and entry.get("probe"):
            cards.append(entry)
            by_id[cid] = entry
            added += 1
    return cards, added


def eval_probe(probe, checker):
    """Evaluate an existence probe ({any|all|leaf}) via an injected checker.

    `checker(leaf) -> (passed: bool, evidence: dict)` is the only env-dependent
    part; injecting it keeps this combinator pure and unit-testable. Returns
    (passed, [evidence...]) collecting every leaf's evidence (found or not).
    """
    if not isinstance(probe, dict):
        return False, [{"check": "malformed", "found": False, "detail": repr(probe)[:80]}]
    if "any" in probe:
        ev, ok = [], False
        for sub in probe["any"]:
            p, e = eval_probe(sub, checker)
            ev += e
            ok = ok or p
        return ok, ev
    if "all" in probe:
        ev, ok = [], True
        for sub in probe["all"]:
            p, e = eval_probe(sub, checker)
            ev += e
            ok = ok and p
        return ok, ev
    # leaf: checker returns (passed, evidence_dict) — wrap the dict into a list
    # so any/all's `ev += e` always concatenates lists, never spreads dict keys.
    passed, evidence = checker(probe)
    return passed, [evidence]


def load_cards(cards_dir):
    """Load + flatten every *.json card file in a directory. Returns (cards, warnings)."""
    cards, warns = [], []
    d = Path(cards_dir)
    if not d.is_dir():
        return [], [f"cards dir not found: {cards_dir}"]
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception as e:  # noqa: BLE001
            warns.append(f"{path.name}: parse failed ({type(e).__name__}: {e})")
            continue
        items = data if isinstance(data, list) else data.get("cards", [])
        for c in items:
            if isinstance(c, dict) and c.get("id") and c.get("intents"):
                cards.append(c)
            else:
                warns.append(f"{path.name}: skipped a card missing id/intents")
    return cards, warns


def _public(card):
    """The card fields surfaced to the agent (drop the internal probe spec)."""
    return {k: card.get(k) for k in (
        "id", "title", "domain", "primitive", "modules", "models", "hooks",
        "reuse_advice", "when_not_enough")}


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    REQUIREMENT = os.environ.get("REQUIREMENT")
    if not REQUIREMENT:
        raise SystemExit('Set REQUIREMENT, e.g. REQUIREMENT="auto-number delivery slips"')
    MODEL = os.environ.get("MODEL") or None
    CARDS_DIR = os.environ.get("CARDS_DIR")
    LEARN_FILE = os.environ.get("LEARN_FILE")
    OUT = os.environ.get("OUT")
    if not CARDS_DIR:
        raise SystemExit("Set CARDS_DIR (the odoo-capabilities/references/cards path).")

    cards, warns = load_cards(CARDS_DIR)
    WARNINGS.extend(warns)

    # Learning loop: fold any captured requirement→card mappings into the corpus
    # so real-world phrasings recall better over time (see `odoo-ai native-learn`).
    learned_mappings = 0
    if LEARN_FILE and Path(LEARN_FILE).is_file():
        try:
            data = json.loads(Path(LEARN_FILE).read_text())
            entries = data if isinstance(data, list) else data.get("learned", [])
            cards, _ = merge_learned(cards, entries)
            learned_mappings = len(entries)
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"learn file {LEARN_FILE}: {type(e).__name__}: {e}")

    # MODEL tokens help recall (e.g. "sale.order" → sale/order); the agent still
    # ranks true relevance afterwards.
    req_for_match = REQUIREMENT + (" " + MODEL if MODEL else "")
    matched = match_cards(req_for_match, cards)

    def checker(leaf):
        kind = leaf.get("kind")
        try:
            if kind == "module_installed":
                m = leaf["module"]
                ok = bool(env["ir.module.module"].sudo().search(  # noqa: F821
                    [("name", "=", m), ("state", "=", "installed")], limit=1))
                return ok, {"check": f"module {m} installed", "found": ok}
            if kind == "model_exists":
                m = leaf["model"]
                ok = m in env  # noqa: F821  (Environment.__contains__ → registry)
                return ok, {"check": f"model {m} in registry", "found": ok}
            if kind == "field_exists":
                m, f = leaf["model"], leaf["field"]
                ok = (m in env) and (f in env[m]._fields)  # noqa: F821
                return ok, {"check": f"{m}.{f} field present", "found": ok}
            if kind == "method_exists":
                m, meth = leaf["model"], leaf["method"]
                ok = (m in env) and hasattr(env[m], meth)  # noqa: F821
                return ok, {"check": f"{m}.{meth}() present", "found": ok}
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"probe {leaf} failed ({type(e).__name__}: {e})")
            return False, {"check": str(leaf)[:60], "found": False, "detail": "probe error"}
        WARNINGS.append(f"unknown probe kind: {kind}")
        return False, {"check": f"unknown probe kind {kind}", "found": False}

    confirmed, unconfirmed = [], []
    for score, card in matched:
        probe = card.get("probe")
        entry = _public(card)
        entry["score"] = score
        if not probe:
            entry["why_absent"] = [{"check": "no existence probe defined", "found": False}]
            unconfirmed.append(entry)
            continue
        passed, evidence = eval_probe(probe, checker)
        if passed:
            entry["evidence"] = [e for e in evidence if e.get("found")]
            confirmed.append(entry)
        else:
            entry["why_absent"] = evidence
            unconfirmed.append(entry)

    out = {
        "requirement": REQUIREMENT,
        "model": MODEL,
        "cards_loaded": len(cards),
        "learned_mappings": learned_mappings,
        "considered": len(matched),
        "confirmed_candidates": confirmed,
        "unconfirmed_candidates": unconfirmed,
        "_decision_contract": (
            "Now RANK the confirmed_candidates by TRUE relevance to the requirement, "
            "pick what to reuse, reject the rest WITH a reason, and name the genuine gap "
            "that needs custom code. Then introspect that gap and extend at the card's hook. "
            "Evidence or silence: only claim a native feature exists if it is in "
            "confirmed_candidates — those carry instance evidence. Surface "
            "unconfirmed_candidates as 'exists in Odoo but not active here' (with why_absent), "
            "never as a recommendation. If confirmed_candidates is empty, run "
            "`odoo-ai capabilities <model>` for the full surface before writing code."),
        "_caveat": ("Recall is a wide net (the agent ranks precision). Existence is THIS "
                    "instance + version; absent here may mean Community-vs-Enterprise or a "
                    "disabled feature-group, not that the capability can't exist."),
        "_warnings": WARNINGS,
    }
    payload = json.dumps(out, indent=2, default=str)
    if OUT:
        Path(OUT).write_text(payload)
        print(f"WROTE {OUT}")
    else:
        print("===ODOO_NCHECK_START===")
        print(payload)
        print("===ODOO_NCHECK_END===")


if "env" in globals():
    run()
