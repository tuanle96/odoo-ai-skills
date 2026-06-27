"""
Unit tests for upgrade_check.py pure helpers.
Import-safe outside an Odoo shell: run() is gated on `env` in globals().
"""
import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import upgrade_check  # noqa: E402  (import-safe: run() gated on `env`)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _f(type_="char", required=False, has_default=False, store=True):
    """Shorthand field-meta dict."""
    return {"type": type_, "required": required, "has_default": has_default, "store": store}


# ---------------------------------------------------------------------------
# detect_renames
# ---------------------------------------------------------------------------

class DetectRenamesTest(unittest.TestCase):

    def test_high_confidence_single_pair(self):
        """One disappeared + one appeared, same type → high confidence."""
        old = {"name_old": _f("char")}
        new = {"name_new": _f("char")}
        renames = upgrade_check.detect_renames(old, new)
        self.assertEqual(len(renames), 1)
        r = renames[0]
        self.assertEqual(r["old"], "name_old")
        self.assertEqual(r["new"], "name_new")
        self.assertEqual(r["confidence"], "high")
        self.assertIn("reason", r)

    def test_ambiguous_multiple_candidates_low(self):
        """One disappeared + multiple appeared with same type → low confidence."""
        old = {"status": _f("char")}
        new = {"status_a": _f("char"), "status_b": _f("char")}
        renames = upgrade_check.detect_renames(old, new)
        self.assertEqual(len(renames), 1)
        self.assertEqual(renames[0]["old"], "status")
        self.assertEqual(renames[0]["confidence"], "low")

    def test_no_candidate_when_type_differs(self):
        """Type mismatch → no rename candidate."""
        old = {"foo": _f("char")}
        new = {"bar": _f("integer")}
        self.assertEqual(upgrade_check.detect_renames(old, new), [])

    def test_no_renames_when_field_stable(self):
        """Field present in both sides → no rename candidate."""
        fields = {"amount": _f("float")}
        self.assertEqual(upgrade_check.detect_renames(fields, fields), [])

    def test_similarity_ranks_best_match_first(self):
        """When two candidates exist, the name-similar one is chosen."""
        old = {"partner_name": _f("char")}
        new = {"xyz_abc_def": _f("char"), "partner_label": _f("char")}
        renames = upgrade_check.detect_renames(old, new)
        self.assertEqual(renames[0]["new"], "partner_label")
        self.assertEqual(renames[0]["confidence"], "low")


# ---------------------------------------------------------------------------
# classify_upgrade_risks
# ---------------------------------------------------------------------------

class ClassifyRisksTest(unittest.TestCase):

    def test_field_removed_blocking(self):
        old = {"foo": _f()}
        new = {}
        risks = upgrade_check.classify_upgrade_risks(old, new)
        removed = [r for r in risks if r["kind"] == "field_removed"]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["field"], "foo")
        self.assertEqual(removed[0]["severity"], "blocking")

    def test_new_required_no_default_blocking(self):
        old = {}
        new = {"bar": _f(required=True, has_default=False)}
        risks = upgrade_check.classify_upgrade_risks(old, new)
        req = [r for r in risks if r["kind"] == "new_required_no_default"]
        self.assertEqual(len(req), 1)
        self.assertEqual(req[0]["field"], "bar")
        self.assertEqual(req[0]["severity"], "blocking")

    def test_new_field_with_default_not_flagged(self):
        old = {}
        new = {"bar": _f(required=True, has_default=True)}
        risks = upgrade_check.classify_upgrade_risks(old, new)
        req = [r for r in risks if r["kind"] == "new_required_no_default"]
        self.assertEqual(req, [])

    def test_type_changed_warning(self):
        old = {"amount": _f("char")}
        new = {"amount": _f("float")}
        risks = upgrade_check.classify_upgrade_risks(old, new)
        tc = [r for r in risks if r["kind"] == "type_changed"]
        self.assertEqual(len(tc), 1)
        self.assertEqual(tc[0]["field"], "amount")
        self.assertEqual(tc[0]["severity"], "warning")

    def test_high_rename_not_double_counted_as_removed(self):
        """High-confidence rename → field_renamed only, NOT also field_removed."""
        old = {"name_old": _f()}
        new = {"name_new": _f()}
        risks = upgrade_check.classify_upgrade_risks(old, new)
        removed = [r for r in risks if r["kind"] == "field_removed"]
        renamed = [r for r in risks if r["kind"] == "field_renamed"]
        self.assertEqual(removed, [], "renamed field must not appear as field_removed")
        self.assertEqual(len(renamed), 1)
        self.assertEqual(renamed[0]["field"], "name_old")
        self.assertEqual(renamed[0]["severity"], "blocking")

    def test_noupdate_protected_warning(self):
        risks = upgrade_check.classify_upgrade_risks(
            {}, {}, noupdate_xmlids=["my_mod.my_record"]
        )
        noupd = [r for r in risks if r["kind"] == "noupdate_protected"]
        self.assertEqual(len(noupd), 1)
        self.assertEqual(noupd[0]["xmlid"], "my_mod.my_record")
        self.assertEqual(noupd[0]["severity"], "warning")
        # noupdate uses 'xmlid' key, not 'field'
        self.assertNotIn("field", noupd[0])

    def test_summary_counts_match(self):
        old = {"gone": _f(), "status": _f("char")}
        new = {"status": _f("integer"), "req_new": _f(required=True, has_default=False)}
        report = upgrade_check.build_report(old, new)
        s = report["summary"]
        blocking = sum(1 for r in report["risks"] if r["severity"] == "blocking")
        warning = sum(1 for r in report["risks"] if r["severity"] == "warning")
        self.assertEqual(s["blocking"], blocking)
        self.assertEqual(s["warning"], warning)


# ---------------------------------------------------------------------------
# render_migration_script
# ---------------------------------------------------------------------------

class RenderMigrationScriptTest(unittest.TestCase):

    def test_ast_parses_with_renames_and_removals(self):
        renames = [{"old": "old_status", "new": "status_new", "confidence": "high"}]
        removals = ["deprecated_field"]
        script = upgrade_check.render_migration_script(
            "my_module", "18.0.2.0.0", renames, removals
        )
        try:
            ast.parse(script)
        except SyntaxError as exc:
            self.fail(f"render_migration_script output is not valid Python: {exc}")

    def test_mentions_renamed_columns(self):
        renames = [{"old": "old_col", "new": "new_col", "confidence": "high"}]
        script = upgrade_check.render_migration_script("m", "1.0", renames, [])
        self.assertIn("old_col", script)
        self.assertIn("new_col", script)

    def test_ast_parses_empty(self):
        """No renames, no removals → still valid Python with pass."""
        script = upgrade_check.render_migration_script("m", "1.0", [], [])
        ast.parse(script)  # must not raise
        self.assertIn("pass", script)

    def test_ast_parses_removals_only(self):
        script = upgrade_check.render_migration_script("m", "1.0", [], ["old_field"])
        ast.parse(script)
        self.assertIn("old_field", script)

    def test_contains_migrate_function(self):
        script = upgrade_check.render_migration_script("m", "1.0", [], [])
        self.assertIn("def migrate(cr, version):", script)


# ---------------------------------------------------------------------------
# build_report integration
# ---------------------------------------------------------------------------

class BuildReportTest(unittest.TestCase):

    def test_report_keys_present(self):
        report = upgrade_check.build_report(
            {"x": _f()}, {"y": _f()},
            module="mod", version="18.0.1.0.0"
        )
        for key in ("renames", "risks", "migration_script", "summary", "_warnings", "_caveat"):
            self.assertIn(key, report)

    def test_migration_script_in_report_ast_parses(self):
        report = upgrade_check.build_report(
            {"old_f": _f("many2one")},
            {"new_f": _f("many2one")},
            module="sale_custom", version="17.0.2.0.0"
        )
        ast.parse(report["migration_script"])


if __name__ == "__main__":
    unittest.main()
