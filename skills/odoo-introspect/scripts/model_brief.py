"""
Odoo model brief generator (Layer A) — run INSIDE `odoo-bin shell`.

Give an AI agent ground truth from the running registry so it customizes from
fact, not memory. Covers: identity, field inventory (+ which modules touched
each field), security dossier (ACL + record rules), the auto-trigger surface
(server actions / automated actions / crons), and per-method MRO with a
source+super() analysis.

IMPORTANT about MRO: the chain is the *potential* super() path, NOT a guarantee
of runtime order. Whether each layer actually runs depends on whether it calls
super(), where, conditionally, early returns, or context flags. This script
reports has_super / super_position / returns_before_super / hooks_called so the
agent can reason about it — but for big business flows, confirm with
`trace_flow.py`. Those fields are heuristics (regex over source, comment-
stripped); each analyze_source result carries "heuristic": True.

Non-fatal problems (e.g. a field-module lookup or a getsource() that failed) are
collected in a module-level WARNINGS list and emitted as "_warnings" instead of
being swallowed silently.

The env-dependent work lives in run(); the pure helpers (analyze_source, …) are
module-level so they are importable/unit-testable without Odoo. run() executes
only when `env` is present (i.e. inside `odoo-bin shell`).

Usage
-----
    MODEL=sale.order METHODS=action_confirm,write \
        odoo-bin shell -d <DB> --no-http < model_brief.py

    # auto-detect every method overridden across addons:
    MODEL=sale.order odoo-bin shell -d <DB> --no-http < model_brief.py

    # include full source bodies for the requested methods (verbose):
    MODEL=sale.order METHODS=action_confirm SOURCE=1 odoo-bin shell -d <DB> < model_brief.py

    # include full server-action / cron CODE bodies (may contain secrets —
    # trusted context only; default is a redacted preview):
    MODEL=sale.order CODE=1 odoo-bin shell -d <DB> < model_brief.py

    # write to file:
    MODEL=sale.order OUT=/tmp/brief.json odoo-bin shell -d <DB> < model_brief.py

Output: pure JSON wrapped in ===ODOO_BRIEF_START=== / ===ODOO_BRIEF_END===.
"""
import os
import re
import json
import inspect
from collections import Counter

WARNINGS = []
HOOK_RE = re.compile(r"self\.(_[a-z][a-z0-9_]*)\s*\(")
ADDON_RE = re.compile(r"odoo\.addons\.([^.]+)\.")


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def addon_of(cls):
    mod = getattr(cls, "_module", None)
    if mod:
        return mod
    m = ADDON_RE.match(cls.__module__ or "")
    return m.group(1) if m else None


def unwrap(fn):
    if isinstance(fn, (classmethod, staticmethod)):
        fn = fn.__func__
    try:
        return inspect.unwrap(fn)
    except Exception:
        return fn


def analyze_source(src):
    out = {"has_super": False, "super_position": None,
           "returns_before_super": None, "hooks_called": [], "heuristic": True}
    if not src:
        return out
    # Strip #-comments per line before structural scanning, to cut false
    # positives from commented-out super()/return. A '#' inside a string literal
    # can still slip through — this is a heuristic, hence "heuristic": True.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    out["hooks_called"] = sorted(set(HOOK_RE.findall(code)))
    if "super(" not in code:
        return out
    out["has_super"] = True
    head = code.split("super(", 1)[0]
    # heuristic position: where does super() land within the body?
    lines = code.splitlines()
    super_line = next((i for i, ln in enumerate(lines) if "super(" in ln), None)
    frac = (super_line / len(lines)) if super_line is not None and lines else None
    if frac is None:
        pos = "unknown"
    elif frac < 0.34:
        pos = "early (before custom logic)"
    elif frac > 0.66:
        pos = "late (after custom logic)"
    else:
        pos = "middle"
    if re.search(r"\breturn\b", head) or re.search(r"\bif\b", head):
        pos += " / conditional-or-early-return present"
    out["super_position"] = pos
    out["returns_before_super"] = bool(re.search(r"\breturn\b", head))
    return out


def _decorator_meta(fn):
    meta = {}
    for attr, key in (("_depends", "depends"), ("_constrains", "constrains"),
                      ("_onchange", "onchange")):
        val = getattr(fn, attr, None)
        if val:
            meta[key] = list(val) if not callable(val) else "<dynamic>"
    api = getattr(fn, "_api", None)
    if api:
        meta["api"] = api
    return meta or None


def normalize_selection(sel, max_items=60):
    """Turn a raw selection into a list of {value, label}, or a marker dict.

    Selection fields are the #1 source of AI guessing wrong literals (e.g.
    `state='confirmed'` when Odoo uses `'sale'`). A list/tuple of pairs becomes
    [{"value", "label"}]; a method-name string or callable can't be resolved
    without env, so it returns {"_dynamic": ...}. Returns None for non-selection.
    """
    if sel is None:
        return None
    if isinstance(sel, str):
        return {"_dynamic": f"method:{sel}"}
    if callable(sel):
        return {"_dynamic": "callable"}
    try:
        out = []
        for pair in list(sel)[:max_items]:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                out.append({"value": pair[0], "label": str(pair[1])})
            elif isinstance(pair, (list, tuple)) and len(pair) == 1:
                out.append({"value": pair[0]})
            else:
                out.append({"value": pair})
        if len(list(sel)) > max_items:
            out.append({"_truncated": f"+{len(list(sel)) - max_items} more"})
        return out or None
    except Exception:
        return None


def repr_domain(dom, max_len=300):
    """JSON-safe representation of a field domain: keep str/list, mark callables."""
    if dom is None:
        return None
    if callable(dom):
        return "<callable>"
    if isinstance(dom, str):
        return dom[:max_len] + ("…" if len(dom) > max_len else "")
    try:
        s = str(dom)
        return s[:max_len] + ("…" if len(s) > max_len else "")
    except Exception:
        return "<unrepresentable>"


def gate_code(records, field="code", want_code=False, preview_len=200):
    """Replace a raw code body with a safe summary unless want_code is set.

    Server-action / cron code bodies routinely embed secrets, endpoints, and
    sensitive business logic. Dumping them by default is a leak risk on an
    open-source tool whose output gets pasted into an external LLM. By default
    we emit has_code / code_len / code_preview (a short, head-only slice) and
    require CODE=1 to include the full body. Mutates each record in place and
    returns the same list for convenience.
    """
    for rec in records or []:
        if not isinstance(rec, dict) or field not in rec:
            continue
        body = rec.get(field)
        if not body or not isinstance(body, str):
            rec[field + "_present"] = False
            rec.pop(field, None)
            continue
        if want_code:
            rec[field + "_present"] = True
            rec[field + "_len"] = len(body)
            continue
        rec.pop(field, None)
        rec[field + "_present"] = True
        rec[field + "_len"] = len(body)
        rec[field + "_preview"] = body[:preview_len] + ("…" if len(body) > preview_len else "")
    return records


def classify_addon_path(module_path, core_dir, enterprise_marker="enterprise"):
    """Classify a module by its on-disk location — ground truth, not author.

    Returns 'core' (ships under Odoo's base addons dir), 'enterprise' (path has
    an `enterprise` segment), 'local' (anything else — your/third-party addons,
    scrutinize before depending), or 'unknown' (no path resolved). The author
    field is unreliable: custom modules routinely copy `author = 'Odoo S.A.'`.
    """
    if not module_path:
        return "unknown"
    norm = module_path.rstrip("/")
    if core_dir:
        cd = core_dir.rstrip("/")
        if norm == cd or norm.startswith(cd + "/"):
            return "core"
    if enterprise_marker in norm.split("/"):
        return "enterprise"
    return "local"


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    if not MODEL:
        raise SystemExit("Set MODEL, e.g. MODEL=sale.order")
    METHODS = [m.strip() for m in os.environ.get("METHODS", "").split(",") if m.strip()]
    WANT_SOURCE = os.environ.get("SOURCE") in ("1", "true", "yes")
    WANT_CODE = os.environ.get("CODE") in ("1", "true", "yes")
    OUT = os.environ.get("OUT")

    model = env[MODEL]            # noqa: F821  (env comes from the odoo shell)
    mcls = type(model)

    def _safe_read(model_name, domain, cols):
        try:
            return env[model_name].sudo().search_read(domain, cols)  # noqa: F821
        except Exception as e:
            WARNINGS.append(f"search_read {model_name} failed ({type(e).__name__}: {e})")
            return [{"_error": str(e)}]

    def mro_for(method):
        chain = []
        for cls in mcls.__mro__:
            if method not in cls.__dict__:
                continue
            raw = cls.__dict__[method]
            fn = unwrap(raw)
            code = getattr(fn, "__code__", None)
            try:
                src = inspect.getsource(fn)
            except Exception as e:
                src = None
                WARNINGS.append(f"getsource failed for {cls.__module__}.{cls.__name__}."
                                f"{method} ({type(e).__name__}); super-analysis skipped")
            entry = {
                "addon": addon_of(cls),                 # clean module name e.g. "sale_stock"
                "class": f"{cls.__module__}.{cls.__name__}",
                "file": inspect.getsourcefile(fn) if code else None,
                "line": code.co_firstlineno if code else None,
                "decorators": _decorator_meta(fn),
            }
            entry.update(analyze_source(src))
            if WANT_SOURCE:
                entry["source"] = src
            chain.append(entry)
        return chain  # index 0 = potential first to run; super() descends 0 -> 1 -> ...

    # --- 1. Identity & capabilities ------------------------------------------
    fields_set = model._fields
    identity = {
        "model": MODEL,
        "table": model._table,
        "description": model._description,
        "order": model._order,
        "rec_name": model._rec_name,
        "inherit": getattr(model, "_inherit", None),
        "inherits": dict(getattr(model, "_inherits", {})),
        "transient": model._transient,
        "auto": model._auto,
        "capabilities": {
            "mail_thread": "message_ids" in fields_set,
            "activities": "activity_ids" in fields_set,
            "portal": "access_url" in fields_set,
            "company_dependent_fields": sorted(
                n for n, f in fields_set.items() if getattr(f, "company_dependent", False)
            ),
        },
    }

    # --- 2. Field inventory --------------------------------------------------
    field_modules = {}
    try:
        for r in env["ir.model.fields"].search([("model", "=", MODEL)]):  # noqa: F821
            field_modules[r.name] = r.modules
    except Exception as e:
        WARNINGS.append(f"field_modules lookup failed ({type(e).__name__}: {e}); "
                        "'modules' will be null per field")

    fields = {}
    for name, f in sorted(fields_set.items()):
        info = {
            "type": f.type,
            "string": f.string,
            "help": getattr(f, "help", None) or None,
            "store": f.store,
            "required": f.required,
            "readonly": f.readonly,
            "index": bool(getattr(f, "index", False)),
            "copy": getattr(f, "copy", None),
            "translate": bool(getattr(f, "translate", False)),
            "tracking": getattr(f, "tracking", None) or None,
            "has_default": getattr(f, "default", None) is not None,
            "compute": f.compute,
            "inverse": getattr(f, "inverse", None),
            "search": getattr(f, "search", None),
            "related": ".".join(f.related) if getattr(f, "related", None) else None,
            "depends": list(f.depends) if getattr(f, "depends", None) else None,
            "comodel": getattr(f, "comodel_name", None),
            "groups": f.groups or None,
            "company_dependent": getattr(f, "company_dependent", False),
            "modules": field_modules.get(name),
        }
        # Selection literals — resolve method-based selections via env when possible.
        if f.type == "selection":
            sel = None
            try:
                sel = normalize_selection(f._description_selection(env))  # noqa: F821
            except Exception:
                sel = normalize_selection(getattr(f, "selection", None))
            info["selection"] = sel
        # Relational extras the agent otherwise guesses.
        if f.type in ("many2one", "many2many"):
            info["ondelete"] = getattr(f, "ondelete", None)
        if f.type in ("one2many", "many2many"):
            info["inverse_name"] = getattr(f, "inverse_name", None)
        if f.type in ("many2one", "one2many", "many2many"):
            dom = repr_domain(getattr(f, "domain", None))
            if dom:
                info["domain"] = dom
        fields[name] = info

    # --- 3. Security dossier -------------------------------------------------
    security = {
        "access_rights": _safe_read(
            "ir.model.access", [("model_id.model", "=", MODEL)],
            ["name", "group_id", "perm_read", "perm_write", "perm_create", "perm_unlink"],
        ),
        "record_rules": _safe_read(
            "ir.rule", [("model_id.model", "=", MODEL)],
            ["name", "active", "global", "domain_force", "groups",
             "perm_read", "perm_write", "perm_create", "perm_unlink"],
        ),
    }

    # --- 4. Auto-trigger surface (fires WITHOUT a user clicking) -------------
    # Code bodies (server actions / crons) are gated behind CODE=1: by default
    # we emit a redacted summary (present/len/preview) so secrets and sensitive
    # business logic don't leak into an external LLM. Set CODE=1 to include full
    # bodies (trusted context only).
    triggers = {
        "server_actions": gate_code(_safe_read(
            "ir.actions.server", [("model_id.model", "=", MODEL)],
            ["name", "state", "usage", "code"],
        ), want_code=WANT_CODE),
        "automated_actions": _safe_read(
            "base.automation", [("model_id.model", "=", MODEL)],
            ["name", "trigger", "filter_domain", "active"],
        ),
        "crons": gate_code(_safe_read(
            "ir.cron", [("model_id.model", "=", MODEL)],
            ["name", "active", "interval_number", "interval_type", "code"],
        ), want_code=WANT_CODE),
        "_code_gating": "full" if WANT_CODE else "redacted (set CODE=1 for full bodies)",
    }

    # --- 5. MRO + source/super analysis per method --------------------------
    if not METHODS:
        cnt = Counter()
        for cls in mcls.__mro__:
            for attr, val in cls.__dict__.items():
                if callable(val) and not attr.startswith("__"):
                    cnt[attr] += 1
        METHODS = sorted(m for m, c in cnt.items() if c >= 2)

    methods = {m: mro_for(m) for m in METHODS}

    # --- 6. Recommended manifest depends ------------------------------------
    # A new custom module must depend on the standard modules in a method's
    # chain, otherwise the override sits at the wrong MRO position. Depending on
    # the highest-level one usually pulls the rest transitively.
    chain_addons = []
    for chain in methods.values():
        for e in chain:
            a = e.get("addon")
            if a and a not in chain_addons and a != "base":
                chain_addons.append(a)
    # Classify by on-disk path (ground truth), NOT module author — custom modules
    # routinely ship `author = 'Odoo S.A.'`, so author can't be trusted.
    import odoo  # available inside the shell
    core_dir, mod_paths = None, {}
    try:
        base_path = odoo.modules.module.get_module_path("base", display_warning=False)
        core_dir = os.path.dirname(base_path) if base_path else None
        for a in chain_addons:
            try:
                mod_paths[a] = odoo.modules.module.get_module_path(a, display_warning=False)
            except Exception:
                mod_paths[a] = None
    except Exception as e:
        WARNINGS.append(f"module path lookup failed ({type(e).__name__}: {e}); "
                        "location split unavailable")

    by_location = {"core": [], "enterprise": [], "local": [], "unknown": []}
    for a in chain_addons:
        by_location[classify_addon_path(mod_paths.get(a), core_dir)].append(a)
    recommended = {
        "method_chain_addons": chain_addons,
        "by_location": by_location,
        "module_paths": mod_paths,
        "note": "'core'/'enterprise' addons ship with Odoo — depend on the one that OWNS the "
                "method you extend so your override resolves ABOVE it in the MRO (the "
                "highest-level one usually pulls the rest transitively). 'local' addons are "
                "yours/third-party: do NOT blindly depend on every local addon you traversed — "
                "that creates accidental coupling. Verify the addon you're writing in isn't "
                "already in this list. Classification is by on-disk path, not author.",
    }

    # --- 7. Emit ------------------------------------------------------------
    brief = {
        "identity": identity,
        "field_count": len(fields),
        "fields": fields,
        "security": security,
        "auto_triggers": triggers,
        "overridden_methods": list(methods.keys()),
        "methods": methods,
        "manifest_depends": recommended,
        "_warnings": WARNINGS,
        "_caveat": "MRO is the POTENTIAL super() chain. Use has_super / super_position "
                   "/ returns_before_super to judge actual order; trace big flows.",
    }
    payload = json.dumps(brief, indent=2, default=str)

    if OUT:
        with open(OUT, "w") as fh:
            fh.write(payload)
        print(f"WROTE {OUT}  ({len(fields)} fields, {len(methods)} methods)")
    else:
        print("===ODOO_BRIEF_START===")
        print(payload)
        print("===ODOO_BRIEF_END===")


# `env` is injected by `odoo-bin shell`; its presence means we're running for
# real. Absent (e.g. an import in a unit test) → run() is skipped and only the
# pure helpers above are exposed.
if "env" in globals():
    run()
