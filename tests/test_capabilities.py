"""
Unit tests for the pure helpers in the capabilities scanner (Layer H — native
capability surface). The script is import-safe outside an Odoo shell: the
env-dependent work runs only when `env` is in globals, so importing it here
exercises just the pure functions.
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import capabilities  # noqa: E402  (import-safe: run() gated on `env`)


class IsFunctionalFieldTests(unittest.TestCase):
    def test_business_fields_kept(self):
        for n in ("commitment_date", "invoice_status", "amount_total", "state"):
            self.assertTrue(capabilities.is_functional_field(n), n)

    def test_orm_plumbing_dropped(self):
        for n in ("id", "display_name", "create_uid", "create_date",
                  "write_uid", "write_date", "__last_update"):
            self.assertFalse(capabilities.is_functional_field(n), n)

    def test_mixin_internals_dropped(self):
        for n in ("message_ids", "message_follower_ids", "activity_ids",
                  "activity_state", "access_url", "access_token",
                  "website_message_ids", "rating_ids"):
            self.assertFalse(capabilities.is_functional_field(n), n)

    def test_empty(self):
        self.assertFalse(capabilities.is_functional_field(""))
        self.assertFalse(capabilities.is_functional_field(None))


class MixinCapabilitiesTests(unittest.TestCase):
    def test_full(self):
        out = capabilities.mixin_capabilities({"message_ids", "activity_ids", "access_url"})
        self.assertEqual(out, {"mail_thread": True, "activities": True, "portal": True})

    def test_partial(self):
        out = capabilities.mixin_capabilities({"message_ids", "name"})
        self.assertEqual(out, {"mail_thread": True, "activities": False, "portal": False})

    def test_empty_or_none(self):
        self.assertFalse(capabilities.mixin_capabilities(set())["mail_thread"])
        self.assertFalse(capabilities.mixin_capabilities(None)["activities"])


class CountSurfaceTests(unittest.TestCase):
    def test_counts_lists_only_and_ignores_truncation(self):
        surface = {
            "models": [{"model": "a"}, {"model": "b"}],
            "crons": [{"name": "c"}, {"_truncated": "+5 more"}],
            "wizards": [],
            "mode": "module",          # non-list metadata ignored
            "_summary": {"x": 1},      # non-list metadata ignored
        }
        self.assertEqual(
            capabilities.count_surface(surface),
            {"models": 2, "crons": 1, "wizards": 0})

    def test_empty_and_none(self):
        self.assertEqual(capabilities.count_surface({}), {})
        self.assertEqual(capabilities.count_surface(None), {})


if __name__ == "__main__":
    unittest.main()
