"""
Odoo business-fixture factory — run INSIDE `odoo-bin shell` (EXEC mode) or import for code generation.

Agents write bad Odoo tests because realistic business records are hard to build:
a confirmed sale order needs a customer AND a stockable product AND a valid order
line; a posted invoice needs a chart of accounts; a manufacturing order needs a
BoM whose finished good points at the right template. Guessing these values is
exactly where hallucinations creep in. This module ships a small registry of
VALID, minimal business-object recipes and hands them over two ways:

  1. CODE mode (default, pure) — emit a ready-to-paste `TransactionCase` skeleton
     that builds the fixture (setUpClass creates each step, resolving
     back-references), so the agent starts from a known-good shape instead of
     inventing one.
  2. EXEC mode (EXEC=1, inside odoo-bin shell) — actually execute the recipe
     inside a SAVEPOINT and ROLL BACK (unless COMMIT=1, discouraged + warned),
     returning the created ids. This VALIDATES the recipe against THIS instance
     (its installed modules, its chart of accounts) rather than trusting the
     literal values.

The registry and every code-generation helper are pure module-level data/functions
(importable + unit-testable without Odoo); only the SAVEPOINT execution in run()
touches `env`, and run() fires only when `odoo-bin shell` injects `env`.

Usage
-----
    # list the available recipes (no DB work):
    FACTORY=list odoo-bin shell -d <DB> --no-http < fixture_factory.py

    # emit a paste-ready test skeleton for one recipe (CODE mode, default):
    FACTORY=sale_order_stockable odoo-bin shell -d <DB> --no-http < fixture_factory.py

    # EXECUTE the recipe against THIS instance in a rolled-back savepoint:
    FACTORY=sale_order_stockable EXEC=1 odoo-bin shell -d <DB> --no-http < fixture_factory.py

    # persist instead of rolling back (dev/throwaway DB only — warned):
    FACTORY=customer_basic EXEC=1 COMMIT=1 ...

    # write the JSON to a file instead of between sentinels:
    FACTORY=invoice_posted EXEC=1 OUT=/tmp/fixture.json ...

Output: pure JSON wrapped in ===ODOO_FIXTURE_START=== / ===ODOO_FIXTURE_END===.
"""
import os
import json


# --- Recipe registry (pure data — no Odoo needed) ----------------------------
# Each step is either:
#   create:  {"ref": <name>, "model": <model>, "values": {...}}
#   method:  {"ref": <name>, "model": <model>, "method": <name>, "on": "@<ref>"}
# Values may carry back-references:
#   "@partner"      -> the record created under ref "partner"
#   "@partner.id"   -> its id (use .id for many2one values; valid on 17/18/19)
#   "@tmpl.product_variant_id.id" -> multi-level attribute traversal
# An optional step-level "code_comment" is emitted as a `# ...` line above the
# generated statement (used to flag 17/18/19 field deltas).
RECIPES = [
    {
        "id": "customer_basic",
        "title": "Company customer + contact person",
        "requires_modules": [],
        "description": "A res.partner company with a child contact — the base of almost every business flow.",
        "steps": [
            {"ref": "company", "model": "res.partner", "values": {
                "name": "Fixture Customer Co",
                "is_company": True,
                "email": "billing@fixture-customer.example",
                "customer_rank": 1,
            }},
            {"ref": "contact", "model": "res.partner", "values": {
                "name": "Fixture Buyer",
                "parent_id": "@company.id",
                "function": "Purchasing",
                "email": "buyer@fixture-customer.example",
            }},
        ],
        "notes": [
            "is_company=True marks the company; the child (parent_id) is the contact person.",
            "customer_rank>0 flags it a customer without needing the sale module installed.",
            "The company is the commercial_partner_id used for invoicing.",
        ],
    },
    {
        "id": "product_stockable",
        "title": "Stockable (storable) product",
        "requires_modules": ["stock"],
        "description": "A product.product of the storable type — the thing deliveries, receipts and MOs move.",
        "steps": [
            {"ref": "product", "model": "product.product",
             "code_comment": "Odoo 17: type='product' (storable). Odoo 18/19: 'product' removed — use type='consu' + is_storable=True.",
             "values": {
                "name": "Fixture Stockable Product",
                "type": "product",
                "list_price": 100.0,
                "standard_price": 60.0,
            }},
        ],
        "notes": [
            "Created as product.product (the variant) so its .id drops straight into sale/purchase lines.",
            "17 vs 18/19: the storable flag moved — v17 type='product'; v18/19 use type='consu' with is_storable=True (detailed_type/'product' was dropped). The codegen emits a version comment.",
            "uom_id / uom_po_id default to Units (uom.product_uom_unit) when omitted.",
            "standard_price is the cost; list_price the sales price.",
        ],
    },
    {
        "id": "sale_order_stockable",
        "title": "Confirmed sale order for a stockable product",
        "requires_modules": ["sale_management", "stock"],
        "description": "Customer + stockable product + sale.order with one line, confirmed (creates a delivery).",
        "steps": [
            {"ref": "customer", "model": "res.partner", "values": {
                "name": "Fixture SO Customer", "is_company": True,
            }},
            {"ref": "product", "model": "product.product",
             "code_comment": "Odoo 17: type='product'. Odoo 18/19: type='consu' + is_storable=True.",
             "values": {"name": "Fixture SO Product", "type": "product", "list_price": 250.0}},
            {"ref": "so", "model": "sale.order", "values": {
                "partner_id": "@customer.id",
                "order_line": [(0, 0, {"product_id": "@product.id", "product_uom_qty": 3.0})],
            }},
            {"ref": "so_confirmed", "model": "sale.order", "method": "action_confirm", "on": "@so"},
        ],
        "notes": [
            "sale.order needs partner_id + at least one order_line built with the (0, 0, {...}) command.",
            "order_line name / price_unit / product_uom compute from product_id on create — omit them.",
            "action_confirm moves draft->sale and (with stock) creates a delivery picking.",
            "product_id is a product.product (variant), never a product.template.",
        ],
    },
    {
        "id": "sale_order_service",
        "title": "Confirmed sale order for a service product",
        "requires_modules": ["sale_management"],
        "description": "A service-product sale order — no stock module needed, no delivery created.",
        "steps": [
            {"ref": "customer", "model": "res.partner", "values": {
                "name": "Fixture Service Customer", "is_company": True,
            }},
            {"ref": "product", "model": "product.product", "values": {
                "name": "Fixture Service", "type": "service", "list_price": 500.0,
            }},
            {"ref": "so", "model": "sale.order", "values": {
                "partner_id": "@customer.id",
                "order_line": [(0, 0, {"product_id": "@product.id", "product_uom_qty": 1.0})],
            }},
            {"ref": "so_confirmed", "model": "sale.order", "method": "action_confirm", "on": "@so"},
        ],
        "notes": [
            "type='service' is unchanged across 17/18/19 — the simplest cross-version SO fixture.",
            "sale_management alone suffices; no stock picking is created on confirm for services.",
        ],
    },
    {
        "id": "purchase_to_receipt",
        "title": "Confirmed purchase order (incoming receipt)",
        "requires_modules": ["purchase", "stock"],
        "description": "Vendor + stockable product + purchase.order, confirmed — creates an incoming picking.",
        "steps": [
            {"ref": "vendor", "model": "res.partner", "values": {
                "name": "Fixture Vendor", "is_company": True, "supplier_rank": 1,
            }},
            {"ref": "product", "model": "product.product",
             "code_comment": "Odoo 17: type='product'. Odoo 18/19: type='consu' + is_storable=True.",
             "values": {"name": "Fixture PO Product", "type": "product", "standard_price": 30.0}},
            {"ref": "po", "model": "purchase.order", "values": {
                "partner_id": "@vendor.id",
                "order_line": [(0, 0, {"product_id": "@product.id", "product_qty": 5.0})],
            }},
            {"ref": "po_confirmed", "model": "purchase.order", "method": "button_confirm", "on": "@po"},
        ],
        "notes": [
            "purchase.order.line name / price_unit / product_uom / date_planned compute from product_id.",
            "button_confirm sets state='purchase' and creates the incoming stock.picking.",
            "supplier_rank>0 marks the partner a vendor; validating the receipt is a further step.",
        ],
    },
    {
        "id": "delivery_with_lot",
        "title": "Lot-tracked product + a stock lot",
        "requires_modules": ["stock"],
        "description": "A tracking='lot' product and a stock.lot for it — the building block of a lot-traced delivery.",
        "steps": [
            {"ref": "product", "model": "product.product",
             "code_comment": "Odoo 17: type='product'. Odoo 18/19: type='consu' + is_storable=True.",
             "values": {"name": "Fixture Lot Product", "type": "product", "tracking": "lot"}},
            {"ref": "lot", "model": "stock.lot", "values": {
                "name": "LOT-FIXTURE-0001",
                "product_id": "@product.id",
            }},
        ],
        "notes": [
            "tracking='lot' forces every move of this product to carry a stock.lot.",
            "stock.lot.company_id defaults to env.company; name is the lot/serial number.",
            "The full delivery (stock.picking + move + move_line assigning the lot + button_validate) "
            "is intentionally omitted: validating a picking needs available quantity (a prior receipt or "
            "inventory adjustment) and reserved move lines. Build that flow on top of this lot.",
        ],
    },
    {
        "id": "invoice_posted",
        "title": "Posted customer invoice",
        "requires_modules": ["account"],
        "description": "Customer + account.move (out_invoice) with a line, posted — validates the CoA is set up.",
        "steps": [
            {"ref": "customer", "model": "res.partner", "values": {
                "name": "Fixture Invoice Customer", "is_company": True,
            }},
            {"ref": "product", "model": "product.product", "values": {
                "name": "Fixture Billable Service", "type": "service", "list_price": 150.0,
            }},
            {"ref": "invoice", "model": "account.move", "values": {
                "move_type": "out_invoice",
                "partner_id": "@customer.id",
                "invoice_line_ids": [(0, 0, {
                    "product_id": "@product.id",
                    "name": "Consulting",
                    "quantity": 2.0,
                    "price_unit": 150.0,
                })],
            }},
            {"ref": "posted", "model": "account.move", "method": "action_post", "on": "@invoice"},
        ],
        "notes": [
            "move_type='out_invoice' is a customer invoice; invoice_line_ids carry name/quantity/price_unit.",
            "account_id and tax_ids default from the product + fiscal position + the company's chart of accounts.",
            "action_post NEEDS a chart of accounts installed on the company — without one, account resolution fails.",
            "Posting assigns the invoice number and locks the move.",
        ],
    },
    {
        "id": "mo_with_bom",
        "title": "Manufacturing order with a bill of materials",
        "requires_modules": ["mrp"],
        "description": "Component + finished product + mrp.bom + mrp.production referencing them.",
        "steps": [
            {"ref": "component", "model": "product.product",
             "code_comment": "Odoo 17: type='product'. Odoo 18/19: type='consu' + is_storable=True.",
             "values": {"name": "Fixture Component", "type": "product"}},
            {"ref": "finished", "model": "product.product",
             "code_comment": "Odoo 17: type='product'. Odoo 18/19: type='consu' + is_storable=True.",
             "values": {"name": "Fixture Finished Good", "type": "product"}},
            {"ref": "bom", "model": "mrp.bom", "values": {
                "product_tmpl_id": "@finished.product_tmpl_id.id",
                "product_qty": 1.0,
                "bom_line_ids": [(0, 0, {"product_id": "@component.id", "product_qty": 2.0})],
            }},
            {"ref": "mo", "model": "mrp.production", "values": {
                "product_id": "@finished.id",
                "product_qty": 1.0,
                "bom_id": "@bom.id",
            }},
        ],
        "notes": [
            "mrp.bom.product_tmpl_id points at the finished good's TEMPLATE (product.product.product_tmpl_id).",
            "bom_line_ids are the components with a per-unit product_qty.",
            "mrp.production (the MO) references product_id + bom_id; confirming/producing it is a further step.",
            "Components and finished good are storable — apply the v17/v18 type version comment.",
        ],
    },
    {
        "id": "multi_company_pair",
        "title": "Second company + a user scoped to it",
        "requires_modules": ["base"],
        "description": "A second res.company and a res.users whose active/allowed company is company B.",
        "steps": [
            {"ref": "company_b", "model": "res.company", "values": {"name": "Fixture Company B"}},
            {"ref": "user_b", "model": "res.users", "values": {
                "name": "Fixture User B",
                "login": "user_b_fixture",
                "company_id": "@company_b.id",
                "company_ids": [(6, 0, ["@company_b.id"])],
            }},
        ],
        "notes": [
            "A second res.company lets you test company_id record rules and multi-company isolation.",
            "company_ids (6, 0, [...]) sets the allowed companies; company_id the active one — company_id "
            "MUST be in company_ids or Odoo raises a validation error.",
            "To exercise ir.rule domains, add groups_id (e.g. env.ref('base.group_user')) and read as this "
            "user via with_user(cls.user_b).",
        ],
    },
]


# --- @ref back-reference resolution (pure) -----------------------------------
class _CodeRef:
    """A back-reference resolved to a Python *expression* for code generation.

    In EXEC mode `resolve_refs` maps "@partner" to the real record (and "@p.id"
    to an int). In CODE mode it maps "@partner" to `_CodeRef("cls.partner")`;
    attribute access (`.id`, `.product_tmpl_id`) chains into a longer expression
    so "@tmpl.product_variant_id.id" renders as `cls.tmpl.product_variant_id.id`.
    """

    __slots__ = ("expr",)

    def __init__(self, expr):
        self.expr = expr

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _CodeRef(f"{self.expr}.{name}")

    def __repr__(self):
        return self.expr


def resolve_refs(values, created):
    """Resolve "@ref" / "@ref.attr" back-references inside a (possibly nested) values dict.

    `created` maps ref name -> record (EXEC) or `_CodeRef` (CODE). Recurses into
    dicts, lists and command tuples ((0, 0, {...}), (6, 0, [ids])). Multi-level
    attributes are traversed left-to-right. Unknown refs raise KeyError. Never
    mutates the input. Pure — no Odoo needed.
    """
    def res(v):
        if isinstance(v, str) and v.startswith("@"):
            ref, _, attr = v[1:].partition(".")
            if ref not in created:
                raise KeyError(f"unknown fixture ref @{ref}")
            base = created[ref]
            if attr:
                for part in attr.split("."):
                    base = getattr(base, part)
            return base
        if isinstance(v, dict):
            return {k: res(val) for k, val in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(res(x) for x in v)
        return v

    return {k: res(val) for k, val in values.items()}


# --- Code generation (pure) --------------------------------------------------
def _rid_to_class(rid):
    """'sale_order_stockable' -> 'TestFixtureSaleOrderStockable'."""
    return "TestFixture" + "".join(part.capitalize() for part in rid.split("_"))


def _py_literal(v):
    """Render a resolved value as a Python source expression.

    `_CodeRef` renders as its bare expression (an unquoted variable path);
    everything else renders as a literal. Handles nested dicts / lists / command
    tuples so a whole `values` payload round-trips to valid source.
    """
    if isinstance(v, _CodeRef):
        return v.expr
    if isinstance(v, bool):
        return "True" if v else "False"
    if v is None:
        return "None"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_py_literal(k)}: {_py_literal(val)}" for k, val in v.items()) + "}"
    if isinstance(v, tuple):
        inner = ", ".join(_py_literal(x) for x in v)
        return f"({inner},)" if len(v) == 1 else f"({inner})"
    if isinstance(v, list):
        return "[" + ", ".join(_py_literal(x) for x in v) + "]"
    return repr(v)


def render_fixture_code(recipe, class_name=None):
    """Render a complete, syntactically-valid `TransactionCase` skeleton string.

    setUpClass builds each step in order (create -> `cls.<ref> = cls.env[...].create({...})`,
    method -> `cls.<ref> = <target>.<method>()`), resolving @ref back-references into
    `cls.<ref>` expressions. A single placeholder test asserts the last created record
    exists. The output always `compile()`s.
    """
    steps = recipe["steps"]
    class_name = class_name or _rid_to_class(recipe["id"])
    # CODE-mode ref table: every ref becomes a `cls.<ref>` expression.
    created = {s["ref"]: _CodeRef(f"cls.{s['ref']}") for s in steps}

    body, last_ref = [], None
    for step in steps:
        ref = step["ref"]
        if "method" in step:
            target = resolve_refs({"_": step["on"]}, created)["_"]
            body.append(f"        cls.{ref} = {_py_literal(target)}.{step['method']}()")
        else:
            last_ref = ref
            if step.get("code_comment"):
                body.append(f"        # {step['code_comment']}")
            resolved = resolve_refs(step.get("values", {}), created)
            body.append(f"        cls.{ref} = cls.env[{step['model']!r}].create({{")
            for k, val in resolved.items():
                body.append(f"            {_py_literal(k)}: {_py_literal(val)},")
            body.append("        })")

    requires = ", ".join(recipe["requires_modules"]) or "base only"
    doc = [
        f'    """Builds fixture {recipe["id"]!r}: {recipe["title"]}.',
        "",
        f"    Requires modules: {requires}",
    ]
    for note in recipe.get("notes", []):
        doc.append(f"    - {note.replace(chr(34) * 3, chr(39) * 3)}")
    doc.append('    """')

    lines = [
        "# Generated by fixture_factory.py — a minimal, valid business-record fixture.",
        f"# Recipe: {recipe['id']} — {recipe['title']}",
        "from odoo.tests.common import TransactionCase, tagged",
        "",
        "",
        "@tagged('post_install', '-at_install')",
        f"class {class_name}(TransactionCase):",
        *doc,
        "",
        "    @classmethod",
        "    def setUpClass(cls):",
        "        super().setUpClass()",
        *body,
        "",
        "    def test_fixture_builds(self):",
        f"        self.assertTrue(self.{last_ref})",
        "",
    ]
    return "\n".join(lines)


# --- Public lookup helpers (pure) --------------------------------------------
def list_recipes():
    """[{id, title, requires_modules, description}] — the catalogue, no steps."""
    return [
        {"id": r["id"], "title": r["title"],
         "requires_modules": r["requires_modules"], "description": r["description"]}
        for r in RECIPES
    ]


def get_recipe(rid):
    """Return the full recipe dict for `rid`, or None."""
    for r in RECIPES:
        if r["id"] == rid:
            return r
    return None


def missing_modules(required, installed):
    """Required module names not present in `installed` (order preserved). Pure."""
    inst = set(installed)
    return [m for m in required if m not in inst]


def render_code_output(rid):
    """Pure CODE-path payload for a recipe id (no Odoo needed).

    'list'/'' -> the catalogue; unknown id -> an error payload; else the
    code-mode payload with the rendered skeleton.
    """
    if rid in ("list", ""):
        return {
            "recipes": list_recipes(),
            "count": len(RECIPES),
            "_caveat": "Pass FACTORY=<recipe_id> for a skeleton; add EXEC=1 to validate it against THIS DB.",
        }
    recipe = get_recipe(rid)
    if recipe is None:
        return {"error": "unknown_recipe", "recipe": rid,
                "available": [r["id"] for r in RECIPES]}
    return {
        "recipe": rid,
        "mode": "code",
        "requires_modules": recipe["requires_modules"],
        "test_code": render_fixture_code(recipe),
        "notes": recipe.get("notes", []),
        "_caveat": "Generated skeleton — adapt account/journal/CoA specifics to this instance.",
    }


# --- env-dependent execution (only inside odoo-bin shell) --------------------
def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes")


def _short(v, limit=120):
    try:
        s = str(v)
    except Exception:  # noqa: BLE001
        return f"<{type(v).__name__}>"
    return s if len(s) <= limit else s[:limit] + "…"


def _emit(payload, out):
    text = json.dumps(payload, indent=2, default=str)
    if out:
        with open(out, "w") as fh:
            fh.write(text)
        print(f"WROTE {out}")
    else:
        print("===ODOO_FIXTURE_START===")
        print(text)
        print("===ODOO_FIXTURE_END===")


def run():
    factory = (os.environ.get("FACTORY") or "list").strip()
    exec_mode = _truthy(os.environ.get("EXEC", ""))
    commit = _truthy(os.environ.get("COMMIT", ""))
    out = os.environ.get("OUT")

    # CODE path (default) — catalogue, unknown-recipe error, or a skeleton. No DB.
    if not exec_mode or factory in ("list", ""):
        _emit(render_code_output(factory), out)
        return

    recipe = get_recipe(factory)
    if recipe is None:
        _emit({"error": "unknown_recipe", "recipe": factory,
               "available": [r["id"] for r in RECIPES]}, out)
        return

    # Module gate: never execute a recipe whose modules aren't installed here.
    installed = env["ir.module.module"].search(  # noqa: F821
        [("state", "=", "installed")]).mapped("name")
    missing = missing_modules(recipe["requires_modules"], installed)
    if missing:
        _emit({"recipe": factory, "mode": "exec", "error": "missing_modules",
               "missing_modules": missing, "requires_modules": recipe["requires_modules"],
               "_caveat": "Install the missing modules (or pick another recipe) before executing."}, out)
        return

    warnings = []
    if commit:
        warnings.append("COMMIT=1 — records were PERSISTED (savepoint released + committed). "
                        "Use a throwaway/dev DB only.")
    if factory == "invoice_posted":
        warnings.append("invoice_posted needs a chart of accounts on the company; "
                        "action_post fails without one.")

    created_records, created_out, error = {}, {}, None
    cr = env.cr  # noqa: F821
    cr.execute("SAVEPOINT odoo_fixture")
    try:
        for step in recipe["steps"]:
            ref = step["ref"]
            if "method" in step:
                target = resolve_refs({"_": step["on"]}, created_records)["_"]
                result = getattr(target, step["method"])()
                created_out[ref] = {"method": step["method"], "on": step.get("on"),
                                    "result": _short(result)}
            else:
                vals = resolve_refs(step.get("values", {}), created_records)
                rec = env[step["model"]].create(vals)  # noqa: F821
                created_records[ref] = rec
                created_out[ref] = {"model": step["model"], "id": rec.id,
                                    "display_name": _short(rec.display_name)}
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    finally:
        # Persist only on an explicit COMMIT that also fully succeeded; otherwise
        # unwind everything this recipe touched.
        if commit and error is None:
            cr.execute("RELEASE SAVEPOINT odoo_fixture")
            env.cr.commit()  # noqa: F821  — real persist; dev/throwaway DB only
        else:
            cr.execute("ROLLBACK TO SAVEPOINT odoo_fixture")

    _emit({
        "recipe": factory,
        "mode": "exec",
        "rolled_back": not (commit and error is None),
        "committed": bool(commit and error is None),
        "error": error,
        "created": created_out,
        "requires_modules": recipe["requires_modules"],
        "test_code": render_fixture_code(recipe),
        "_warnings": warnings,
        "_caveat": "Executed against THIS instance in a savepoint (rolled back unless COMMIT=1). "
                   "On error, execution stopped at the failing step and everything was unwound.",
    }, out)


# `env` is injected by `odoo-bin shell`; its presence means we're running for
# real. Absent (a plain import in a unit test) -> run() is skipped and only the
# pure registry + helpers above are exposed.
if "env" in globals():
    run()  # noqa: F821
