"""
Odoo effective-security simulator (Layer G) — run INSIDE `odoo-bin shell`.

model_brief (Layer A) dumps the RAW ACL rows and record rules. This answers the
question that actually matters before you touch security: "for THIS user (in
THIS company), what can they really DO to this model, and which rows can they
SEE?" — by combining ACLs (additive across the user's groups) and record rules
(global rules ANDed, group rules ORed) the way Odoo does at runtime, plus the
group-restricted fields that vanish from their view.

For the record-rule domain it uses Odoo's OWN machinery (`ir.rule._compute_domain`
under `with_user`), so the effective domain matches runtime. ACL effect is
computed additively from the `ir.model.access` rows that apply to the user (a
stable, version-independent rule), and cross-checked against `check_access` /
`check_access_rights` where available.

⚠️  This simulates the ACTING USER's access. It does NOT model `sudo()`, which
bypasses BOTH ACL and record rules — any code path that calls sudo() ignores
everything here. And the SUPERUSER (id 1) bypasses both entirely, so simulating
as the superuser is not representative (flagged in `_warnings`).

The env-dependent work is in run(); the pure helpers (effective_acl,
field_visible, parse_field_groups) are module-level so they import without Odoo.

Usage
-----
    MODEL=sale.order AS_USER=demo odoo-bin shell -d <DB> --no-http < security_sim.py
    MODEL=sale.order AS_USER=7 AS_COMPANY=2 odoo-bin shell -d <DB> < security_sim.py

NOTE: pass the user via AS_USER (login or numeric id) — NOT USER, which the
shell already sets to the OS account. AS_COMPANY is an id or company name; it
defaults to the user's company.

Output: pure JSON wrapped in ===ODOO_SECURITY_START=== / ===ODOO_SECURITY_END===.
"""
import os
import json

WARNINGS = []
MODES = ("read", "write", "create", "unlink")


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def effective_acl(acl_rows):
    """Additive ACL: a mode is granted if ANY applicable row grants it.

    `acl_rows` is the list of `ir.model.access` rows that apply to the user
    (their groups' rows + global rows where group_id is empty), each a dict with
    perm_read/perm_write/perm_create/perm_unlink. Empty list → all False (Odoo
    denies a non-superuser when no ACL row grants the model).
    """
    eff = {m: False for m in MODES}
    for r in acl_rows or []:
        eff["read"] = eff["read"] or bool(r.get("perm_read"))
        eff["write"] = eff["write"] or bool(r.get("perm_write"))
        eff["create"] = eff["create"] or bool(r.get("perm_create"))
        eff["unlink"] = eff["unlink"] or bool(r.get("perm_unlink"))
    return eff


def parse_field_groups(group_spec):
    """Split a field `groups=` spec into positive / negative group xmlids.

    The spec is a comma-separated list of group XML ids; a leading '!' negates
    (the field is hidden FROM members of that group). Returns
    {"positive": [...], "negative": [...]}.
    """
    pos, neg = [], []
    for g in (group_spec or "").split(","):
        g = g.strip()
        if not g:
            continue
        (neg if g.startswith("!") else pos).append(g.lstrip("!"))
    return {"positive": pos, "negative": neg}


def field_visible(group_spec, user_group_xmlids):
    """Odoo field-level `groups=` visibility for a user.

    Visible iff the user is in at least one positive group (or none are listed)
    AND in none of the negated groups. Empty/None spec → always visible.
    """
    if not group_spec:
        return True
    parsed = parse_field_groups(group_spec)
    ug = set(user_group_xmlids or [])
    if any(g in ug for g in parsed["negative"]):
        return False
    if parsed["positive"] and not any(g in ug for g in parsed["positive"]):
        return False
    return True


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    if not MODEL:
        raise SystemExit("Set MODEL, e.g. MODEL=sale.order")
    AS_USER = os.environ.get("AS_USER")
    AS_COMPANY = os.environ.get("AS_COMPANY")

    Users = env["res.users"]                       # noqa: F821
    if AS_USER:
        if AS_USER.isdigit():
            user = Users.browse(int(AS_USER)).exists()
        else:
            user = Users.search([("login", "=", AS_USER)], limit=1)
        if not user:
            raise SystemExit(f"user {AS_USER!r} not found (login or id)")
    else:
        user = env.user                            # noqa: F821
    uid = user.id

    company = None
    if AS_COMPANY:
        Comp = env["res.company"]                  # noqa: F821
        company = (Comp.browse(int(AS_COMPANY)).exists() if AS_COMPANY.isdigit()
                   else Comp.search([("name", "=", AS_COMPANY)], limit=1))
        if not company:
            WARNINGS.append(f"company {AS_COMPANY!r} not found; using the user's default company")
            company = None

    is_superuser = uid == 1   # odoo.SUPERUSER_ID
    if is_superuser:
        WARNINGS.append("acting user is the SUPERUSER (id 1) — ACL and record rules are "
                        "BYPASSED at runtime; this simulation is NOT representative. "
                        "Pass AS_USER=<a real login/id>.")

    # The simulated recordset: bound to the target user (and company).
    model_as = env[MODEL].with_user(user)          # noqa: F821
    if company:
        model_as = model_as.with_company(company)

    user_group_ids = set(user.groups_id.ids)
    # Map the user's groups to xmlids for field-visibility explanation.
    user_group_xmlids = set()
    try:
        for gid, xmlid in user.groups_id.get_external_id().items():
            if xmlid:
                user_group_xmlids.add(xmlid)
    except Exception as e:
        WARNINGS.append(f"group xmlid lookup failed ({type(e).__name__}: {e})")

    # --- 1. ACL: additive across the user's applicable rows ------------------
    acl_recs = env["ir.model.access"].sudo().search(           # noqa: F821
        [("model_id.model", "=", MODEL)])
    if not acl_recs:
        WARNINGS.append(f"{MODEL} has NO ir.model.access entries; only the superuser "
                        "can access it (every mode denied for normal users)")
    applicable_rows, contributing = [], []
    for a in acl_recs:
        is_global = not a.group_id
        if is_global or a.group_id.id in user_group_ids:
            row = {"perm_read": a.perm_read, "perm_write": a.perm_write,
                   "perm_create": a.perm_create, "perm_unlink": a.perm_unlink}
            applicable_rows.append(row)
            contributing.append({
                "name": a.name,
                "group": a.group_id.display_name if a.group_id else None,  # None = global (all users)
                **row,
            })
    eff = effective_acl(applicable_rows)

    # Cross-check against Odoo's own check (version-tolerant; advisory only).
    def _odoo_check(mode):
        try:
            if hasattr(model_as, "check_access"):        # Odoo 18/19
                model_as.check_access(mode)
                return True
            return bool(model_as.check_access_rights(mode, raise_exception=False))  # ≤17
        except Exception:
            return False

    cross = {m: _odoo_check(m) for m in MODES}
    mismatches = [m for m in MODES if cross[m] != eff[m]]
    if mismatches and not is_superuser:
        WARNINGS.append(f"ACL additive result disagrees with Odoo's own check for "
                        f"{mismatches} — likely an implied-group or model-inheritance "
                        f"nuance; trust the 'odoo_check' values for those modes")

    access_rights = {
        **eff,
        "_source": "additive over applicable ir.model.access rows",
        "odoo_check": cross,
        "contributing_acl": contributing,
    }

    # --- 2. Record rules: effective domain via Odoo's own combiner -----------
    # Bind the rule engine to the SAME user AND company as the simulated
    # recordset; otherwise _compute_domain() resolves company-dependent rules
    # (user.company_id / allowed_company_ids in the domain_force) against the
    # user's default company, and the effective domain would silently diverge
    # from what AS_COMPANY actually sees at runtime.
    Rule = env["ir.rule"].with_user(user)          # noqa: F821
    if company:
        Rule = Rule.with_company(company).with_context(
            allowed_company_ids=[company.id])
    rule_recs = env["ir.rule"].sudo().search(      # noqa: F821
        [("model_id.model", "=", MODEL), ("active", "=", True)])

    def rule_applies(r):
        if r["global"]:           # 'global' is a Python keyword → item access
            return True
        return bool(set(r.groups.ids) & user_group_ids)

    def describe_rules(mode):
        perm = f"perm_{mode}"
        glob, grp = [], []
        for r in rule_recs:
            if not r[perm]:
                continue
            if not rule_applies(r):
                continue
            entry = {"id": r.id, "name": r.name, "domain_force": r.domain_force,
                     "groups": r.groups.mapped("display_name") or None}
            (glob if r["global"] else grp).append(entry)
        try:
            effective_domain = Rule._compute_domain(MODEL, mode)
        except Exception as e:
            effective_domain = {"_error": f"{type(e).__name__}: {e}"}
            WARNINGS.append(f"_compute_domain({mode}) failed ({type(e).__name__}: {e})")
        return {
            "effective_domain": effective_domain,   # None = no row restriction (all rows)
            "global_rules": glob,                    # ANDed (every one must pass)
            "group_rules": grp,                      # ORed among themselves, then ANDed with globals
        }

    record_rules = {m: describe_rules(m) for m in MODES}

    # --- 3. Group-restricted fields (vanish from the user's view) ------------
    field_access = {"restricted": [], "_note": "fields hidden by `groups=` for this user "
                    "(absent from their fields_get); sudo() ignores this too"}
    try:
        su_fields = set(env[MODEL].fields_get())               # noqa: F821
        user_fields = set(model_as.fields_get())
        for f in sorted(su_fields - user_fields):
            spec = getattr(env[MODEL]._fields.get(f), "groups", None)  # noqa: F821
            field_access["restricted"].append({"field": f, "groups": spec})
    except Exception as e:
        WARNINGS.append(f"field access diff failed ({type(e).__name__}: {e})")

    # --- 4. Multi-company posture -------------------------------------------
    acting_company = company or user.company_id
    multi_company = {
        "acting_company": {"id": acting_company.id, "name": acting_company.name} if acting_company else None,
        "user_allowed_companies": [{"id": c.id, "name": c.name} for c in user.company_ids],
        "model_has_company_id": "company_id" in env[MODEL]._fields,   # noqa: F821
    }

    out = {
        "model": MODEL,
        "user": {"id": uid, "login": user.login, "name": user.name,
                 "is_superuser": is_superuser, "groups_count": len(user_group_ids)},
        "company": multi_company,
        "access_rights": access_rights,
        "record_rules": record_rules,
        "field_access": field_access,
        "_warnings": WARNINGS,
        "_caveat": "Simulates the ACTING USER. sudo() bypasses BOTH ACL and record rules — "
                   "grep source (Layer A SOURCE=1) for sudo() on the paths you care about. "
                   "Record-rule effective_domain comes from Odoo's own ir.rule._compute_domain; "
                   "ACL effect is additive over applicable rows (see odoo_check for Odoo's own verdict).",
    }
    payload = json.dumps(out, indent=2, default=str)
    print("===ODOO_SECURITY_START===")
    print(payload)
    print("===ODOO_SECURITY_END===")


# `env` is injected by `odoo-bin shell`; absent (e.g. a unit-test import) → run()
# is skipped and only the pure helpers above are exposed.
if "env" in globals():
    run()
