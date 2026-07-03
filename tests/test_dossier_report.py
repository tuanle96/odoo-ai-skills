"""
Unit tests for dossier_report.py — the Instance Dossier HTML renderer (local).

Renders synthetic FULL and PARTIAL dossier dicts to a tempdir and asserts the
output file exists, contains the expected section titles, skips missing sections
silently, emits {"ok": true, "html_path": ...} to stdout, and — critically —
escapes hostile data (no raw "<script>" reaches the HTML: esc() is used).
No Odoo dependency; viz.py + report.css are exercised for real.
"""
import io
import sys
import json
import tempfile
import unittest
import contextlib
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location(
    "dossier_report", SCRIPTS_DIR / "dossier_report.py")
dossier_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dossier_report)


def _full_dossier():
    return {
        "meta": {"db_name": "acme_prod", "odoo_version": "17.0",
                 "internal_user_count": 42, "company_count": 2,
                 "generated_at": "2026-07-03T10:00:00", "db_uuid": "abc-123"},
        "installed_modules": {"total": 210, "shown": 210, "modules": []},
        "custom_summary": {"standard": 180, "oca": 15, "custom": 15,
                           "custom_modules": ["acme_sale", "acme_stock"]},
        "studio_footprint": {"web_studio_installed": True, "studio_view_count": 12,
                             "manual_field_count": 34, "x_studio_field_count": 20,
                             "per_model_manual": [{"model": "res.partner", "count": 12},
                                                  {"model": "sale.order", "count": 8}]},
        "custom_fields": [{"model": "sale.order", "name": "x_ref", "ttype": "char",
                           "relation": None}],
        "server_actions": [{"id": 1, "name": "Recompute", "model": "sale.order",
                            "state": "code", "usage": "ir_actions_server", "custom": True}],
        "automations": [{"model": "crm.lead", "name": "Auto assign",
                         "trigger": "on_create", "active": True}],
        "crons": [{"name": "Nightly sync", "model": "sale.order",
                   "interval_number": 1, "interval_type": "days",
                   "active": True, "user": "admin"}],
        "security": {"groups_total": 90, "custom_groups": 6, "rules_total": 55,
                     "models_with_rules": 30, "multi_company_rules": 4,
                     "record_rules": [{"model": "sale.order", "name": "Own orders",
                                       "is_global": False, "groups": ["Sales / User"],
                                       "domain": "[('company_id','in',company_ids)]",
                                       "multi_company": True}]},
        "view_overrides": {"count": 40, "views": [{"model": "sale.order",
                                                   "name": "SO form x", "inherit_of": "sale.view_order_form"}]},
        "data_volumes": {"res.partner": 5000, "sale.order": 12000, "account.move": 30000},
        "config_surface": {"integration_keys": ["mail.catchall.domain", "sale.webhook_url"],
                           "integration_keys_total": 2, "outgoing_mail_servers": 1},
        "multi_company": {"company_count": 2, "company_names": ["Acme US", "Acme EU"],
                          "models_with_company_field": 9},
        "upgrade_risk_flags": [
            {"flag": "studio_present", "severity": "warn", "detail": "Studio detected"},
            {"flag": "many_custom_modules", "severity": "high", "detail": "15 custom modules"},
        ],
    }


class RenderFullDossierTests(unittest.TestCase):
    def _render(self, dossier):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "dossier.json"
            out = Path(td) / "report.html"
            src.write_text(json.dumps(dossier), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dossier_report.main([str(src), "--out", str(out)])
            result = json.loads(buf.getvalue().strip())
            html = out.read_text(encoding="utf-8") if out.exists() else ""
            return rc, result, html

    def test_full_render_ok(self):
        rc, result, html = self._render(_full_dossier())
        self.assertEqual(rc, 0)
        self.assertTrue(result["ok"])
        self.assertTrue(result["html_path"].endswith(".html"))
        self.assertTrue(html)

    def test_contains_expected_section_titles(self):
        _rc, _result, html = self._render(_full_dossier())
        for title in ("Upgrade risk flags", "Studio footprint", "Module footprint",
                      "Custom fields", "Server actions", "Automated actions",
                      "Scheduled actions", "Security", "View overrides",
                      "Data volumes", "Integration surface", "Companies"):
            with self.subTest(title=title):
                self.assertIn(title, html)

    def test_default_out_path_alongside_input(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "dossier.json"
            src.write_text(json.dumps(_full_dossier()), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                dossier_report.main([str(src)])
            result = json.loads(buf.getvalue().strip())
            self.assertTrue(result["ok"])
            self.assertTrue((Path(td) / "dossier.html").exists())

    def test_data_volume_values_rendered(self):
        _rc, _result, html = self._render(_full_dossier())
        self.assertIn("12000", html)  # sale.order volume shows in the bar chart


class PartialDossierTests(unittest.TestCase):
    def test_partial_renders_only_present_sections(self):
        partial = {
            "meta": {"db_name": "small_db", "generated_at": "2026-07-03T09:00:00"},
            "data_volumes": {"res.partner": 3},
            "upgrade_risk_flags": [],
        }
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "d.json"
            out = Path(td) / "r.html"
            src.write_text(json.dumps(partial), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                dossier_report.main([str(src), "--out", str(out)])
            result = json.loads(buf.getvalue().strip())
            html = out.read_text(encoding="utf-8")
        self.assertTrue(result["ok"])
        self.assertIn("Data volumes", html)
        self.assertIn("No elevated upgrade-risk flags", html)  # empty-flags path
        # sections whose data is absent must be skipped silently
        self.assertNotIn("Studio footprint", html)
        self.assertNotIn("Server actions", html)

    def test_empty_dossier_still_ok(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "d.json"
            out = Path(td) / "r.html"
            src.write_text("{}", encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = dossier_report.main([str(src), "--out", str(out)])
            result = json.loads(buf.getvalue().strip())
            self.assertEqual(rc, 0)
            self.assertTrue(result["ok"])
            self.assertTrue(out.exists())


class InjectionSafetyTests(unittest.TestCase):
    """Hostile data must be HTML-escaped (esc()) — no raw <script> in output."""

    MALICIOUS = "<script>alert(1)</script>"

    def _render(self, dossier):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "d.json"
            out = Path(td) / "r.html"
            src.write_text(json.dumps(dossier), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                dossier_report.main([str(src), "--out", str(out)])
            return out.read_text(encoding="utf-8")

    def test_malicious_module_name_escaped(self):
        d = _full_dossier()
        d["custom_summary"]["custom_modules"] = [self.MALICIOUS]
        html = self._render(d)
        self.assertNotIn(self.MALICIOUS, html)
        self.assertIn("&lt;script&gt;", html)

    def test_malicious_field_name_escaped(self):
        d = _full_dossier()
        d["custom_fields"] = [{"model": "sale.order", "name": self.MALICIOUS,
                               "ttype": "char", "relation": None}]
        html = self._render(d)
        self.assertNotIn(self.MALICIOUS, html)
        self.assertIn("&lt;script&gt;", html)

    def test_malicious_flag_detail_escaped(self):
        d = _full_dossier()
        d["upgrade_risk_flags"] = [{"flag": "x", "severity": "high",
                                    "detail": self.MALICIOUS}]
        html = self._render(d)
        self.assertNotIn(self.MALICIOUS, html)


if __name__ == "__main__":
    unittest.main()
