"""
Unit tests for env_diff — environment parity & drift detector.
Import-safe outside an Odoo shell (run() is gated on `env`).
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import env_diff  # noqa: E402  (import-safe: run() gated on `env`)


def _fp(**kwargs):
    """Build a minimal valid fingerprint dict; defaults to empty/community."""
    base = {
        "modules": {},
        "edition": "community",
        "counts": {
            "ir.model": 0, "ir.rule": 0, "ir.model.access": 0,
            "ir.cron": 0, "ir.actions.server": 0,
        },
        "studio_fields": [],
        "config_params": [],
    }
    base.update(kwargs)
    return base


class FingerprintShapeTest(unittest.TestCase):
    def test_returns_all_required_keys(self):
        shape = env_diff.fingerprint_shape()
        for key in ("modules", "edition", "counts", "studio_fields", "config_params"):
            self.assertIn(key, shape)

    def test_types(self):
        shape = env_diff.fingerprint_shape()
        self.assertIsInstance(shape["modules"], dict)
        self.assertIsInstance(shape["edition"], str)
        self.assertIsInstance(shape["counts"], dict)
        self.assertIsInstance(shape["studio_fields"], list)
        self.assertIsInstance(shape["config_params"], list)

    def test_edition_is_valid(self):
        shape = env_diff.fingerprint_shape()
        self.assertIn(shape["edition"], ("enterprise", "community"))


class DiffFingerprintsModulesTest(unittest.TestCase):
    def test_only_in_target(self):
        base = _fp(modules={"sale": "16.0.1.0.0"})
        target = _fp(modules={"sale": "16.0.1.0.0", "helpdesk": "16.0.1.0.0"})
        diff = env_diff.diff_fingerprints(base, target)
        self.assertIn("helpdesk", diff["modules"]["only_in_target"])
        self.assertEqual(diff["modules"]["only_in_base"], [])
        self.assertEqual(diff["modules"]["version_changed"], [])

    def test_only_in_base(self):
        base = _fp(modules={"sale": "16.0.1.0.0", "crm": "16.0.1.0.0"})
        target = _fp(modules={"sale": "16.0.1.0.0"})
        diff = env_diff.diff_fingerprints(base, target)
        self.assertIn("crm", diff["modules"]["only_in_base"])
        self.assertEqual(diff["modules"]["only_in_target"], [])

    def test_version_changed(self):
        base = _fp(modules={"sale": "16.0.1.0.0", "purchase": "16.0.1.0.0"})
        target = _fp(modules={"sale": "16.0.2.0.0", "purchase": "16.0.1.0.0"})
        diff = env_diff.diff_fingerprints(base, target)
        self.assertEqual(len(diff["modules"]["version_changed"]), 1)
        vc = diff["modules"]["version_changed"][0]
        self.assertEqual(vc["name"], "sale")
        self.assertEqual(vc["base"], "16.0.1.0.0")
        self.assertEqual(vc["target"], "16.0.2.0.0")

    def test_identical_modules_no_diff(self):
        mods = {"sale": "16.0.1.0.0", "purchase": "16.0.1.0.0"}
        fp = _fp(modules=mods)
        diff = env_diff.diff_fingerprints(fp, fp)
        self.assertEqual(diff["modules"]["only_in_base"], [])
        self.assertEqual(diff["modules"]["only_in_target"], [])
        self.assertEqual(diff["modules"]["version_changed"], [])


class DiffFingerprintsCountsTest(unittest.TestCase):
    def test_counts_delta_positive(self):
        base = _fp(counts={"ir.model": 100, "ir.rule": 10})
        target = _fp(counts={"ir.model": 120, "ir.rule": 10})
        diff = env_diff.diff_fingerprints(base, target)
        self.assertEqual(diff["counts"]["ir.model"]["base"], 100)
        self.assertEqual(diff["counts"]["ir.model"]["target"], 120)
        self.assertEqual(diff["counts"]["ir.model"]["delta"], 20)
        self.assertEqual(diff["counts"]["ir.rule"]["delta"], 0)

    def test_counts_delta_negative(self):
        base = _fp(counts={"ir.cron": 15})
        target = _fp(counts={"ir.cron": 12})
        diff = env_diff.diff_fingerprints(base, target)
        self.assertEqual(diff["counts"]["ir.cron"]["delta"], -3)

    def test_identical_counts_zero_delta(self):
        fp = _fp(counts={"ir.model": 50, "ir.rule": 5})
        diff = env_diff.diff_fingerprints(fp, fp)
        for v in diff["counts"].values():
            self.assertEqual(v["delta"], 0)


class DiffFingerprintsStudioAndConfigTest(unittest.TestCase):
    def test_studio_fields_diff(self):
        base = _fp(studio_fields=["sale.order.x_studio_a", "res.partner.x_studio_shared"])
        target = _fp(studio_fields=["sale.order.x_studio_b", "res.partner.x_studio_shared"])
        diff = env_diff.diff_fingerprints(base, target)
        self.assertIn("sale.order.x_studio_a", diff["studio_fields"]["only_in_base"])
        self.assertIn("sale.order.x_studio_b", diff["studio_fields"]["only_in_target"])
        self.assertNotIn("res.partner.x_studio_shared", diff["studio_fields"]["only_in_base"])
        self.assertNotIn("res.partner.x_studio_shared", diff["studio_fields"]["only_in_target"])

    def test_config_params_diff(self):
        base = _fp(config_params=["web.base.url", "mail.catchall.domain"])
        target = _fp(config_params=["web.base.url", "new.param.key"])
        diff = env_diff.diff_fingerprints(base, target)
        self.assertIn("mail.catchall.domain", diff["config_params"]["only_in_base"])
        self.assertIn("new.param.key", diff["config_params"]["only_in_target"])

    def test_edition_change_detected(self):
        base = _fp(edition="community")
        target = _fp(edition="enterprise")
        diff = env_diff.diff_fingerprints(base, target)
        self.assertTrue(diff["edition"]["changed"])
        self.assertEqual(diff["edition"]["base"], "community")
        self.assertEqual(diff["edition"]["target"], "enterprise")

    def test_identical_edition_not_changed(self):
        fp = _fp(edition="enterprise")
        diff = env_diff.diff_fingerprints(fp, fp)
        self.assertFalse(diff["edition"]["changed"])


class SummarizeDriftHighSeverityTest(unittest.TestCase):
    """High severity: target has things coding env lacks."""

    def _zero_diff(self, **overrides):
        """Return a clean zero-drift diff dict, patched with overrides."""
        base = {
            "modules": {"only_in_base": [], "only_in_target": [], "version_changed": []},
            "counts": {},
            "studio_fields": {"only_in_base": [], "only_in_target": []},
            "config_params": {"only_in_base": [], "only_in_target": []},
            "edition": {"base": "community", "target": "community", "changed": False},
        }
        base.update(overrides)
        return base

    def test_high_when_target_has_extra_modules(self):
        diff = self._zero_diff(modules={
            "only_in_base": [], "only_in_target": ["helpdesk", "timesheet"],
            "version_changed": [],
        })
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "high")
        self.assertTrue(len(summary["blocking"]) > 0)
        self.assertIn("do NOT claim production safety", summary["verdict"])

    def test_high_when_target_has_extra_studio_fields(self):
        diff = self._zero_diff(studio_fields={
            "only_in_base": [], "only_in_target": ["res.partner.x_studio_vip"],
        })
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "high")
        self.assertIn("do NOT claim production safety", summary["verdict"])

    def test_high_when_edition_changed(self):
        diff = self._zero_diff(edition={
            "base": "community", "target": "enterprise", "changed": True,
        })
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "high")
        self.assertIn("do NOT claim production safety", summary["verdict"])

    def test_verdict_mentions_extra_count(self):
        diff = self._zero_diff(modules={
            "only_in_base": [], "only_in_target": ["a", "b", "c"], "version_changed": [],
        })
        summary = env_diff.summarize_drift(diff)
        self.assertIn("3", summary["verdict"])

    def test_blocking_list_non_empty_on_high(self):
        diff = self._zero_diff(modules={
            "only_in_base": [], "only_in_target": ["x"], "version_changed": [],
        })
        summary = env_diff.summarize_drift(diff)
        self.assertIsInstance(summary["blocking"], list)
        self.assertGreater(len(summary["blocking"]), 0)


class SummarizeDriftLowAndNoneTest(unittest.TestCase):
    def test_none_on_identical_fingerprints(self):
        fp = _fp(modules={"sale": "16.0.1.0.0"}, edition="enterprise",
                 studio_fields=["sale.order.x_studio_x"],
                 config_params=["web.base.url"])
        diff = env_diff.diff_fingerprints(fp, fp)
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "none")
        self.assertEqual(summary["verdict"], "Environments match on captured dimensions.")
        self.assertEqual(summary["blocking"], [])

    def test_none_on_empty_fingerprints(self):
        diff = env_diff.diff_fingerprints(_fp(), _fp())
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "none")

    def test_low_on_version_change(self):
        base = _fp(modules={"sale": "16.0.1.0.0"})
        target = _fp(modules={"sale": "16.0.2.0.0"})
        diff = env_diff.diff_fingerprints(base, target)
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "low")
        self.assertEqual(summary["blocking"], [])

    def test_low_on_count_delta(self):
        base = _fp(counts={"ir.model": 100})
        target = _fp(counts={"ir.model": 110})
        diff = env_diff.diff_fingerprints(base, target)
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "low")

    def test_low_on_base_only_modules(self):
        # dev has modules prod doesn't — less critical, still low
        base = _fp(modules={"sale": "16.0.1.0.0", "dev_tool": "16.0.1.0.0"})
        target = _fp(modules={"sale": "16.0.1.0.0"})
        diff = env_diff.diff_fingerprints(base, target)
        summary = env_diff.summarize_drift(diff)
        self.assertEqual(summary["severity"], "low")


if __name__ == "__main__":
    unittest.main()
