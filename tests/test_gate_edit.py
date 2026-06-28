"""
Unit tests for the pure decision logic in gate_edit.py (Layer K — enforcement).
gate_edit is a LOCAL tool (no Odoo); these test model extraction + the
allow/block decision without touching the filesystem.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import gate_edit as G  # noqa: E402


class ExtractPythonTests(unittest.TestCase):
    def test_name_and_inherit_single(self):
        src = "class SO(models.Model):\n    _name = 'sale.order'\n    _inherit = \"mail.thread\"\n"
        self.assertEqual(G.extract_models_from_python(src), {"sale.order", "mail.thread"})

    def test_inherit_list(self):
        src = "_inherit = ['sale.order', \"portal.mixin\", 'mail.thread']"
        self.assertEqual(G.extract_models_from_python(src),
                         {"sale.order", "portal.mixin", "mail.thread"})

    def test_none(self):
        self.assertEqual(G.extract_models_from_python(""), set())
        self.assertEqual(G.extract_models_from_python("x = 1"), set())


class ExtractXmlTests(unittest.TestCase):
    def test_model_field_and_attr(self):
        src = ('<record model="ir.ui.view">'
               '<field name="model">sale.order</field></record>'
               '<record model="ir.actions.server"><field name="model_id" ref="x"/></record>')
        got = G.extract_models_from_xml(src)
        self.assertIn("sale.order", got)
        # ir.ui.view (the record's own technical model) must be filtered out
        self.assertNotIn("ir.ui.view", got)

    def test_business_model_attr_kept(self):
        # a model="sale.order" attr (e.g. on a tree/form) is a business target
        self.assertIn("sale.order", G.extract_models_from_xml('<tree model="sale.order"/>'))


class DecideTests(unittest.TestCase):
    def test_block_when_no_evidence(self):
        d = G.decide({"sale.order"}, set(), 0, has_validator=True)
        self.assertEqual(d["decision"], "block")
        self.assertEqual(d["missing_evidence"], ["sale.order"])
        self.assertIn("odoo-ai all sale.order", d["required_commands"])

    def test_allow_when_evidence_present_and_clean(self):
        ev = {"sale_order.brief.json"}
        d = G.decide({"sale.order"}, ev, 0, has_validator=True)
        self.assertEqual(d["decision"], "allow")
        self.assertEqual(d["missing_evidence"], [])

    def test_block_on_validator_blocking_even_with_evidence(self):
        ev = {"sale_order.brief.json"}
        d = G.decide({"sale.order"}, ev, 2, has_validator=True)
        self.assertEqual(d["decision"], "block")
        self.assertTrue(any("blocking" in r for r in d["reasons"]))

    def test_technical_models_never_required(self):
        # editing only ir.ui.view / res.config has no business model to introspect
        d = G.decide({"ir.ui.view", "res.config.settings"}, set(), 0, has_validator=True)
        self.assertEqual(d["decision"], "allow")
        self.assertEqual(d["touched_models"], [])

    def test_surface_evidence_also_counts(self):
        # a surface/metadata/capabilities artifact also proves introspection
        for name in ("sale_order.surface.json", "sale_order.metadata.json",
                     "sale_order.capabilities.json", "sale_order.entrypoints.json"):
            self.assertTrue(G.evidence_for_model("sale.order", {name}), name)
        self.assertFalse(G.evidence_for_model("sale.order", {"other_model.brief.json"}))

    def test_validator_unavailable_does_not_block(self):
        # if the validator couldn't run, evidence presence alone allows
        ev = {"sale_order.brief.json"}
        d = G.decide({"sale.order"}, ev, 0, has_validator=False)
        self.assertEqual(d["decision"], "allow")
        self.assertIsNone(d["validate_blocking"])


if __name__ == "__main__":
    unittest.main()
