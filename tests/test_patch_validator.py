"""
Unit tests for patch_validator.py — the static Odoo anti-pattern linter (Layer I).

Each rule is checked on a POSITIVE sample (it fires) and a CLEAN sample (it does
NOT — guarding against false positives, which matter most for a linter). Pure;
no Odoo dependency. Import-safe.
"""
import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import patch_validator as pv  # noqa: E402


def _rules(findings):
    return {f["rule"] for f in findings}


class PythonRuleTests(unittest.TestCase):
    def _check(self, src):
        return _rules(pv.check_python("x.py", src))

    def test_attrs_states_fires_as_kwarg(self):
        self.assertIn("deprecated_attrs_states",
                      self._check("f = fields.Char(attrs={'invisible': 1})"))

    def test_attrs_states_no_false_positive_on_plain_assignment(self):
        # a local var named `states` is not the removed kwarg
        self.assertNotIn("deprecated_attrs_states",
                         self._check("states = ['draft', 'done']"))

    def test_name_get(self):
        self.assertIn("name_get", self._check("    def name_get(self):\n        return []"))
        self.assertNotIn("name_get", self._check("    def _compute_display_name(self):\n        pass"))

    def test_route_type_json(self):
        self.assertIn("route_type_json",
                      self._check("@http.route('/x', type='json')\ndef x(self): pass"))
        self.assertNotIn("route_type_json",
                         self._check("@http.route('/x', type='jsonrpc')\ndef x(self): pass"))
        # a dict {'type': 'json'} is not a route kwarg → no false positive
        self.assertNotIn("route_type_json", self._check("vals = {'type': 'json'}"))

    def test_sql_injection_fstring(self):
        self.assertIn("sql_injection",
                      self._check("self.env.cr.execute(f'SELECT * FROM t WHERE id={rec.id}')"))

    def test_sql_injection_percent_and_format(self):
        self.assertIn("sql_injection",
                      self._check("cr.execute('SELECT %s' %% (uid,))".replace("%%", "%")))
        self.assertIn("sql_injection",
                      self._check("cr.execute('SELECT {}'.format(uid))"))

    def test_sql_injection_parameterized_is_clean(self):
        self.assertNotIn("sql_injection",
                         self._check("cr.execute('SELECT * FROM t WHERE id = %s', (rec.id,))"))

    def test_unjustified_sudo_fires(self):
        self.assertIn("unjustified_sudo", self._check("recs = self.env['x'].sudo().search([])"))

    def test_sudo_with_comment_is_clean(self):
        self.assertNotIn("unjustified_sudo",
                         self._check("recs = self.sudo().search([])  # cron has no user"))

    def test_private_env_aliases(self):
        self.assertIn("private_env_alias", self._check("x = self._cr.fetchall()"))
        self.assertIn("private_env_alias", self._check("u = self._uid"))
        self.assertNotIn("private_env_alias", self._check("x = self.env.cr.fetchall()"))

    def test_leftover_debug_and_print(self):
        self.assertIn("leftover_debug", self._check("    breakpoint()"))
        self.assertIn("leftover_debug", self._check("    import pdb"))
        self.assertIn("leftover_print", _rules(pv.check_python("model.py", "    print(rec)")))

    def test_print_suppressed_in_test_files(self):
        self.assertNotIn("leftover_print", _rules(pv.check_python("tests/test_x.py", "    print(rec)")))

    def test_query_in_loop_fires(self):
        src = ("for line in self.order_line:\n"
               "    partner = self.env['res.partner'].search([('id', '=', line.x)])\n")
        self.assertIn("query_in_loop", self._check(src))

    def test_query_outside_loop_is_clean(self):
        src = "partners = self.env['res.partner'].search([('active', '=', True)])\n"
        self.assertNotIn("query_in_loop", self._check(src))

    def test_create_without_model_create_multi_blocks(self):
        src = "    def create(self, vals):\n        return super().create(vals)\n"
        findings = pv.check_python("m.py", src)
        self.assertIn("create_not_batch", _rules(findings))
        self.assertTrue(any(f["severity"] == "blocking" for f in findings
                            if f["rule"] == "create_not_batch"))

    def test_create_with_model_create_multi_is_clean(self):
        src = ("    @api.model_create_multi\n"
               "    def create(self, vals_list):\n"
               "        return super().create(vals_list)\n")
        self.assertNotIn("create_not_batch", self._check(src))

    def test_ensure_one_in_create_warns(self):
        src = "    def create(self, vals_list):\n        self.ensure_one()\n        return None\n"
        self.assertIn("ensure_one_in_batch", self._check(src))

    def test_ensure_one_in_normal_method_is_clean(self):
        src = "    def action_print(self):\n        pass\n\n    def _helper(self):\n        self.ensure_one()\n"
        self.assertNotIn("ensure_one_in_batch", self._check(src))


class XmlRuleTests(unittest.TestCase):
    def _check(self, src):
        return _rules(pv.check_xml("v.xml", src))

    def test_attrs_states_in_xml(self):
        self.assertIn("xml_attrs_states", self._check('<field name="x" attrs="{\'invisible\': 1}"/>'))

    def test_tree_tag(self):
        self.assertIn("tree_tag", self._check("<tree><field name='n'/></tree>"))
        self.assertNotIn("tree_tag", self._check("<list><field name='n'/></list>"))

    def test_xpath_without_position(self):
        self.assertIn("xpath_no_position", self._check('<xpath expr="//field"/>'))
        self.assertNotIn("xpath_no_position", self._check('<xpath expr="//field" position="after"/>'))


class ValidatePathsTests(unittest.TestCase):
    def test_summary_counts_and_dispatch(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.py"), "w") as fh:
                fh.write("    def create(self, vals):\n        return super().create(vals)\n")
            with open(os.path.join(d, "v.xml"), "w") as fh:
                fh.write("<tree/>\n")
            res = pv.validate_paths([d])
        self.assertEqual(res["summary"]["files"], 2)
        self.assertGreaterEqual(res["summary"]["blocking"], 1)   # the create()
        self.assertGreaterEqual(res["summary"]["warning"], 1)    # the <tree>
        self.assertIn("create_not_batch", res["summary"]["by_rule"])
        self.assertIn("tree_tag", res["summary"]["by_rule"])

    def test_clean_module_has_no_findings(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "ok.py"), "w") as fh:
                fh.write("from odoo import models, api\n\n"
                         "class O(models.Model):\n"
                         "    _inherit = 'sale.order'\n\n"
                         "    @api.model_create_multi\n"
                         "    def create(self, vals_list):\n"
                         "        return super().create(vals_list)\n")
            res = pv.validate_paths([d])
        self.assertEqual(res["summary"]["blocking"], 0, res["findings"])
        self.assertEqual(res["summary"]["warning"], 0, res["findings"])

    def test_main_prints_json(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.py")
            with open(p, "w") as fh:
                fh.write("x = self._cr\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                pv.main([p])
            data = json.loads(buf.getvalue())
        self.assertIn("private_env_alias", {f["rule"] for f in data["findings"]})


if __name__ == "__main__":
    unittest.main()
