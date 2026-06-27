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

    # graph-resolve dotted depends/related through comodel_name (fewer
    # false positives than the last-segment text heuristic):
    MODEL=sale.order FIELD=commitment_date RESOLVE_PATHS=1 \
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


def resolve_dotted_path(start_model, dotted, comodel_of):
    """Walk a dotted depends/related path through the relation graph.

    The text heuristic in `depends_hit` matches the LAST segment of a dotted
    path regardless of which model it actually lands on, so `order_id.date` and
    `partner_id.date` both "hit" FIELD=date even though they reach different
    models. This resolves the path for real: starting at `start_model`, each
    non-terminal segment must be a relational field whose `comodel_name` becomes
    the next model; the final segment is the terminal field on the model reached.

    `comodel_of(model, field) -> comodel_name | None` returns the target model
    for a relational field, or None for non-relational / unknown fields.

    Returns {"terminal_model", "terminal_field", "resolved", "reason"}.
    `resolved` is False when a non-terminal hop can't be traversed (the field is
    unknown or not relational) — the caller then can't confirm the terminal.
    """
    parts = [p for p in (dotted or "").split(".") if p]
    if not parts:
        return {"terminal_model": None, "terminal_field": None,
                "resolved": False, "reason": "empty path"}
    model = start_model
    for i, seg in enumerate(parts):
        if i == len(parts) - 1:
            return {"terminal_model": model, "terminal_field": seg,
                    "resolved": True, "reason": "ok"}
        comodel = comodel_of(model, seg)
        if not comodel:
            return {"terminal_model": model, "terminal_field": seg, "resolved": False,
                    "reason": f"cannot traverse {model}.{seg} (non-relational or unknown)"}
        model = comodel
    return {"terminal_model": model, "terminal_field": None,
            "resolved": False, "reason": "no terminal segment"}


def path_hits_target(start_model, paths, target_model, target_field, comodel_of):
    """First path in `paths` that graph-resolves to (target_model, target_field).

    Returns the match detail dict (path + terminal) or None. Used to replace the
    last-segment heuristic when graph resolution is enabled.
    """
    for p in paths or []:
        info = resolve_dotted_path(start_model, p, comodel_of)
        if (info["resolved"] and info["terminal_model"] == target_model
                and info["terminal_field"] == target_field):
            return {"path": p, "terminal_model": info["terminal_model"],
                    "terminal_field": info["terminal_field"]}
    return None


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    FIELD = os.environ.get("FIELD")
    if not MODEL or not FIELD:
        raise SystemExit("Set MODEL and FIELD, e.g. MODEL=sale.order FIELD=commitment_date")
    RESOLVE_PATHS = os.environ.get("RESOLVE_PATHS") in ("1", "true", "yes")

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

    def comodel_of(model_name, fname):
        """Relation-graph hop for path resolution: comodel of a relational field."""
        try:
            f = env[model_name]._fields.get(fname)              # noqa: F821
        except Exception:
            return None
        return getattr(f, "comodel_name", None) if f is not None else None

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
                resolved_via = None
                if RESOLVE_PATHS:
                    # Graph-resolve each depends path (relative to the compute's
                    # OWN model) and the related path, confirming they actually
                    # land on MODEL.FIELD — not just share its last segment.
                    match = path_hits_target(mname, dep, MODEL, FIELD, comodel_of)
                    if match:
                        resolved_via = {"via": "depends", **match}
                    elif rel:
                        rmatch = path_hits_target(mname, [".".join(rel)], MODEL, FIELD, comodel_of)
                        if rmatch:
                            resolved_via = {"via": "related", **rmatch}
                    matched = resolved_via is not None
                else:
                    # Text heuristic: last-segment match (may false-positive).
                    matched = depends_hit(dep, FIELD) or (mname == MODEL and rel and rel[-1] == FIELD)
                if not matched:
                    continue
                hit = {
                    "model": mname, "field": fname,
                    "stored": bool(getattr(f, "store", False)),
                    "compute": getattr(f, "compute", None),
                    "related": ".".join(rel) if rel else None,
                    "depends": dep or None,
                }
                if resolved_via:
                    hit["resolved_via"] = resolved_via
                hits.append(hit)
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
        "path_resolution": "graph-resolved" if RESOLVE_PATHS else "text-heuristic",
        "defining_modules": modules,
        "reference_count": len(refs),
        "severity_counts": by_sev,
        "references": refs,
        "_warnings": WARNINGS,
        "_caveat": (
            "Field depends/related links are graph-resolved through comodel_name, "
            "so dotted paths are confirmed to land on this exact model.field (few "
            "false positives). View/domain/code text scans remain whole-identifier "
            "heuristics scoped to this model. Confirm high-severity hits, and write "
            "the migration (odoo-migration) to cover every dependent."
            if RESOLVE_PATHS else
            "Text scans (views/domains/code) AND field depends/related are whole-"
            "identifier / last-segment heuristics scoped to this model where possible; "
            "a same-named field elsewhere can false-positive. Re-run with "
            "--resolve-paths (RESOLVE_PATHS=1) to graph-resolve depends/related "
            "through comodel_name. Confirm high-severity hits, and write the "
            "migration (odoo-migration) to cover every dependent."),
    }
    payload = json.dumps(out, indent=2, default=str)
    print("===ODOO_REFS_START===")
    print(payload)
    print("===ODOO_REFS_END===")


if "env" in globals():
    run()
