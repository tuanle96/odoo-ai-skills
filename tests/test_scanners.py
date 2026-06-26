"""
Unit tests for the pure helpers in the two focused introspection scanners
(field_refs = reverse impact, preflight = load-state). Both scripts are
import-safe outside an Odoo shell: the env-dependent work runs only when `env`
is in globals, so importing them here exercises just the pure functions.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import field_refs  # noqa: E402  (import-safe: run() gated on `env`)
import preflight   # noqa: E402  (import-safe: run() gated on `env`)


class DependsHitTests(unittest.TestCase):
    def test_local_match(self):
        self.assertTrue(field_refs.depends_hit(["commitment_date"], "commitment_date"))

    def test_dotted_last_segment_match(self):
        self.assertTrue(field_refs.depends_hit(["order_id.commitment_date"], "commitment_date"))

    def test_no_match(self):
        self.assertFalse(field_refs.depends_hit(["order_id.date_order"], "commitment_date"))

    def test_empty_and_none(self):
        self.assertFalse(field_refs.depends_hit([], "x"))
        self.assertFalse(field_refs.depends_hit(None, "x"))


class MentionsFieldTests(unittest.TestCase):
    def test_whole_identifier_only(self):
        self.assertTrue(field_refs.mentions_field("<field name='date'/>", "date"))
        self.assertFalse(field_refs.mentions_field("<field name='commitment_date'/>", "date"))

    def test_in_domain(self):
        self.assertTrue(field_refs.mentions_field("[('user_id','=',uid)]", "user_id"))

    def test_empty(self):
        self.assertFalse(field_refs.mentions_field("", "date"))


class ClassifySeverityTests(unittest.TestCase):
    def test_high(self):
        self.assertEqual(field_refs.classify_severity("stored_compute_depends"), "high")
        self.assertEqual(field_refs.classify_severity("related_field"), "high")

    def test_medium(self):
        for kind in ("view", "record_rule", "ir_filter", "server_action", "automation"):
            self.assertEqual(field_refs.classify_severity(kind), "medium")

    def test_low_default(self):
        self.assertEqual(field_refs.classify_severity("whatever"), "low")


class ParseAddonsPathTests(unittest.TestCase):
    def test_splits_and_strips(self):
        self.assertEqual(preflight.parse_addons_path("/a, /b ,/c"), ["/a", "/b", "/c"])

    def test_empty_and_none(self):
        self.assertEqual(preflight.parse_addons_path(""), [])
        self.assertEqual(preflight.parse_addons_path(None), [])


class ShadowPathsTests(unittest.TestCase):
    def test_duplicate_flagged(self):
        flags = preflight.shadow_paths(["/opt/odoo/addons", "/opt/odoo/addons/"])
        self.assertTrue(any(f["reason"].startswith("duplicate") for f in flags))

    def test_datadir_flagged(self):
        flags = preflight.shadow_paths(["/home/u/.local/share/Odoo/addons/18.0"])
        self.assertTrue(any("data-dir" in f["reason"] for f in flags))

    def test_clean_paths(self):
        self.assertEqual(preflight.shadow_paths(["/opt/a", "/opt/b"]), [])


if __name__ == "__main__":
    unittest.main()
