"""
Risk-based scenario test generator (Layer H diagnostic) — run INSIDE `odoo-bin shell`.

Given an Odoo model + the methods being changed, this classifies the change risk
and emits (a) the mandatory test scenarios and (b) a runnable TransactionCase
skeleton covering them — so the agent always writes the right guards before
touching financial, inventory, or payroll logic.

Risk tiers drive which scenario keys are required:
  critical  — accounting journals, payments, payroll, POS, stock valuations
  high      — sales/purchase orders, pickings, manufacturing, journal items
  normal    — everything else

The pure helpers (classify_model_risk, required_scenarios, render_test_skeleton,
build_report) need no Odoo and are unit-tested; run() executes only inside the
shell (gated on `env` in globals).

Usage
-----
    MODEL=account.move METHODS=action_post,write \\
        odoo-bin shell -d <DB> --no-http < scenario_gen.py

    # Or via the CLI wrapper:
    odoo-ai scenarios account.move --methods action_post,write

Output: pure JSON wrapped in ===ODOO_SCEN_START=== / ===ODOO_SCEN_END===.
"""
import os
import re
import json

WARNINGS = []

# --- Risk classification constants -------------------------------------------
# High is checked BEFORE critical so account.move.line (high exact) is not
# swallowed by the account.move critical-prefix startswith rule.
_CRITICAL_PREFIXES = (
    "account.move", "account.payment", "account.bank.statement",
    "stock.valuation", "stock.quant", "hr.payslip",
    "payment.transaction", "pos.order",
)
_HIGH_EXACT = frozenset({
    "sale.order", "purchase.order", "stock.picking",
    "mrp.production", "account.move.line",
})

# --- Scenario catalogue — key → human-readable rationale --------------------
_SCENARIO_META = {
    "non_admin": (
        "Verify behaviour with a non-admin user — admin masks ACL and record-rule "
        "gaps that non-admin users will hit in production."
    ),
    "at_install_vs_post_install": (
        "Run once at_install (module loading) and once post_install (all modules "
        "loaded) to catch dependency-order breakage introduced by the change."
    ),
    "multi_company": (
        "Confirm that company_id scoping prevents cross-company data leaks and "
        "that record rules fire correctly per active company."
    ),
    "batch": (
        "Call the method on a multi-record recordset; ORM helpers like "
        "mapped/filtered must not silently drop records when applied in batch."
    ),
    "upgrade_i_and_u": (
        "Re-run after module reinstall (-i) and upgrade (-u) to catch "
        "data-migration regressions introduced by the change."
    ),
    "locked_period": (
        "Attempt the operation on a record whose accounting period is locked "
        "(lock_date_account or hash-secured journal); must raise UserError."
    ),
    "record_rules": (
        "Assert that ir.rule company-domain filters apply: a user from company B "
        "must not read or mutate records owned by company A."
    ),
}


# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
def classify_model_risk(model_name):
    """Classify a model into a risk tier; returns {"tier": str, "reasons": [str]}.

    Tier is 'critical', 'high', or 'normal'. High is evaluated first so that
    account.move.line (explicitly high) is not promoted to critical by the
    account.move prefix rule.
    """
    name = (model_name or "").strip()

    if name in _HIGH_EXACT:
        return {
            "tier": "high",
            "reasons": [f"{name!r} is a high-risk transactional model"],
        }

    for prefix in _CRITICAL_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return {
                "tier": "critical",
                "reasons": [
                    f"{name!r} matches critical prefix {prefix!r} "
                    "(financial / payroll / valuation data at stake)"
                ],
            }

    return {
        "tier": "normal",
        "reasons": [f"{name!r} has no elevated-risk classification"],
    }


def required_scenarios(model_name, methods, has_company_id=True, is_transient=False):
    """Derive the mandatory test scenario list for a model + changed-method set.

    Returns [{"key": str, "why": str}] ordered most-universal first.
    """
    risk = classify_model_risk(model_name)
    tier = risk["tier"]
    methods = list(methods or [])

    scenarios = []

    def _add(key):
        scenarios.append({"key": key, "why": _SCENARIO_META[key]})

    # Always required
    _add("non_admin")
    _add("at_install_vs_post_install")

    if has_company_id:
        _add("multi_company")

    # Batch: create / write / unlink OR any action_* method
    if any(m in {"create", "write", "unlink"} or m.startswith("action_") for m in methods):
        _add("batch")

    if not is_transient:
        _add("upgrade_i_and_u")

    # Locked-period guard: only for critical accounting models
    if tier == "critical" and (model_name or "").startswith("account."):
        _add("locked_period")

    if has_company_id:
        _add("record_rules")

    return scenarios


def _model_to_class_name(model_name):
    """Convert 'sale.order' → 'TestSaleOrderScenarios'."""
    parts = re.split(r"[._\-]+", model_name or "model")
    return "Test" + "".join(p.capitalize() for p in parts if p) + "Scenarios"


def render_test_skeleton(model_name, methods, scenarios):
    """Render a ready-to-run odoo.tests.TransactionCase skeleton string.

    Each scenario becomes a test_<key> stub with the rationale as its docstring
    and a self.fail("TODO: ...") so the stub is immediately red in CI until the
    developer fills it in.
    """
    class_name = _model_to_class_name(model_name)
    method_repr = ", ".join(repr(m) for m in (methods or []))
    why_of = {sc["key"]: sc["why"] for sc in scenarios}
    has_at_install = "at_install_vs_post_install" in why_of

    lines = [
        "# Generated by scenario_gen.py — fill in the TODOs before merging.",
        "from odoo.tests.common import TransactionCase, tagged",
        "",
        "",
        "@tagged('post_install', '-at_install')",
        f"class {class_name}(TransactionCase):",
        f'    """Post-install scenario tests for {model_name} — methods: [{method_repr}]."""',
        "",
        "    @classmethod",
        "    def setUpClass(cls):",
        "        super().setUpClass()",
        "        # Non-admin user for ACL / record-rule testing.",
        "        cls.non_admin = cls.env['res.users'].create({",
        "            'name': 'Test Non-Admin',",
        "            'login': 'test_non_admin_scen',",
        "            'groups_id': [(6, 0, [cls.env.ref('base.group_user').id])],",
        "        })",
        "",
    ]

    for sc in scenarios:
        key = sc["key"]
        lines += [
            f"    def test_{key}(self):",
            f'        """{sc["why"]}"""',
            f'        self.fail("TODO: implement test_{key} for {model_name}")',
            "",
        ]

    # A post_install-only class would CONTRADICT a required at_install_vs_post_install
    # scenario — so emit a second, at_install-tagged class to cover the other phase.
    if has_at_install:
        lines += [
            "",
            "@tagged('at_install', '-post_install')",
            f"class {class_name}AtInstall(TransactionCase):",
            f'    """At-install half for {model_name}: verify behaviour BEFORE later',
            '    modules patch it (the post_install class above checks the fully-',
            '    composed registry)."""',
            "",
            "    def test_at_install_behaviour(self):",
            f'        """{why_of["at_install_vs_post_install"]}"""',
            f'        self.fail("TODO: at-install assertions for {model_name}")',
            "",
        ]

    return "\n".join(lines)


def build_report(model_name, methods, has_company_id=True, is_transient=False):
    """Assemble the full scenario report dict.

    Returns {"model", "methods", "risk", "scenarios", "skeleton", "_caveat"}.
    """
    risk = classify_model_risk(model_name)
    scenarios = required_scenarios(
        model_name, methods,
        has_company_id=has_company_id,
        is_transient=is_transient,
    )
    skeleton = render_test_skeleton(model_name, methods, scenarios)
    return {
        "model": model_name,
        "methods": list(methods or []),
        "risk": risk,
        "scenarios": scenarios,
        "skeleton": skeleton,
        "_caveat": (
            "Scenarios are a floor, not a ceiling. Stubs fail by design (self.fail) "
            "until each TODO is implemented. Risk tier is inferred from the model name "
            "alone — confirm with `odoo-ai security <model>` for the effective ACL / "
            "record-rule picture before shipping."
        ),
    }


# --- Env-dependent work (runs only inside odoo-bin shell) --------------------
def run():
    MODEL = os.environ.get("MODEL")
    if not MODEL:
        raise SystemExit('Set MODEL, e.g. MODEL=sale.order')
    methods_raw = os.environ.get("METHODS") or ""
    methods = [m.strip() for m in methods_raw.split(",") if m.strip()] or ["create", "write"]

    has_company_id = True
    is_transient = False
    try:
        has_company_id = "company_id" in env[MODEL]._fields   # noqa: F821
        is_transient = bool(env[MODEL]._transient)             # noqa: F821
    except Exception as exc:  # noqa: BLE001
        WARNINGS.append(
            f"env introspection failed ({type(exc).__name__}: {exc}); "
            "assuming has_company_id=True, is_transient=False"
        )

    report = build_report(MODEL, methods, has_company_id=has_company_id, is_transient=is_transient)
    report["_warnings"] = WARNINGS
    payload = json.dumps(report, indent=2, default=str)
    print("===ODOO_SCEN_START===")
    print(payload)
    print("===ODOO_SCEN_END===")


if "env" in globals():
    run()
