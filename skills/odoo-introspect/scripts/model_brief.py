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


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    if not MODEL:
        raise SystemExit("Set MODEL, e.g. MODEL=sale.order")
    METHODS = [m.strip() for m in os.environ.get("METHODS", "").split(",") if m.strip()]
    WANT_SOURCE = os.environ.get("SOURCE") in ("1", "true", "yes")
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
        fields[name] = {
            "type": f.type,
            "string": f.string,
            "store": f.store,
            "required": f.required,
            "readonly": f.readonly,
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
    triggers = {
        "server_actions": _safe_read(
            "ir.actions.server", [("model_id.model", "=", MODEL)],
            ["name", "state", "usage", "code"],
        ),
        "automated_actions": _safe_read(
            "base.automation", [("model_id.model", "=", MODEL)],
            ["name", "trigger", "filter_domain", "active"],
        ),
        "crons": _safe_read(
            "ir.cron", [("model_id.model", "=", MODEL)],
            ["name", "active", "interval_number", "interval_type", "code"],
        ),
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
    recommended = {
        "method_chain_addons": chain_addons,
        "note": "Have your custom module depend on these (or the highest-level one) "
                "so your override resolves ABOVE them in the MRO. Verify the addon "
                "you are writing in is not already one of these.",
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
