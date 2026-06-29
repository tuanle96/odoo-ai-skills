"""
Unit + integration tests for viz.py (introspection JSON -> HTML charts).

Run with:
    python3 -m unittest tests.test_viz -v

Unit tests feed each renderer a minimal realistic dict and assert the expected
chart markup. The integration test renders the real samples/ fixtures end-to-end.
"""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
SAMPLES_DIR = SCRIPTS_DIR.parent / "references" / "samples"
sys.path.insert(0, str(SCRIPTS_DIR))

import viz  # noqa: E402

# --- fixtures --------------------------------------------------------------- #
BRIEF = {
    "identity": {"model": "sale.order", "table": "sale_order", "inherit": ["mail.thread"]},
    "field_count": 10,
    "overridden_methods": ["action_confirm"],
    "methods": {
        "action_confirm": [
            {"addon": "bm_sale", "returns_before_super": True, "has_super": False, "hooks_called": []},
            {"addon": "sale", "returns_before_super": False, "has_super": True,
             "super_position": "late (after custom logic)", "hooks_called": ["_action_confirm"]},
        ]
    },
    "manifest_depends": {"by_location": {"core": ["sale"], "local": ["bm_sale"]}},
}

META = {
    "model": "sale.order",
    "menu_graph": {"menus": [{"path": "Sales / Orders / Quotations", "action": "Quotations"}]},
    "reports": [{"name": "Quotation", "report_name": "sale.report_saleorder", "report_type": "qweb-pdf"}],
}

TRACE = {
    "root": "sale.order(1).action_confirm",
    "total_sql": 88, "total_addon_calls": 137, "error": None,
    "summary": {
        "max_depth": 9,
        "top_self_sql": [{"model": "stock.move", "method": "_action_done", "addon": "stock", "self_sql": 31}],
        "call_counts": [{"model": "sale.order.line", "method": "_compute_qty", "addon": "sale", "count": 14}],
    },
    "distinct_steps": [{"model": "sale.order", "method": "action_confirm", "addon": "sale"}],
    "calls": [{"depth": 0, "addon": "sale", "model": "sale.order", "method": "action_confirm", "sql_count": 88}],
}

SECURITY = {
    "model": "sale.order",
    "user": {"login": "sam@acme.com"},
    "access_rights": {
        "read": True, "write": True, "create": True, "unlink": False,
        "contributing_acl": [{"group": "Sales / User", "perm_read": True, "perm_write": True,
                              "perm_create": True, "perm_unlink": False}],
    },
    "record_rules": {"read": {"effective_domain": ["|", ["user_id", "=", 7], ["user_id", "=", False]]}},
    "field_access": {"restricted": [{"field": "margin", "groups": "sale.group_sale_margin"}]},
}

ESG = {
    "mode": "esg",
    "graph": {
        "models": [{"model": "sale.order", "touched_by": ["sale"]}],
        "edges": [{"from": "sale.order", "to": "stock.picking", "weight": 5},
                  {"from": "sale.order", "to": "account.move", "weight": 2}],
        "app_edges": [{"from": "sale", "to": "stock", "weight": 5}],
        "writes": {},
    },
    "summary": {"models_touched": 3, "model_edges": 2, "cross_app_edges": 1, "seeds_traced": 4},
}


# --- classification --------------------------------------------------------- #
class TestClassify(unittest.TestCase):
    def test_by_suffix(self):
        self.assertEqual(viz.classify(Path("sale_order.brief.json"), {}), "brief")
        self.assertEqual(viz.classify(Path("x.metadata.json"), {}), "meta")
        self.assertEqual(viz.classify(Path("x.trace.json"), {}), "trace")
        self.assertEqual(viz.classify(Path("x.security.json"), {}), "security")
        self.assertEqual(viz.classify(Path("x.esg.json"), {}), "esg")

    def test_by_content(self):
        self.assertEqual(viz.classify(Path("weird.json"), BRIEF), "brief")
        self.assertEqual(viz.classify(Path("weird.json"), META), "meta")
        self.assertEqual(viz.classify(Path("weird.json"), TRACE), "trace")
        self.assertEqual(viz.classify(Path("weird.json"), SECURITY), "security")
        self.assertEqual(viz.classify(Path("weird.json"), ESG), "esg")

    def test_unknown(self):
        self.assertIsNone(viz.classify(Path("entrypoints.json"), {"views": {}}))


# --- renderers -------------------------------------------------------------- #
class TestRenderers(unittest.TestCase):
    def test_brief_ladder_marks_stop_and_native(self):
        out = viz.render_brief(BRIEF, 1)
        self.assertIn('class="ladder"', out)
        self.assertIn('class="rung stop"', out)   # bm_sale returns before super()
        self.assertIn("rung native", out)         # sale is a core addon
        self.assertIn("action_confirm", out)
        self.assertIn("_action_confirm", out)     # hooks_called surfaced

    def test_metadata_tree(self):
        out = viz.render_metadata(META, 1)
        self.assertIn('class="tree"', out)
        for label in ("Sales", "Orders", "Quotations"):
            self.assertIn(label, out)
        self.assertIn("sale.report_saleorder", out)

    def test_trace_bars_and_calls(self):
        out = viz.render_trace(TRACE, 1)
        self.assertIn('class="barchart"', out)
        self.assertIn("width:", out)              # a proportional bar fill
        self.assertIn("_action_done", out)
        self.assertIn("d0", out)                  # depth label in the call list

    def test_security_matrix(self):
        out = viz.render_security(SECURITY, 1)
        self.assertIn('class="matrix"', out)
        self.assertIn('<td class="y">', out)      # granted perms
        self.assertIn('<td class="n">', out)      # unlink denied
        self.assertIn("Sales / User", out)
        self.assertIn("margin", out)              # restricted field chip

    def test_esg_bars_and_mermaid(self):
        out = viz.render_esg(ESG, 1)
        self.assertIn('class="barchart"', out)
        self.assertIn("flowchart LR", out)        # graph emitted as Mermaid text
        self.assertIn("sale.order", out)
        self.assertIn("stock.picking", out)

    def test_renderers_tolerate_empty(self):
        # Defensive: missing keys must not raise.
        for fn in (viz.render_brief, viz.render_metadata, viz.render_trace,
                   viz.render_security, viz.render_esg):
            self.assertIn("panel", fn({}, 1))


# --- assembly + escaping ---------------------------------------------------- #
class TestAssembly(unittest.TestCase):
    def test_build_html_inlines_css_and_is_self_contained(self):
        html = viz.build_html(["<section class='panel'>x</section>"], "T", ["📅 now"])
        self.assertIn("<style>", html)
        self.assertIn("--hl", html)                       # real report.css inlined
        self.assertNotIn('rel="stylesheet"', html)        # no external link
        self.assertIn("<title>T</title>", html)

    def test_escaping(self):
        out = viz.render_security({"model": "x<script>", "access_rights": {}}, 1)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)


# --- integration ------------------------------------------------------------ #
class TestMainIntegration(unittest.TestCase):
    def test_renders_sample_bundle(self):
        self.assertTrue(SAMPLES_DIR.is_dir(), f"samples dir missing: {SAMPLES_DIR}")
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "report.html"
            rc = viz.main([str(SAMPLES_DIR), "--no-open", "--out", str(out)])
            self.assertEqual(rc, 0)
            html = out.read_text(encoding="utf-8")
            # A / C / D / G fixtures exist in samples -> all four chart blocks.
            for cls in ('class="ladder"', 'class="tree"', 'class="barchart"', 'class="matrix"'):
                self.assertIn(cls, html)
            self.assertIn("<style>", html)

    def test_esg_through_main(self):
        import json
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "x.esg.json").write_text(json.dumps(ESG))
            out = Path(td) / "report.html"
            rc = viz.main([str(td), "--no-open", "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertIn("flowchart LR", out.read_text(encoding="utf-8"))

    def test_no_known_layers_returns_error(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "junk.json").write_text('{"views": {}}')
            rc = viz.main([str(td), "--no-open", "--out", str(Path(td) / "r.html")])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
