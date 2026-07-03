"""
Unit tests for fixture_factory.py — business-record fixture recipes + code generation.

Covers: registry completeness (required keys, requires_modules policy, unique ids),
list_recipes / get_recipe shape, render_fixture_code output compiles for EVERY recipe,
@ref resolution (whole-record, .id attribute, nested command tuples, multi-level,
unknown ref), render_code_output payload shapes (catalogue / unknown / code), and
missing_modules. Import-safe; no Odoo dependency (run() is skipped without `env`).
"""
import sys
import unittest
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"

_spec = importlib.util.spec_from_file_location("fixture_factory", SCRIPTS_DIR / "fixture_factory.py")
ff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ff)

EXPECTED_IDS = {
    "customer_basic", "product_stockable", "sale_order_stockable", "sale_order_service",
    "purchase_to_receipt", "delivery_with_lot", "invoice_posted", "mo_with_bom",
    "multi_company_pair",
}


class _FakeRec:
    """Stand-in for an Odoo record: attribute access returns whatever was set."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------

class RegistryTests(unittest.TestCase):
    def test_every_recipe_has_required_keys(self):
        required = {"id", "title", "requires_modules", "description", "steps", "notes"}
        for r in ff.RECIPES:
            with self.subTest(recipe=r.get("id")):
                self.assertTrue(required <= set(r), f"missing keys: {required - set(r)}")
                self.assertIsInstance(r["requires_modules"], list)
                self.assertIsInstance(r["notes"], list)
                self.assertTrue(r["notes"], "notes must be non-empty")
                self.assertTrue(r["description"])

    def test_steps_well_formed(self):
        for r in ff.RECIPES:
            self.assertTrue(r["steps"], f"{r['id']} has no steps")
            for step in r["steps"]:
                with self.subTest(recipe=r["id"], ref=step.get("ref")):
                    self.assertIn("ref", step)
                    if "method" in step:
                        self.assertIn("on", step)
                        self.assertTrue(step["on"].startswith("@"))
                    else:
                        self.assertIn("model", step)
                        self.assertIn("values", step)
                        self.assertIsInstance(step["values"], dict)

    def test_requires_modules_non_empty_except_customer_basic(self):
        for r in ff.RECIPES:
            with self.subTest(recipe=r["id"]):
                if r["id"] == "customer_basic":
                    self.assertEqual(r["requires_modules"], [])
                else:
                    self.assertTrue(r["requires_modules"],
                                    f"{r['id']} must declare requires_modules")

    def test_recipe_ids_unique(self):
        ids = [r["id"] for r in ff.RECIPES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_expected_recipe_ids_present(self):
        self.assertEqual({r["id"] for r in ff.RECIPES}, EXPECTED_IDS)


# ---------------------------------------------------------------------------
# list_recipes / get_recipe
# ---------------------------------------------------------------------------

class LookupTests(unittest.TestCase):
    def test_list_recipes_shape(self):
        rows = ff.list_recipes()
        self.assertEqual(len(rows), len(ff.RECIPES))
        for row in rows:
            self.assertEqual(set(row), {"id", "title", "requires_modules", "description"})
            self.assertNotIn("steps", row, "catalogue must not leak steps")

    def test_get_recipe_known(self):
        r = ff.get_recipe("customer_basic")
        self.assertIsNotNone(r)
        self.assertEqual(r["id"], "customer_basic")

    def test_get_recipe_unknown(self):
        self.assertIsNone(ff.get_recipe("does_not_exist"))


# ---------------------------------------------------------------------------
# render_fixture_code — output compiles for EVERY recipe
# ---------------------------------------------------------------------------

class RenderCompileTests(unittest.TestCase):
    def test_every_recipe_compiles(self):
        for r in ff.RECIPES:
            with self.subTest(recipe=r["id"]):
                src = ff.render_fixture_code(r)
                compile(src, "<gen>", "exec")  # raises SyntaxError on bad output

    def test_generated_class_structure(self):
        for r in ff.RECIPES:
            src = ff.render_fixture_code(r)
            with self.subTest(recipe=r["id"]):
                self.assertIn("from odoo.tests.common import TransactionCase", src)
                self.assertIn("class TestFixture", src)
                self.assertIn("(TransactionCase):", src)
                self.assertIn("def setUpClass(cls):", src)
                self.assertIn("def test_fixture_builds(self):", src)
                self.assertIn("self.assertTrue(self.", src)

    def test_custom_class_name_respected(self):
        src = ff.render_fixture_code(ff.get_recipe("customer_basic"), class_name="MyFixtureCase")
        self.assertIn("class MyFixtureCase(TransactionCase):", src)
        compile(src, "<gen>", "exec")

    def test_version_comment_emitted_for_stockable(self):
        src = ff.render_fixture_code(ff.get_recipe("product_stockable"))
        self.assertIn("# Odoo 17: type='product'", src)

    def test_method_step_renders_call(self):
        src = ff.render_fixture_code(ff.get_recipe("sale_order_stockable"))
        self.assertIn("cls.so.action_confirm()", src)

    def test_command_tuple_and_backref_render(self):
        src = ff.render_fixture_code(ff.get_recipe("sale_order_stockable"))
        self.assertIn("(0, 0, {'product_id': cls.product.id", src)
        self.assertIn("'partner_id': cls.customer.id", src)


# ---------------------------------------------------------------------------
# resolve_refs — the pure @ref resolver
# ---------------------------------------------------------------------------

class ResolveRefsTests(unittest.TestCase):
    def test_whole_record_reference(self):
        rec = _FakeRec(id=7)
        out = ff.resolve_refs({"x": "@p"}, {"p": rec})
        self.assertIs(out["x"], rec)

    def test_dot_id_attribute(self):
        out = ff.resolve_refs({"partner_id": "@p.id"}, {"p": _FakeRec(id=42)})
        self.assertEqual(out["partner_id"], 42)

    def test_multi_level_attribute_traversal(self):
        tmpl = _FakeRec(id=99)
        variant = _FakeRec(product_tmpl_id=tmpl)
        out = ff.resolve_refs({"t": "@v.product_tmpl_id.id"}, {"v": variant})
        self.assertEqual(out["t"], 99)

    def test_nested_command_tuple_resolved(self):
        out = ff.resolve_refs(
            {"order_line": [(0, 0, {"product_id": "@prod.id"})]},
            {"prod": _FakeRec(id=5)},
        )
        self.assertEqual(out["order_line"], [(0, 0, {"product_id": 5})])

    def test_six_command_inner_list_resolved(self):
        out = ff.resolve_refs(
            {"company_ids": [(6, 0, ["@c.id"])]},
            {"c": _FakeRec(id=3)},
        )
        self.assertEqual(out["company_ids"], [(6, 0, [3])])

    def test_non_reference_values_untouched(self):
        vals = {"name": "Plain", "qty": 2.0, "flag": True, "nothing": None}
        self.assertEqual(ff.resolve_refs(vals, {}), vals)

    def test_email_like_string_not_treated_as_ref(self):
        # only a LEADING '@' is a ref; an email in the middle is a plain string
        out = ff.resolve_refs({"email": "buyer@example.com"}, {})
        self.assertEqual(out["email"], "buyer@example.com")

    def test_unknown_ref_raises(self):
        with self.assertRaises(KeyError):
            ff.resolve_refs({"x": "@missing.id"}, {})

    def test_input_not_mutated(self):
        vals = {"partner_id": "@p.id"}
        ff.resolve_refs(vals, {"p": _FakeRec(id=1)})
        self.assertEqual(vals, {"partner_id": "@p.id"})


# ---------------------------------------------------------------------------
# _CodeRef — the code-generation expression marker
# ---------------------------------------------------------------------------

class CodeRefTests(unittest.TestCase):
    def test_bare_expression(self):
        self.assertEqual(ff._py_literal(ff._CodeRef("cls.p")), "cls.p")

    def test_attribute_chain(self):
        ref = ff._CodeRef("cls.p")
        self.assertEqual(ff._py_literal(ref.id), "cls.p.id")
        self.assertEqual(ff._py_literal(ref.product_tmpl_id.id), "cls.p.product_tmpl_id.id")


# ---------------------------------------------------------------------------
# render_code_output — CODE-path payloads (pure, no Odoo)
# ---------------------------------------------------------------------------

class RenderCodeOutputTests(unittest.TestCase):
    def test_list_payload(self):
        out = ff.render_code_output("list")
        self.assertEqual(out["count"], len(ff.RECIPES))
        self.assertEqual(len(out["recipes"]), len(ff.RECIPES))

    def test_empty_string_is_catalogue(self):
        self.assertIn("recipes", ff.render_code_output(""))

    def test_unknown_recipe_error_shape(self):
        out = ff.render_code_output("nope")
        self.assertEqual(out["error"], "unknown_recipe")
        self.assertEqual(out["recipe"], "nope")
        self.assertEqual(set(out["available"]), EXPECTED_IDS)

    def test_known_recipe_code_payload(self):
        out = ff.render_code_output("invoice_posted")
        self.assertEqual(out["mode"], "code")
        self.assertEqual(out["recipe"], "invoice_posted")
        self.assertEqual(out["requires_modules"], ["account"])
        self.assertIn("TransactionCase", out["test_code"])
        compile(out["test_code"], "<gen>", "exec")


# ---------------------------------------------------------------------------
# missing_modules
# ---------------------------------------------------------------------------

class MissingModulesTests(unittest.TestCase):
    def test_reports_absent_only(self):
        self.assertEqual(ff.missing_modules(["a", "b", "c"], ["a", "c"]), ["b"])

    def test_all_present(self):
        self.assertEqual(ff.missing_modules(["stock"], ["stock", "sale"]), [])

    def test_empty_requirements(self):
        self.assertEqual(ff.missing_modules([], ["anything"]), [])

    def test_order_preserved(self):
        self.assertEqual(ff.missing_modules(["z", "y", "x"], []), ["z", "y", "x"])


if __name__ == "__main__":
    unittest.main()
