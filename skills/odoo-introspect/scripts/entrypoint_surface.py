"""
Odoo entrypoint surface scanner (Layer K — discovery) — run INSIDE `odoo-bin shell`.

Answers the question that comes BEFORE every other layer: **where does reality
START in this instance?** The rest of the suite is verification-driven — it tells
you the truth about a model/method/flow you ALREADY named (trace_flow needs a
record-id + method; brief needs a model). Nothing tells the agent *what is even
worth introspecting*. So an agent walks in blind, guesses an entry method
(`write`, `create`, a button it half-remembers), and misses cron / automation /
HTTP-route entrypoints entirely.

This scanner enumerates the live ENTRYPOINT SURFACE — every place execution can
begin — straight from the running registry, and ranks it so the agent starts
from the high-value roots instead of guessing:

  * object buttons   — `action_*` / `button_*` methods on concrete models (the
                       things users click and agents customize)
  * window actions   — ir.actions.act_window (open a model's views)
  * server actions   — ir.actions.server (coded/automated logic)
  * scheduled actions— ir.cron (async entrypoints that fire with no user)
  * automation rules — base.automation (triggered on create/write/unlink/time)
  * reports          — ir.actions.report
  * HTTP routes      — @http.route controllers (public site / portal / RPC)

This is DISCOVERY, not a process map. It does not claim to know the end-to-end
business process — that is a probabilistic runtime-trace distribution, not a
static graph, and pretending otherwise would make the agent confidently wrong
(see odoo-introspect philosophy). It hands you the ranked ROOTS; `trace_flow`
(Layer D) then samples the real cross-model flow from any root you pick. The
`top_trace_seeds` list is exactly that bridge — feed it to `odoo-ai esg`.

Like capabilities.py, this NEVER reads server-action / cron `code` bodies — only
names, triggers, targets, and method signatures. Nothing to gate.

Three scopes (set at most one):
    (neither)        -> instance-wide: rank entrypoints across business models +
                        the action registry + routes (technical/core noise filtered).
    MODEL=sale.order -> the entrypoint surface AROUND one model.
    MODULE=sale      -> entrypoints owned/served by one addon.

Config (env):
    MODEL / MODULE   (opt)  scope; omit both for instance-wide
    SURFACE_LIMIT    (opt)  max entrypoints to return after ranking (default 200)
    INCLUDE_ROUTES   (opt)  "0" to skip HTTP-route discovery (default: on)
    OUT              (opt)  write JSON to this path instead of stdout sentinels

The env-dependent work lives in run(); the pure helpers are module-level so they
are importable/unit-testable without Odoo. run() executes only when `env` is
present (i.e. inside `odoo-bin shell`).

Output: pure JSON wrapped in ===ODOO_SURFACE_START=== / ===ODOO_SURFACE_END===.
"""
import os
import json

WARNINGS = []

# Action-shaped method prefixes — the public entrypoints a user reaches from a
# button and an agent typically customizes. Private `_action_*` helpers are the
# IMPLEMENTATION of these and are reached via trace, not as roots, so we don't
# list them as separate seeds (keeps the surface to real roots, not internals).
ACTION_PREFIXES = ("action_", "button_", "toggle_", "open_", "print_")

# Models that are ORM/UI plumbing, never a business flow root. Prefix-matched.
TECHNICAL_MODEL_PREFIXES = (
    "ir.", "base.", "bus.", "base_import.", "web_editor.", "web_tour.",
    "report.", "mail.message", "mail.followers", "mail.notification",
    "mail.tracking", "format.", "barcodes.", "iap.", "res.config",
    "ir.actions", "ir.qweb", "ir.ui", "ir.model", "ir.attachment",
)

# Addons that are framework/plumbing — their entrypoints are rarely the patch
# target. Demoted in ranking (not excluded: a cron in `base` can still matter).
PLUMBING_MODULES = frozenset({
    "base", "web", "web_editor", "web_tour", "bus", "base_import", "base_setup",
    "http_routing", "iap", "mail_bot", "web_unsplash", "auth_signup",
    "resource", "phone_validation", "digest", "base_automation",
})

# Apps whose flows are the canonical high-value, cross-app targets. Boosted.
CORE_BUSINESS_MODULES = frozenset({
    "sale", "sale_management", "sale_stock", "stock", "account", "purchase",
    "mrp", "crm", "hr", "hr_holidays", "hr_expense", "project", "point_of_sale",
    "pos_sale", "delivery", "repair", "fleet", "subscription", "sale_subscription",
    "website_sale", "stock_account", "purchase_stock", "mrp_account",
    "l10n_", "payment", "loyalty", "helpdesk", "field_service",
})

# Well-known cross-app flow methods — the methods most likely to cascade across
# apps (and the ones AI most often gets wrong). A strong trace-seed signal.
CROSS_APP_METHODS = frozenset({
    "action_confirm", "action_post", "action_done", "action_validate",
    "button_validate", "action_cancel", "action_invoice_create",
    "action_create_invoice", "_action_confirm", "button_confirm",
    "action_assign", "action_pos_order_paid", "action_launch_stock_rule",
    "action_apply", "action_quotation_send", "action_approve",
})


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def is_technical_model(model_name):
    """True for ORM/UI plumbing models that are never a business-flow root."""
    if not model_name:
        return True
    return any(model_name == p or model_name.startswith(p)
               for p in TECHNICAL_MODEL_PREFIXES)


def is_action_method(name):
    """True for a public, action-shaped method name (a UI-button entrypoint).

    Excludes private (`_`-prefixed) helpers — those are implementation reached
    via trace, not roots — and the ORM verbs that aren't real flow roots.
    """
    if not name or name.startswith("_"):
        return False
    if name in ("action", "open"):           # bare, not a real handler
        return False
    return name.startswith(ACTION_PREFIXES)


def module_centrality(module):
    """Score an addon's likelihood of being a high-value entrypoint root.

    1.0 = core business app (sale/stock/account/...); 0.3 = framework plumbing;
    0.6 = everything else (custom/OCA addons, which ARE interesting). Localization
    (`l10n_*`) and the business set are matched by prefix/membership.
    """
    if not module:
        return 0.5
    if module in PLUMBING_MODULES:
        return 0.3
    if module in CORE_BUSINESS_MODULES or any(
            module.startswith(p) for p in CORE_BUSINESS_MODULES if p.endswith("_")):
        return 1.0
    return 0.6


def score_entrypoint(entry, centrality=module_centrality):
    """Score one entrypoint dict → (score, reasons). Pure; ranking is deterministic.

    Inputs read: type, module, ref/method, active, n_relations (optional fan-out
    hint). Higher = start here first. Reasons explain the score for the agent.
    """
    reasons = []
    type_weight = {
        "object_button": 1.0,    # what users click + agents customize → top
        "server_action": 0.7,
        "automation": 0.7,       # fires invisibly → easy to miss, worth surfacing
        "cron": 0.6,             # async, no user → classic blind spot
        "window_action": 0.45,   # navigation, not logic
        "report": 0.4,
        "route": 0.55,           # public/portal/RPC surface
    }.get(entry.get("type"), 0.4)
    reasons.append(f"type:{entry.get('type')}")

    cen = centrality(entry.get("module"))
    if cen >= 1.0:
        reasons.append("business-app")
    elif cen <= 0.3:
        reasons.append("plumbing")

    ref = entry.get("ref") or entry.get("method") or ""
    cross = ref in CROSS_APP_METHODS
    if cross:
        reasons.append("cross-app-flow")

    fanout = entry.get("n_relations") or 0
    fan_bonus = min(fanout / 40.0, 0.5)      # cap the fan-out contribution

    # inactive crons/automations are real but lower priority
    inactive_penalty = 0.0
    if entry.get("active") is False:
        inactive_penalty = 0.3
        reasons.append("inactive")

    score = (type_weight * 2.0) + (cen * 1.5) + (0.8 if cross else 0.0) \
        + fan_bonus - inactive_penalty
    return round(score, 3), reasons


def rank_entrypoints(entries, centrality=module_centrality, limit=200):
    """Score, sort (desc, stable by ref for ties), and cap a list of entrypoints.

    Returns (ranked_list, truncated_count). Each entry gains `rank` + `why`.
    """
    scored = []
    for e in entries or []:
        s, why = score_entrypoint(e, centrality)
        e = dict(e)
        e["rank"], e["why"] = s, why
        scored.append(e)
    scored.sort(key=lambda x: (-x["rank"], x.get("type", ""),
                               x.get("model", "") or "", x.get("ref", "") or ""))
    if limit and len(scored) > limit:
        return scored[:limit], len(scored) - limit
    return scored, 0


def pick_trace_seeds(ranked, k=12):
    """The top object-button entrypoints worth `odoo-ai trace` — the ESG bridge.

    Only `object_button` roots make clean trace seeds (a model+method `trace_flow`
    can drive); window actions / routes need request context, crons need their
    own args. De-dupes by (model, method).
    """
    seeds, seen = [], set()
    for e in ranked or []:
        if e.get("type") != "object_button":
            continue
        key = (e.get("model"), e.get("method"))
        if key in seen or not all(key):
            continue
        seen.add(key)
        seeds.append({"model": e["model"], "method": e["method"],
                      "rank": e.get("rank"), "label": e.get("label")})
        if len(seeds) >= k:
            break
    return seeds


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    MODULE = os.environ.get("MODULE")
    LIMIT = int(os.environ.get("SURFACE_LIMIT", "200") or "200")
    INCLUDE_ROUTES = os.environ.get("INCLUDE_ROUTES", "1") != "0"
    OUT = os.environ.get("OUT")

    def safe(fn, what, default=None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"{what} unavailable ({type(e).__name__}: {e})")
            return [] if default is None else default

    def search_read(model, domain, fields, cap=2000):
        # Cap the read: the action/cron/automation registries are small config
        # tables, but an explicit limit keeps this safe even on an outsized DB.
        return safe(lambda: env[model].sudo().search_read(domain, fields, limit=cap),  # noqa: F821
                    model, [])

    # --- action-registry entrypoints (one search per kind, instance-wide) ----
    def registry_entrypoints(model_filter=None, module_xmlids=None):
        out = []
        dom = []
        # window actions
        for r in search_read("ir.actions.act_window", dom,
                             ["name", "res_model", "view_mode"]):
            if model_filter and r.get("res_model") != model_filter:
                continue
            out.append({"type": "window_action", "model": r.get("res_model"),
                        "label": r.get("name"), "ref": None,
                        "module": _module_of_model(r.get("res_model")),
                        "view_mode": r.get("view_mode"), "active": True})
        # server actions
        for r in search_read("ir.actions.server", dom, ["name", "model_name", "usage", "state"]):
            m = r.get("model_name")
            if model_filter and m != model_filter:
                continue
            out.append({"type": "server_action", "model": m, "label": r.get("name"),
                        "ref": None, "usage": r.get("usage"), "state": r.get("state"),
                        "module": _module_of_model(m), "active": True})
        # reports
        for r in search_read("ir.actions.report", dom, ["name", "model", "report_type"]):
            m = r.get("model")
            if model_filter and m != model_filter:
                continue
            out.append({"type": "report", "model": m, "label": r.get("name"),
                        "ref": None, "report_type": r.get("report_type"),
                        "module": _module_of_model(m), "active": True})
        # crons
        for r in search_read("ir.cron", dom,
                             ["name", "model_name", "active", "interval_number", "interval_type"]):
            m = r.get("model_name")
            if model_filter and m != model_filter:
                continue
            out.append({"type": "cron", "model": m, "label": r.get("name"),
                        "ref": None, "module": _module_of_model(m),
                        "trigger": f"every {r.get('interval_number')} {r.get('interval_type')}",
                        "active": bool(r.get("active"))})
        # automation rules (model may be absent if base_automation isn't installed)
        for r in safe(lambda: env["base.automation"].sudo().search_read(  # noqa: F821
                dom, ["name", "model_name", "trigger", "active"], limit=2000),
                "base.automation", []):
            m = r.get("model_name")
            if model_filter and m != model_filter:
                continue
            out.append({"type": "automation", "model": m, "label": r.get("name"),
                        "ref": None, "module": _module_of_model(m),
                        "trigger": r.get("trigger"), "active": bool(r.get("active"))})
        return out

    # cache model→module so centrality is cheap and consistent
    _model_module_cache = {}

    def _module_of_model(model_name):
        if not model_name:
            return None
        if model_name in _model_module_cache:
            return _model_module_cache[model_name]
        mod = None
        try:
            if model_name in env:  # noqa: F821
                mod = getattr(env[model_name], "_module", None)  # noqa: F821
        except Exception:  # noqa: BLE001
            mod = None
        _model_module_cache[model_name] = mod
        return mod

    # --- object-button methods on concrete models ----------------------------
    def model_action_methods(model_names):
        out = []
        for name in model_names:
            try:
                model = env[name]  # noqa: F821
            except Exception:  # noqa: BLE001
                continue
            if getattr(model, "_abstract", False) or getattr(model, "_transient", False):
                continue
            module = getattr(model, "_module", None)
            n_relations = sum(1 for f in model._fields.values()
                              if f.type in ("many2one", "one2many", "many2many"))
            seen = set()
            for attr in dir(model):
                if not is_action_method(attr) or attr in seen:
                    continue
                fn = getattr(type(model), attr, None)
                if not callable(fn):
                    continue
                seen.add(attr)
                out.append({"type": "object_button", "model": name, "method": attr,
                            "ref": attr, "label": _humanize(attr), "module": module,
                            "n_relations": n_relations, "active": True})
        return out

    def _humanize(method):
        base = method
        for p in ACTION_PREFIXES:
            if base.startswith(p):
                base = base[len(p):]
                break
        return base.replace("_", " ").strip().capitalize() or method

    # --- HTTP routes (the genuinely new scanner) -----------------------------
    def http_routes():
        """Discover @http.route endpoints from loaded controller classes.

        Version-tolerant: walks odoo.http.Controller subclasses and reads each
        method's `routing` attr (set by @http.route in every supported version),
        rather than building a routing Map (which needs a request/db context).
        """
        out = []
        try:
            import odoo.http as ohttp
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"http routes unavailable ({type(e).__name__}: {e})")
            return out

        classes = []
        # Preferred: the per-module controller registry, when present.
        cpm = getattr(ohttp, "controllers_per_module", None)
        if isinstance(cpm, dict) and cpm:
            for mod, items in cpm.items():
                for item in items:
                    cls = item[1] if isinstance(item, (list, tuple)) and len(item) > 1 else item
                    classes.append((mod, cls))
        else:
            # Fallback: walk Controller subclasses; derive the addon from __module__.
            def walk(cls):
                for sub in cls.__subclasses__():
                    classes.append((_addon_of_module(getattr(sub, "__module__", "")), sub))
                    walk(sub)
            try:
                walk(ohttp.Controller)
            except Exception as e:  # noqa: BLE001
                WARNINGS.append(f"controller walk failed ({type(e).__name__}: {e})")

        seen = set()
        for mod, cls in classes:
            for attr in dir(cls):
                try:
                    fn = getattr(cls, attr)
                except Exception:  # noqa: BLE001
                    continue
                routing = getattr(fn, "routing", None) or getattr(fn, "original_routing", None)
                if not isinstance(routing, dict):
                    continue
                for path in routing.get("routes", []) or []:
                    key = (path, routing.get("type"))
                    if key in seen:
                        continue
                    seen.add(key)
                    methods = routing.get("methods")
                    out.append({
                        "type": "route", "model": None, "ref": path, "label": path,
                        "module": mod or _addon_of_module(getattr(cls, "__module__", "")),
                        "controller": f"{getattr(cls, '__name__', '?')}.{attr}",
                        "route_type": routing.get("type", "http"),
                        "auth": routing.get("auth"),
                        "methods": list(methods) if methods else None,
                        "active": True,
                    })
        return out

    def _addon_of_module(dotted):
        # odoo.addons.<addon>.controllers.x -> <addon>
        parts = (dotted or "").split(".")
        if len(parts) >= 3 and parts[0] == "odoo" and parts[1] == "addons":
            return parts[2]
        return None

    # --- choose the population to scan based on scope ------------------------
    all_models = sorted(env.registry.models.keys())  # noqa: F821

    if MODEL:
        scope = {"mode": "model", "model": MODEL}
        if MODEL not in env:  # noqa: F821
            result = {"mode": "surface", "scope": scope, "found": False,
                      "note": "model not in registry (typo? module not installed?)"}
            _emit(result, OUT)
            return
        target_models = [MODEL]
        registry_eps = registry_entrypoints(model_filter=MODEL)
        routes = []  # routes aren't model-scoped
    elif MODULE:
        scope = {"mode": "module", "module": MODULE}
        # models owned by this module
        target_models = [m for m in all_models
                         if _module_of_model(m) == MODULE and not is_technical_model(m)]
        registry_eps = [e for e in registry_entrypoints()
                        if e.get("module") == MODULE or
                        (e.get("model") and _module_of_model(e["model"]) == MODULE)]
        routes = [r for r in (http_routes() if INCLUDE_ROUTES else [])
                  if r.get("module") == MODULE]
    else:
        scope = {"mode": "instance", "note": "business models; technical/plumbing filtered"}
        target_models = [m for m in all_models if not is_technical_model(m)]
        registry_eps = registry_entrypoints()
        routes = http_routes() if INCLUDE_ROUTES else []

    button_eps = model_action_methods(target_models)
    entries = button_eps + registry_eps + routes
    ranked, truncated = rank_entrypoints(entries, module_centrality, LIMIT)

    counts = {}
    for e in entries:
        counts[e["type"]] = counts.get(e["type"], 0) + 1

    result = {
        "mode": "surface",
        "scope": scope,
        "counts": counts,
        "total_entrypoints": len(entries),
        "returned": len(ranked),
        "truncated": truncated,
        "entrypoints": ranked,
        "top_trace_seeds": pick_trace_seeds(ranked),
        "_advice": ("Start from the top-ranked roots, not a guessed method. Object "
                    "buttons are the things users click and agents customize; crons / "
                    "automations / routes are the entrypoints AI usually forgets. Feed "
                    "`top_trace_seeds` to `odoo-ai esg` (or `odoo-ai trace <model> <id> "
                    "<method>`) to see the REAL cross-model flow before you write code."),
        "_caveat": ("This is DISCOVERY of roots, not a process map. The real flow is a "
                    "runtime-trace distribution (conditional on group/company/automations) "
                    "— trace it, don't assume it. A method existing here is not proof it "
                    "is reachable for a given user/company; confirm with trace + security."),
        "_warnings": WARNINGS,
    }
    _emit(result, OUT)


def _emit(result, OUT):
    payload = json.dumps(result, indent=2, default=str)
    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}")
    else:
        print("===ODOO_SURFACE_START===")
        print(payload)
        print("===ODOO_SURFACE_END===")


# `env` is injected by `odoo-bin shell`; its presence means we're running for
# real. Absent (e.g. an import in a unit test) → run() is skipped and only the
# pure helpers above are exposed.
if "env" in globals():
    run()
