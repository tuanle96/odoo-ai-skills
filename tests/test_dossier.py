"""
Unit tests for dossier.py — the Instance Dossier v0 collector (Layer: takeover).

The shell collector must import WITHOUT Odoo (env-dependent work lives in run(),
gated by `if "env" in globals()`). We test the pure, module-level helpers:
classify_module_author, is_integration_config_key, domain_references_company,
_rg_count, and the pure derivation risk_flags() on synthetic dossier dicts.
"""
import sys
import unittest
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
# redaction is imported at module load via `from redaction import ...`
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

_spec = importlib.util.spec_from_file_location("dossier", SCRIPTS_DIR / "dossier.py")
dossier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dossier)


# ---------------------------------------------------------------------------
# Import safety: env-dependent run() must NOT fire on import
# ---------------------------------------------------------------------------

class ImportSafetyTests(unittest.TestCase):
    def test_module_imports_without_odoo(self):
        # If run() had fired (no `env`), the import above would have raised.
        for name in ("classify_module_author", "is_integration_config_key",
                     "domain_references_company", "risk_flags", "run"):
            self.assertTrue(hasattr(dossier, name), name)

    def test_redaction_available(self):
        # redaction is a sibling; it must import so the collector can redact.
        self.assertTrue(callable(dossier.redact_payload))


# ---------------------------------------------------------------------------
# classify_module_author
# ---------------------------------------------------------------------------

class ClassifyModuleAuthorTests(unittest.TestCase):
    def test_standard_odoo_sa(self):
        self.assertEqual(dossier.classify_module_author("Odoo S.A."), "standard")
        self.assertEqual(
            dossier.classify_module_author("Odoo S.A., other contributors"), "standard")

    def test_oca(self):
        self.assertEqual(
            dossier.classify_module_author("Odoo Community Association (OCA)"), "oca")

    def test_custom_third_party(self):
        self.assertEqual(dossier.classify_module_author("Acme Consulting"), "custom")

    def test_custom_empty_or_none(self):
        self.assertEqual(dossier.classify_module_author(""), "custom")
        self.assertEqual(dossier.classify_module_author(None), "custom")

    def test_case_insensitive(self):
        self.assertEqual(dossier.classify_module_author("ODOO S.A."), "standard")


# ---------------------------------------------------------------------------
# is_integration_config_key
# ---------------------------------------------------------------------------

class IntegrationConfigKeyTests(unittest.TestCase):
    def test_integration_ish_keys_match(self):
        for key in ("mail.catchall.domain", "sale.webhook_url", "my.api_key",
                    "auth_oauth.provider", "payment.token", "web.base.url"):
            with self.subTest(key=key):
                self.assertTrue(dossier.is_integration_config_key(key), key)

    def test_plain_keys_do_not_match(self):
        for key in ("database.uuid", "base.language", "digest.periodicity"):
            with self.subTest(key=key):
                self.assertFalse(dossier.is_integration_config_key(key), key)

    def test_none_safe(self):
        self.assertFalse(dossier.is_integration_config_key(None))


# ---------------------------------------------------------------------------
# domain_references_company
# ---------------------------------------------------------------------------

class DomainCompanyTests(unittest.TestCase):
    def test_true_when_company_id_present(self):
        self.assertTrue(
            dossier.domain_references_company("[('company_id','in',company_ids)]"))

    def test_false_when_absent(self):
        self.assertFalse(dossier.domain_references_company("[('state','=','draft')]"))

    def test_none_and_empty_safe(self):
        self.assertFalse(dossier.domain_references_company(None))
        self.assertFalse(dossier.domain_references_company(""))


# ---------------------------------------------------------------------------
# _rg_count (read_group count extraction, version-robust)
# ---------------------------------------------------------------------------

class ReadGroupCountTests(unittest.TestCase):
    def test_dunder_count(self):
        self.assertEqual(dossier._rg_count({"model": "res.partner", "__count": 7}), 7)

    def test_field_count_fallback(self):
        self.assertEqual(dossier._rg_count({"model": "res.partner", "model_count": 4}), 4)

    def test_missing_count_returns_zero(self):
        self.assertEqual(dossier._rg_count({"model": "res.partner"}), 0)


# ---------------------------------------------------------------------------
# risk_flags — pure derivation on synthetic dossiers
# ---------------------------------------------------------------------------

def _flag_map(flags):
    return {f["flag"]: f for f in flags}


class RiskFlagsTests(unittest.TestCase):
    def test_empty_dossier_no_flags(self):
        self.assertEqual(dossier.risk_flags({}), [])

    def test_studio_present_via_web_studio(self):
        flags = dossier.risk_flags({"studio_footprint": {"web_studio_installed": True}})
        self.assertIn("studio_present", _flag_map(flags))

    def test_studio_present_via_x_studio_fields(self):
        flags = dossier.risk_flags({"studio_footprint": {"x_studio_field_count": 3}})
        self.assertIn("studio_present", _flag_map(flags))

    def test_no_studio_flag_when_clean(self):
        flags = dossier.risk_flags(
            {"studio_footprint": {"web_studio_installed": False, "x_studio_field_count": 0}})
        self.assertNotIn("studio_present", _flag_map(flags))

    def test_manual_fields_threshold_strictly_greater(self):
        self.assertNotIn("manual_fields_high",
                         _flag_map(dossier.risk_flags(
                             {"studio_footprint": {"manual_field_count": 20}})))
        self.assertIn("manual_fields_high",
                      _flag_map(dossier.risk_flags(
                          {"studio_footprint": {"manual_field_count": 21}})))

    def test_many_custom_modules_high(self):
        self.assertNotIn("many_custom_modules",
                         _flag_map(dossier.risk_flags({"custom_summary": {"custom": 10}})))
        f = _flag_map(dossier.risk_flags({"custom_summary": {"custom": 11}}))
        self.assertIn("many_custom_modules", f)
        self.assertEqual(f["many_custom_modules"]["severity"], "high")

    def test_many_view_overrides_high(self):
        self.assertNotIn("many_view_overrides",
                         _flag_map(dossier.risk_flags({"view_overrides": {"count": 30}})))
        f = _flag_map(dossier.risk_flags({"view_overrides": {"count": 31}}))
        self.assertEqual(f["many_view_overrides"]["severity"], "high")

    def test_many_active_automations_counts_only_active(self):
        autos = [{"active": True}] * 11 + [{"active": False}] * 5
        self.assertIn("many_active_automations",
                      _flag_map(dossier.risk_flags({"automations": autos})))
        autos2 = [{"active": True}] * 9 + [{"active": False}] * 20
        self.assertNotIn("many_active_automations",
                         _flag_map(dossier.risk_flags({"automations": autos2})))

    def test_many_custom_server_actions_counts_only_custom(self):
        acts = [{"custom": True}] * 11 + [{"custom": False}] * 3
        self.assertIn("many_custom_server_actions",
                      _flag_map(dossier.risk_flags({"server_actions": acts})))

    def test_multi_company_with_custom_rules(self):
        f = _flag_map(dossier.risk_flags({
            "multi_company": {"company_count": 3},
            "security": {"custom_groups": 2},
        }))
        self.assertIn("multi_company_with_custom_rules", f)
        self.assertEqual(f["multi_company_with_custom_rules"]["severity"], "high")

    def test_single_company_no_multi_company_flag(self):
        self.assertNotIn("multi_company_with_custom_rules", _flag_map(dossier.risk_flags({
            "multi_company": {"company_count": 1},
            "security": {"custom_groups": 5},
        })))

    def test_all_severities_valid(self):
        big = {
            "studio_footprint": {"web_studio_installed": True, "manual_field_count": 99,
                                 "x_studio_field_count": 50},
            "custom_summary": {"custom": 40},
            "view_overrides": {"count": 99},
            "automations": [{"active": True}] * 20,
            "server_actions": [{"custom": True}] * 20,
            "multi_company": {"company_count": 4},
            "security": {"custom_groups": 9},
        }
        flags = dossier.risk_flags(big)
        self.assertGreaterEqual(len(flags), 7)
        for f in flags:
            self.assertIn(f["severity"], ("info", "warn", "high"))
            self.assertTrue(f["flag"] and f["detail"])

    def test_partial_dossier_does_not_raise(self):
        # sections may be None (guarded collection failed) — must not blow up
        dossier.risk_flags({"studio_footprint": None, "custom_summary": None,
                            "automations": None, "server_actions": None,
                            "multi_company": None, "security": None,
                            "view_overrides": None})


if __name__ == "__main__":
    unittest.main()
