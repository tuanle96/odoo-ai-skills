"""
Unit tests for fit_gap (Fit-Gap alpha) PURE helpers — the classification ladder,
effort banding, EN+VN risk classing, gap detection and the summary counter.

Import-safe outside an Odoo shell (run() is gated on `env`); reuses native_check's
pure helpers via the same scripts dir. Tests use synthetic match dicts only — no
Odoo, no cards on disk required.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fit_gap  # noqa: E402  (import-safe: run() gated on `env`)


def _match(card_id="x", domain="sale", modules=None, gated=False,
           found=False, modules_installed=True, gaps=None):
    """Build a synthetic match dict shaped like run() produces one."""
    return {
        "card": {"id": card_id, "title": card_id, "domain": domain,
                 "primitive": "wizard", "modules": modules or []},
        "gated": gated,
        "probe_result": [{"check": f"{card_id} probe", "found": found}],
        "modules_installed": modules_installed,
        "gaps": gaps or [],
    }


class ImportSafetyTests(unittest.TestCase):
    def test_module_imports_without_odoo_or_cards(self):
        # Importing at all (above) proves import-safety; the sibling reuse resolved.
        self.assertTrue(hasattr(fit_gap, "classify_requirement"))
        self.assertIsNotNone(fit_gap.match_cards, "native_check helpers should import")


class ClassifyRequirementTests(unittest.TestCase):
    def test_native_config(self):
        out = fit_gap.classify_requirement([_match(gated=True, found=True)])
        self.assertEqual(out["classification"], "native_config")
        self.assertEqual(out["confidence"], "gated")
        self.assertTrue(out["evidence"], "gated evidence should carry the found probe")
        self.assertTrue(all(e["found"] for e in out["evidence"]))
        self.assertEqual(out["gaps"], [])

    def test_config_plus_gap(self):
        out = fit_gap.classify_requirement(
            [_match(gated=True, found=True, gaps=["non-standard revenue recognition"])])
        self.assertEqual(out["classification"], "config_plus_gap")
        self.assertEqual(out["confidence"], "gated")
        self.assertIn("non-standard revenue recognition", out["gaps"])

    def test_module_available_not_installed(self):
        out = fit_gap.classify_requirement(
            [_match(gated=False, modules=["sale_stock"], modules_installed=False)])
        self.assertEqual(out["classification"], "module_available_not_installed")
        self.assertEqual(out["confidence"], "heuristic")
        self.assertIn("sale_stock", out["recommended_modules"])

    def test_pattern_known_not_present(self):
        out = fit_gap.classify_requirement(
            [_match(gated=False, modules=["sale"], modules_installed=True)])
        self.assertEqual(out["classification"], "pattern_known_not_present")
        self.assertEqual(out["confidence"], "heuristic")
        self.assertEqual(out["recommended_modules"], [])

    def test_no_known_pattern_on_empty(self):
        out = fit_gap.classify_requirement([])
        self.assertEqual(out["classification"], "no_known_pattern")
        self.assertEqual(out["confidence"], "heuristic")
        self.assertEqual(out["recommendation"], "custom_or_process_change")

    def test_gated_wins_over_not_installed(self):
        # One gated card + one not-installed card -> native_config (a present
        # native capability outranks 'a module you could install').
        out = fit_gap.classify_requirement([
            _match(card_id="present", gated=True, found=True),
            _match(card_id="other", gated=False, modules=["oca_x"], modules_installed=False),
        ])
        self.assertEqual(out["classification"], "native_config")
        self.assertEqual(len(out["matched_cards"]), 2)


class EffortBandTests(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(fit_gap.effort_band("native_config"), "S")
        self.assertEqual(fit_gap.effort_band("config_plus_gap"), "M")
        self.assertEqual(fit_gap.effort_band("module_available_not_installed"), "M")
        self.assertEqual(fit_gap.effort_band("pattern_known_not_present"), "L")
        self.assertEqual(fit_gap.effort_band("no_known_pattern"), "L")

    def test_unknown_defaults_to_L(self):
        self.assertEqual(fit_gap.effort_band("something_else"), "L")


class RiskClassTests(unittest.TestCase):
    def test_english_keywords_high(self):
        self.assertEqual(fit_gap.risk_class("configure accounting entries", ["sale"]), "high")
        self.assertEqual(fit_gap.risk_class("stock valuation layers", ["stock"]), "high")
        self.assertEqual(fit_gap.risk_class("user permission matrix", ["sale"]), "high")
        self.assertEqual(fit_gap.risk_class("multi-company setup", ["sale"]), "high")
        self.assertEqual(fit_gap.risk_class("run payroll", ["sale"]), "high")

    def test_vietnamese_keywords_high(self):
        self.assertEqual(fit_gap.risk_class("quản lý kế toán", ["sale"]), "high")
        self.assertEqual(fit_gap.risk_class("định giá tồn kho", ["sale"]), "high")
        self.assertEqual(fit_gap.risk_class("phân quyền người dùng", ["sale"]), "high")
        self.assertEqual(fit_gap.risk_class("bảng lương nhân viên", ["sale"]), "high")

    def test_account_domain_always_high(self):
        self.assertEqual(fit_gap.risk_class("add a note", ["account"]), "high")

    def test_normal(self):
        self.assertEqual(fit_gap.risk_class("add a banner to the quote", ["sale"]), "normal")
        self.assertEqual(fit_gap.risk_class("", []), "normal")


class DetectGapsTests(unittest.TestCase):
    CARD = {"when_not_enough": ["non-standard revenue recognition",
                                "external invoice reservation"]}

    def test_hit(self):
        hits = fit_gap.detect_gaps("we need revenue recognition scheduling", self.CARD)
        self.assertIn("non-standard revenue recognition", hits)

    def test_no_hit(self):
        self.assertEqual(fit_gap.detect_gaps("just print a delivery slip", self.CARD), [])

    def test_empty_when_not_enough(self):
        self.assertEqual(fit_gap.detect_gaps("anything", {}), [])


class SummarizeTests(unittest.TestCase):
    def test_counts(self):
        reqs = [
            {"classification": "native_config"},
            {"classification": "native_config"},
            {"classification": "config_plus_gap"},
            {"classification": "no_known_pattern"},
        ]
        summary = fit_gap.summarize(reqs)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["by_classification"]["native_config"], 2)
        self.assertEqual(summary["by_classification"]["config_plus_gap"], 1)
        self.assertEqual(summary["by_classification"]["no_known_pattern"], 1)
        # every classification key is present (zero-filled) for stable consumers
        self.assertEqual(summary["by_classification"]["pattern_known_not_present"], 0)

    def test_empty(self):
        summary = fit_gap.summarize([])
        self.assertEqual(summary["total"], 0)
        self.assertTrue(all(v == 0 for v in summary["by_classification"].values()))


if __name__ == "__main__":
    unittest.main()
