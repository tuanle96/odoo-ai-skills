"""
Unit tests for the odoo-upgrade skill scripts (18→19 cross-version porting).

All three scripts are stdlib-only and import without Odoo:
  - upgrade_brief.py : fixture regression — examples/fixture_module_18 carries
    9 planted breakages; the brief must report exactly 6 ERROR + 3 WARNING.
  - upgrade_verify.py: parse_output on a synthetic Odoo log (chained tracebacks,
    custom-frame attribution, ERROR/CRITICAL extraction).
  - gen_manifest.py  : rename/merge matching calibration — the absolute
    intersection floor that kills tiny-model spurious matches (found dogfooding
    on the full 18-vs-19 community trees).
"""
import unittest
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "odoo-upgrade"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SKILL_DIR / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


upgrade_brief = _load("upgrade_brief")
upgrade_verify = _load("upgrade_verify")
gen_manifest = _load("gen_manifest")
migrate_all = _load("migrate_all")
db_upgrade = _load("db_upgrade")
preflight = _load("preflight")


class TestFixtureRegression(unittest.TestCase):
    """The planted-breakage fixture must keep detecting 9/9 (6 ERROR, 3 WARNING)."""

    @classmethod
    def setUpClass(cls):
        import json
        manifest = json.loads(
            (SKILL_DIR / "references" / "manifest_18_19.partial.json").read_text())
        cls.brief = upgrade_brief.build_brief(
            SKILL_DIR / "examples" / "fixture_module_18", manifest)

    def test_severity_counts(self):
        self.assertEqual(self.brief["summary"]["ERROR"], 6)
        self.assertEqual(self.brief["summary"]["WARNING"], 3)

    def test_key_findings_present(self):
        subjects = {(f["kind"], f["subject"]) for f in self.brief["findings"]}
        for expected in [
            ("removed_module_dependency", "hr_contract"),
            ("removed_model", "hr.contract"),
            ("removed_model", "hr.candidate"),
            ("removed_model", "hr.expense.sheet"),
            ("removed_xmlid", "base.action_partner_title_contact"),
            ("removed_xmlid", "hr_contract.hr_contract_view_form"),
        ]:
            self.assertIn(expected, subjects)

    def test_findings_carry_locations_and_detection(self):
        for f in self.brief["findings"]:
            self.assertTrue(f["locations"], f"finding without location: {f['subject']}")
            self.assertIn("detection", f)

    def test_rename_candidate_suggestion(self):
        contract = next(f for f in self.brief["findings"]
                        if f["kind"] == "removed_model" and f["subject"] == "hr.contract")
        self.assertIn("hr.version", contract["detail"])


class TestVerifyParser(unittest.TestCase):
    SYNTHETIC_LOG = """\
2026-07-03 05:00:00,123 42 INFO verify19 odoo.modules.loading: loading module my_module
2026-07-03 05:00:01,000 42 ERROR verify19 odoo.modules.registry: Failed to load registry
Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/odoo/modules/registry.py", line 90, in new
    odoo.modules.load_modules(registry)
  File "/mnt/extra-addons/my_module/models/models.py", line 12, in action_report
    tmpl = self.env.ref("base.gone_xmlid")
ValueError: External ID not found in the system: base.gone_xmlid

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/odoo/http.py", line 10, in dispatch
    result = handler()
KeyError: 'x'
2026-07-03 05:00:02,000 42 CRITICAL verify19 odoo.modules.module: Couldn't load module my_module
"""

    def test_chained_tracebacks_and_custom_frame(self):
        r = upgrade_verify.parse_output(self.SYNTHETIC_LOG, ["my_module"])
        self.assertEqual(len(r["tracebacks"]), 2)
        first, second = r["tracebacks"]
        self.assertEqual(first["exception"], "ValueError")
        self.assertEqual(first["custom_frame"]["line"], 12)
        self.assertIn("my_module", first["custom_frame"]["file"])
        self.assertIsNone(second["custom_frame"])  # failure outside our addons

    def test_log_error_extraction(self):
        r = upgrade_verify.parse_output(self.SYNTHETIC_LOG, ["my_module"])
        levels = [e["level"] for e in r["log_errors"]]
        self.assertEqual(levels, ["ERROR", "CRITICAL"])

    def test_module_loaded_regex_contract(self):
        # main() requires positive proof of load: "Loading module <name> (n/m)".
        # Guard the log line format the check depends on.
        import re
        line = "2026-07-03 07:25:28,163 1 INFO verify19 odoo.modules.loading: Loading module bm_country (15/15)"
        self.assertTrue(re.search(r"Loading module bm_country \(\d+/\d+\)", line))


class TestManifestMatching(unittest.TestCase):
    def _model(self, *fields):
        return {"fields": {f: {"type": "Char"} for f in fields}, "methods": set()}

    def test_overlap_coefficient_superset_case(self):
        # hr.contract(27) absorbed into hr.version(63): overlap must stay high
        old = set(f"f{i}" for i in range(27))
        new = old | set(f"g{i}" for i in range(36))
        self.assertAlmostEqual(gen_manifest._overlap(old, new), 1.0)

    def test_intersection_floor_kills_tiny_model_match(self):
        # 4-field model sharing 3 generic fields scores 0.75 by overlap —
        # must NOT become a rename candidate (absolute intersection < 4).
        removed = {"hr.candidate": self._model("name", "company_id", "employee_id",
                                               "partner_id", "stage_id", "email")}
        tiny = {"noise.model": self._model("name", "company_id", "employee_id", "extra")}
        self.assertEqual(gen_manifest._best_matches(removed, tiny, "test"), [])

    def test_real_rename_still_matches(self):
        fields = [f"f{i}" for i in range(10)]
        removed = {"old.model": self._model(*fields)}
        added = {"new.model": self._model(*fields, "brand_new")}
        out = gen_manifest._best_matches(removed, added, "test")
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["old"], out[0]["new"]), ("old.model", "new.model"))
        self.assertIn("HEURISTIC", out[0]["note"])

    def test_two_phase_rename_then_merge(self):
        fields = [f"f{i}" for i in range(8)]
        removed = {"gone.a": self._model(*fields), "gone.b": self._model(*[f"x{i}" for i in range(8)])}
        added = {"fresh.a": self._model(*fields)}                       # rename target
        surviving = {"host.b": self._model(*[f"x{i}" for i in range(8)], "own")}  # merge target
        renames, merges = gen_manifest._match_removed_models(removed, added, surviving)
        self.assertEqual([(r["old"], r["new"]) for r in renames], [("gone.a", "fresh.a")])
        self.assertEqual([(m["old"], m["new"]) for m in merges], [("gone.b", "host.b")])


class TestFleetOrchestrator(unittest.TestCase):
    def test_topo_order_respects_depends(self):
        deps = {"c": ["b", "sale"], "b": ["a"], "a": ["base"], "solo": []}
        order, cyclic = migrate_all.topo_order(deps)
        self.assertEqual(cyclic, [])
        self.assertLess(order.index("a"), order.index("b"))
        self.assertLess(order.index("b"), order.index("c"))
        self.assertEqual(sorted(order), ["a", "b", "c", "solo"])

    def test_topo_order_cycle_appended_and_flagged(self):
        deps = {"x": ["y"], "y": ["x"], "a": []}
        order, cyclic = migrate_all.topo_order(deps)
        self.assertEqual(cyclic, ["x", "y"])
        self.assertEqual(order, ["a", "x", "y"])

    def test_effort_grades(self):
        def brief(err, warn, kinds=()):
            return {"summary": {"ERROR": err, "WARNING": warn, "INFO": 0},
                    "findings": [{"severity": "ERROR", "kind": k} for k in kinds]}
        self.assertEqual(migrate_all.effort_grade(brief(0, 7)), "S")
        self.assertEqual(migrate_all.effort_grade(
            brief(2, 0, ["removed_xmlid", "removed_xmlid"])), "M")
        self.assertEqual(migrate_all.effort_grade(
            brief(1, 0, ["removed_model"])), "L")          # rewrite work
        self.assertEqual(migrate_all.effort_grade(
            brief(5, 0, ["removed_xmlid"] * 5)), "L")      # volume

    def test_fleet_on_fixture(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fleet = migrate_all.run_fleet(
                [SKILL_DIR / "examples"],
                SKILL_DIR / "references" / "manifest_18_19.partial.json",
                Path(td))
            self.assertEqual(fleet["order"], ["fixture_module_18"])
            m = fleet["modules"]["fixture_module_18"]
            self.assertEqual((m["errors"], m["warnings"], m["effort"]), (6, 3, "L"))
            written = json.loads((Path(td) / "fleet.json").read_text())
            self.assertEqual(written["totals"]["modules"], 1)


class TestDbUpgradeCommands(unittest.TestCase):
    CF = "docker/docker-compose.upgrade.yml"

    def test_seed_targets_source_version_service(self):
        cmd = db_upgrade.cmd_seed(self.CF, "up19", "base,contacts")
        self.assertIn("odoo18", cmd)
        self.assertIn("-i", cmd)
        self.assertIn("base,contacts", cmd)

    def test_restore_picks_loader_by_extension(self):
        _, _, load_dump = db_upgrade.cmd_restore(self.CF, "up19", Path("prod.dump"))
        _, _, load_sql = db_upgrade.cmd_restore(self.CF, "up19", Path("prod.sql"))
        self.assertIn("pg_restore", load_dump)
        self.assertIn("psql", load_sql)

    def test_upgrade_uses_openupgrade_externally(self):
        cmd = db_upgrade.cmd_upgrade(self.CF, "up19")
        inner = cmd[-1]
        self.assertIn("odoo19", cmd)
        self.assertIn("--upgrade-path=/opt/openupgrade/openupgrade_scripts/scripts", inner)
        self.assertIn("--load=base,web,openupgrade_framework", inner)
        self.assertIn("/opt/openupgrade", inner)   # mounted checkout, never vendored
        self.assertIn("-u all", inner)

    def test_verdicts(self):
        clean = {"tracebacks": [], "log_errors": []}
        errors = {"tracebacks": [], "log_errors": [{"level": "ERROR"}]}
        crash = {"tracebacks": [{"exception": "X"}], "log_errors": []}
        self.assertEqual(db_upgrade.verdict_of(0, clean), "ok")
        self.assertEqual(db_upgrade.verdict_of(0, errors), "ok_with_log_errors")
        self.assertEqual(db_upgrade.verdict_of(0, crash), "failed")
        self.assertEqual(db_upgrade.verdict_of(1, clean), "failed")


class TestPortableSet(unittest.TestCase):
    DEPENDS = {"base_mod": ["base"], "ent_mod": ["account_reports"],
               "child_of_ent": ["ent_mod", "base_mod"], "dead_mod": ["base"],
               "ok_mod": ["base_mod"]}

    def test_exclusion_propagates_transitively(self):
        portable, excl = migrate_all.portable_set(self.DEPENDS, None, {"ent_mod"})
        self.assertEqual(portable, ["base_mod", "dead_mod", "ok_mod"])
        self.assertIn("child_of_ent", excl)  # dragged out by its dependency

    def test_installed_filter(self):
        installed = {"base_mod", "ok_mod", "ent_mod", "child_of_ent"}
        portable, _ = migrate_all.portable_set(self.DEPENDS, installed, {"ent_mod"})
        self.assertEqual(portable, ["base_mod", "ok_mod"])  # dead_mod not installed


try:
    import lxml  # noqa: F401
    HAS_LXML = True
except ImportError:
    HAS_LXML = False


@unittest.skipUnless(HAS_LXML, "anchor_check needs lxml")
class TestAnchorCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ac = _load("anchor_check")
        from lxml import etree
        cls.etree = etree

    def _el(self, xml):
        return self.etree.fromstring(xml)

    def test_locate_xpath_and_hasclass(self):
        arch = self._el('<form><div class="oe_title mw-100"><h1/></div></form>')
        spec = self._el('<xpath expr="//div[hasclass(\'oe_title\')]" position="inside"/>')
        node, err = self.ac.locate(arch, spec)
        self.assertIsNone(err)
        self.assertEqual(node.tag, "div")
        # exact-@class match fails when classes were extended — the 19 gotcha
        spec2 = self._el('<xpath expr="//div[@class=\'oe_title\']" position="inside"/>')
        node2, err2 = self.ac.locate(arch, spec2)
        self.assertIsNone(node2)
        self.assertEqual(err2, "no-match")

    def test_locate_tag_attrs_and_apply_positions(self):
        arch = self._el('<form><field name="a"/><field name="b"/></form>')
        spec = self._el('<field name="b" position="before"><field name="seq"/></field>')
        node, err = self.ac.locate(arch, spec)
        self.assertIsNone(err)
        self.ac.apply_op(arch, spec, node)
        self.assertEqual([f.get("name") for f in arch], ["a", "seq", "b"])
        rep = self._el('<field name="a" position="replace"><field name="a2"/></field>')
        node, _ = self.ac.locate(arch, rep)
        self.ac.apply_op(arch, rep, node)
        self.assertEqual([f.get("name") for f in arch], ["a2", "seq", "b"])

    def test_ops_of_unwraps_data(self):
        arch = self._el('<arch><data><xpath expr="//x" position="inside"/></data></arch>')
        ops = self.ac.ops_of(arch)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].tag, "xpath")

    def test_sibling_inject_still_reports_miss(self):
        # anchor_check reports the miss; the human triages against depends.
        # Both a safe (in-depends) and a real (undeclared) sibling anchor look
        # identical to the tool — a plain ANCHOR-MISS — which is why field
        # note #15 says triage against `depends`, don't eyeball.
        arch = self._el('<form><field name="date_order"/></form>')
        spec = self._el('<xpath expr="//field[@name=\'requisition_id\']" position="after"/>')
        node, err = self.ac.locate(arch, spec)
        self.assertIsNone(node)
        self.assertEqual(err, "no-match")


class TestPreflight(unittest.TestCase):
    def _ws(self, td, manifest, files):
        root = Path(td) / "mod"
        (root).mkdir()
        (root / "__manifest__.py").write_text(manifest)
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return Path(td)

    def test_sql_constraints_and_dead_block(self):
        import tempfile
        code = ("from odoo import models, fields, _\n\n"
                "class M(models.Model):\n    _name = 'x'\n"
                "    # _sql_constraints = [\n    #     ('dead', 'unique(a)', 'x'),\n    # ]\n"
                "    _sql_constraints = [\n"
                "        ('uniq_name', 'unique(name)', _('dup')),\n    ]\n")
        with tempfile.TemporaryDirectory() as td:
            ws = self._ws(td, "{'name':'m','version':'18.0.1.0.0'}", {"models/m.py": code})
            preflight.convert_sql_constraints(ws)
            out = (ws / "mod" / "models" / "m.py").read_text()
            self.assertIn("_uniq_name = models.Constraint(", out)
            self.assertNotIn("_sql_constraints", out)
            self.assertNotIn("# _sql_constraints", out)  # dead block removed

    def test_version_bump_and_search_group(self):
        import tempfile
        xml = ('<odoo><record model="ir.ui.view"><field name="arch" type="xml">'
               '<search><group expand="1" string="Group By">'
               '<filter name="f" context="{}"/></group></search>'
               '</field></record></odoo>')
        with tempfile.TemporaryDirectory() as td:
            ws = self._ws(td, "{'name':'m','version':'18.0.2.0.0'}", {"views/v.xml": xml})
            preflight.bump_versions(ws)
            preflight.xml_sweeps(ws)
            self.assertIn("'version':'19.0.2.0.0'",
                          (ws / "mod" / "__manifest__.py").read_text())
            out = (ws / "mod" / "views" / "v.xml").read_text()
            self.assertNotIn("expand=", out)
            self.assertNotIn('string="Group By"', out)

    def test_flag_init_hooks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ws = self._ws(td, "{'name':'m','version':'18.0.1.0.0'}",
                          {"hooks.py": "def post_init_hook(cr, registry):\n    cr.execute('x')\n"})
            # add a module already on the 19 signature — must NOT be flagged
            (Path(td) / "ok").mkdir()
            (Path(td) / "ok" / "__init__.py").write_text("def post_init_hook(env):\n    pass\n")
            out = preflight.flag_init_hooks(Path(td))
            self.assertIn("mod/hooks.py", out)
            self.assertNotIn("ok/__init__.py", out)

    def test_fields_import_and_deps_scan(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ws = self._ws(td,
                          "{'name':'m','version':'18.0.1.0.0',"
                          "'external_dependencies':{'python':['PyJWT','xlrd']}}",
                          {"models/m.py": "from odoo.fields import datetime\n"})
            preflight.fix_fields_import(ws)
            self.assertIn("from datetime import datetime",
                          (ws / "mod" / "models" / "m.py").read_text())
            out = Path(td) / "out"
            preflight.scan_python_deps(ws, out)
            self.assertEqual((out / "pydeps.txt").read_text(), "PyJWT xlrd")


if __name__ == "__main__":
    unittest.main()
