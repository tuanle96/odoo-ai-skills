"""
Odoo field reverse-impact scanner (Layer E) — run INSIDE `odoo-bin shell`.

Answers the question the other layers don't: "if I rename / retype / drop THIS
field, what breaks?" model_brief (Layer A) and trace_flow (Layer D) look
FORWARD (what is this model, what runs). This looks BACKWARD — every place in
the running registry that depends on a field:

  - other stored/related computes whose @api.depends names it (will go stale)
  - related= fields pointing at it (downstream chains)
  - views (ir.ui.view) that render it (xpath / form / list / search)
  - record rules (ir.rule.domain_force) and saved filters (ir.filters) using it
  - server / automated actions whose code or filter_domain mentions it
  - ir.model.fields metadata (which modules define/extend it)

Use it before a rename/retype/drop so the migration (→ odoo-migration) and the
patch (→ odoo-dev) cover every dependent — not just the one you noticed.

The env-dependent work is in run(); the pure, unit-testable helpers
(depends_hit, mentions_field, classify_severity) are module-level so they import
without Odoo. run() executes only when `env` is present (inside odoo-bin shell).

Usage
-----
    MODEL=sale.order FIELD=commitment_date \
        odoo-bin shell -d <DB> --no-http < field_refs.py

Output: pure JSON wrapped in ===ODOO_REFS_START=== / ===ODOO_REFS_END===.
"""
import os
import re
import json

WARNINGS = []


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def depends_hit(depends, field, comodel_fields=None):
    """Does an @api.depends / field.depends list reference `field`?

    Matches both a bare local dependency ('commitment_date') and the LAST
    segment of a dotted path ('order_id.commitment_date'), since the dotted form
    is how a *related* model reaches across to this field.
    """
    if not depends:
        return False
    for dep in depends:
        if not dep:
            continue
        parts = dep.split(".")
        if dep == field or parts[-1] == field:
            return True
    return False


# Word-boundary match so 'date' doesn't hit 'commitment_date' and vice-versa.
def mentions_field(text, field):
    """Whole-identifier occurrence of `field` in arbitrary text (arch / domain /
    code). Heuristic — a same-named field on another model in the same blob
    could false-positive; callers scope the search by model where possible."""
    if not text or not field:
        return False
    return re.search(r"(?<![\w.])" + re.escape(field) + r"(?![\w])", text) is not None


def classify_severity(kind):
    """Rank a reference kind by how likely it is to BREAK silently on a change.
    stored/related computes rot silently → high; views error or no-op →
    medium; saved filters/actions → medium; plain metadata → low."""
    high = {"stored_compute_depends", "related_field"}
    medium = {"view", "record_rule", "ir_filter", "server_action", "automation"}
    if kind in high:
        return "high"
    if kind in medium:
        return "medium"
    return "low"


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    FIELD = os.environ.get("FIELD")
    if not MODEL or not FIELD:
        raise SystemExit("Set MODEL and FIELD, e.g. MODEL=sale.order FIELD=commitment_date")

    refs = []

    def add(kind, **data):
        refs.append({"kind": kind, "severity": classify_severity(kind), **data})

    def safe(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            WARNINGS.append(f"{label} failed ({type(e).__name__}: {e})")
            return None

    model = env[MODEL]                       # noqa: F821
    target = model._fields.get(FIELD)
    if target is None:
        WARNINGS.append(f"{FIELD} is not a current field of {MODEL} "
                        "(already renamed/removed?); scanning references anyway")

    # 1. Other fields (any model) whose depends/related reach this field. ------
    def scan_fields():
        hits = []
        for mname in env.registry.models:       # noqa: F821
            try:
                m = env[mname]                   # noqa: F821
            except Exception:
                continue
            for fname, f in m._fields.items():
                if mname == MODEL and fname == FIELD:
                    continue
                dep = list(getattr(f, "depends", None) or [])
                rel = list(getattr(f, "related", None) or [])
                # related on the SAME model targeting this field, or a depends
                # path whose last hop is this field, is a real downstream link.
                if depends_hit(dep, FIELD) or (mname == MODEL and rel and rel[-1] == FIELD):
                    hits.append({
                        "model": mname, "field": fname,
                        "stored": bool(getattr(f, "store", False)),
                        "compute": getattr(f, "compute", None),
                        "related": ".".join(rel) if rel else None,
                        "depends": dep or None,
                    })
        return hits
    for h in safe("field scan", scan_fields) or []:
        kind = "related_field" if h["related"] else "stored_compute_depends"
        add(kind, **h)

    # 2. Views that render the field (scoped to this model's views). -----------
    def scan_views():
        views = env["ir.ui.view"].sudo().search([("model", "=", MODEL)])  # noqa: F821
        out = []
        for v in views:
            arch = v.arch_db or ""
            if mentions_field(arch, FIELD):
                out.append({"id": v.id, "xml_id": v.xml_id, "name": v.name,
                            "type": v.type, "inherit_id": v.inherit_id.xml_id or None})
        return out
    for v in safe("view scan", scan_views) or []:
        add("view", **v)

    # 3. Record rules whose domain_force mentions the field. -------------------
    def scan_rules():
        rules = env["ir.rule"].sudo().search([("model_id.model", "=", MODEL)])  # noqa: F821
        return [{"id": r.id, "name": r.name, "global": r.get("global") if hasattr(r, "get") else r["global"],
                 "domain_force": r.domain_force}
                for r in rules if mentions_field(r.domain_force or "", FIELD)]
    for r in safe("rule scan", scan_rules) or []:
        add("record_rule", **r)

    # 4. Saved filters (ir.filters) on this model. -----------------------------
    def scan_filters():
        flt = env["ir.filters"].sudo().search([("model_id", "=", MODEL)])  # noqa: F821
        return [{"id": f.id, "name": f.name, "domain": f.domain, "context": f.context}
                for f in flt
                if mentions_field(f.domain or "", FIELD) or mentions_field(f.context or "", FIELD)]
    for f in safe("filter scan", scan_filters) or []:
        add("ir_filter", **f)

    # 5. Server actions + automations referencing the field. -------------------
    def scan_server_actions():
        sa = env["ir.actions.server"].sudo().search([("model_id.model", "=", MODEL)])  # noqa: F821
        return [{"id": a.id, "name": a.name, "state": a.state}
                for a in sa if mentions_field(getattr(a, "code", "") or "", FIELD)]
    for a in safe("server action scan", scan_server_actions) or []:
        add("server_action", **a)

    def scan_automations():
        try:
            au = env["base.automation"].sudo().search([("model_id.model", "=", MODEL)])  # noqa: F821
        except Exception:
            return []
        return [{"id": a.id, "name": a.name, "trigger": a.trigger}
                for a in au if mentions_field(a.filter_domain or "", FIELD)]
    for a in safe("automation scan", scan_automations) or []:
        add("automation", **a)

    # 6. Which modules define/extend the field (rename touches all of them). ---
    modules = safe("field modules lookup", lambda: (
        env["ir.model.fields"].sudo().search(                          # noqa: F821
            [("model", "=", MODEL), ("name", "=", FIELD)]).modules or None))

    by_sev = {"high": 0, "medium": 0, "low": 0}
    for r in refs:
        by_sev[r["severity"]] += 1

    out = {
        "model": MODEL,
        "field": FIELD,
        "field_exists": target is not None,
        "defining_modules": modules,
        "reference_count": len(refs),
        "severity_counts": by_sev,
        "references": refs,
        "_warnings": WARNINGS,
        "_caveat": "Text scans (views/domains/code) are whole-identifier heuristics "
                   "scoped to this model where possible; a same-named field elsewhere "
                   "in a shared blob can false-positive. Confirm high-severity hits, "
                   "and write the migration (odoo-migration) to cover every dependent.",
    }
    payload = json.dumps(out, indent=2, default=str)
    print("===ODOO_REFS_START===")
    print(payload)
    print("===ODOO_REFS_END===")


if "env" in globals():
    run()
