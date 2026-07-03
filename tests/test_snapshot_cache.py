"""
Unit tests for snapshot_cache.py — the content-addressed instance-facts cache.

Covers: store/lookup roundtrip in a tempdir, max_age expiry, missing key → None,
addons_fingerprint sensitivity (mtime/size) + stability + missing-path marker,
cache_key stability & param-ordering independence, and mark_provenance shape /
merge-eligibility enforcement. Import-safe; no Odoo dependency.
"""
import os
import sys
import json
import time
import tempfile
import unittest
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"

_spec = importlib.util.spec_from_file_location(
    "snapshot_cache", SCRIPTS_DIR / "snapshot_cache.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


# ---------------------------------------------------------------------------
# store / lookup roundtrip
# ---------------------------------------------------------------------------

class StoreLookupTests(unittest.TestCase):
    def test_roundtrip_returns_payload_and_meta(self):
        with tempfile.TemporaryDirectory() as d:
            payload = {"model": "sale.order", "fields": {"name": {"type": "char"}}}
            meta = {"step": "facts", "db": "demo"}
            path = sc.store("k1", payload, meta, cache_dir=d)
            self.assertTrue(os.path.exists(path))
            got = sc.lookup("k1", cache_dir=d)
            self.assertIsNotNone(got)
            self.assertEqual(got["payload"], payload)
            self.assertEqual(got["meta"]["step"], "facts")
            self.assertEqual(got["meta"]["db"], "demo")

    def test_store_returns_path_inside_cache_dir(self):
        with tempfile.TemporaryDirectory() as d:
            path = sc.store("abc", {"x": 1}, {}, cache_dir=d)
            self.assertEqual(Path(path).parent, Path(d))
            self.assertEqual(Path(path).name, "abc.json")

    def test_missing_key_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(sc.lookup("does-not-exist", cache_dir=d))

    def test_corrupt_record_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bad.json").write_text("{not valid json")
            self.assertIsNone(sc.lookup("bad", cache_dir=d))


# ---------------------------------------------------------------------------
# max_age expiry
# ---------------------------------------------------------------------------

class MaxAgeTests(unittest.TestCase):
    def test_fresh_within_max_age_is_hit(self):
        with tempfile.TemporaryDirectory() as d:
            sc.store("k", {"x": 1}, {}, cache_dir=d)
            self.assertIsNotNone(sc.lookup("k", cache_dir=d, max_age_s=3600))

    def test_backdated_beyond_max_age_is_miss(self):
        with tempfile.TemporaryDirectory() as d:
            path = sc.store("k", {"x": 1}, {}, cache_dir=d)
            old = time.time() - 10_000
            os.utime(path, (old, old))
            self.assertIsNone(sc.lookup("k", cache_dir=d, max_age_s=3600))
            # ...but still a hit when no age limit is applied
            self.assertIsNotNone(sc.lookup("k", cache_dir=d))


# ---------------------------------------------------------------------------
# addons_fingerprint
# ---------------------------------------------------------------------------

class AddonsFingerprintTests(unittest.TestCase):
    def _write(self, d, name, content):
        p = Path(d) / name
        p.write_text(content)
        return p

    def test_stable_across_calls(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "models.py", "x = 1\n")
            self._write(d, "views.xml", "<odoo/>\n")
            fp1 = sc.addons_fingerprint([d])
            fp2 = sc.addons_fingerprint([d])
            self.assertEqual(fp1, fp2)
            self.assertEqual(len(fp1), 64)  # sha256 hex

    def test_changes_when_size_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "models.py", "x = 1\n")
            fp1 = sc.addons_fingerprint([d])
            p.write_text("x = 1\ny = 2\n")  # size + mtime change
            self.assertNotEqual(fp1, sc.addons_fingerprint([d]))

    def test_changes_when_mtime_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "models.py", "x = 1\n")
            fp1 = sc.addons_fingerprint([d])
            future = time.time() + 5000
            os.utime(p, (future, future))
            self.assertNotEqual(fp1, sc.addons_fingerprint([d]))

    def test_ignores_non_source_files(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "models.py", "x = 1\n")
            fp1 = sc.addons_fingerprint([d])
            self._write(d, "README.md", "docs")     # not a source ext
            self._write(d, "notes.txt", "scratch")
            self.assertEqual(fp1, sc.addons_fingerprint([d]))

    def test_missing_path_marker_never_raises(self):
        fp = sc.addons_fingerprint(["/nope/not/here"])
        self.assertEqual(len(fp), 64)
        # a present path and a missing one differ from the present path alone
        with tempfile.TemporaryDirectory() as d:
            self._write(d, "a.py", "x = 1\n")
            self.assertNotEqual(
                sc.addons_fingerprint([d]),
                sc.addons_fingerprint([d, "/nope/not/here"]))


# ---------------------------------------------------------------------------
# cache_key
# ---------------------------------------------------------------------------

class CacheKeyTests(unittest.TestCase):
    def test_stable_and_sha256(self):
        k1 = sc.cache_key("demo", "facts", {"a": 1}, "fp")
        k2 = sc.cache_key("demo", "facts", {"a": 1}, "fp")
        self.assertEqual(k1, k2)
        self.assertEqual(len(k1), 64)

    def test_param_ordering_independent(self):
        k1 = sc.cache_key("demo", "facts", {"a": 1, "b": 2}, "fp")
        k2 = sc.cache_key("demo", "facts", {"b": 2, "a": 1}, "fp")
        self.assertEqual(k1, k2)

    def test_distinct_inputs_distinct_keys(self):
        base = sc.cache_key("demo", "facts", {"a": 1}, "fp")
        self.assertNotEqual(base, sc.cache_key("other", "facts", {"a": 1}, "fp"))
        self.assertNotEqual(base, sc.cache_key("demo", "brief", {"a": 1}, "fp"))
        self.assertNotEqual(base, sc.cache_key("demo", "facts", {"a": 2}, "fp"))
        self.assertNotEqual(base, sc.cache_key("demo", "facts", {"a": 1}, "fp2"))


# ---------------------------------------------------------------------------
# mark_provenance
# ---------------------------------------------------------------------------

class MarkProvenanceTests(unittest.TestCase):
    def test_cold_is_merge_eligible_created_at(self):
        out = sc.mark_provenance({"model": "sale.order"}, "cold")
        self.assertEqual(out["_cache"]["provenance"], "cold")
        self.assertTrue(out["_cache"]["merge_eligible"])
        self.assertIn("created_at", out["_cache"])
        self.assertNotIn("cached_at", out["_cache"])

    def test_warm_not_merge_eligible_cached_at(self):
        out = sc.mark_provenance({"model": "sale.order"}, "warm")
        self.assertEqual(out["_cache"]["provenance"], "warm")
        self.assertFalse(out["_cache"]["merge_eligible"])
        self.assertIn("cached_at", out["_cache"])
        self.assertNotIn("created_at", out["_cache"])

    def test_stale_rejected_not_merge_eligible(self):
        out = sc.mark_provenance({"x": 1}, "stale-rejected")
        self.assertEqual(out["_cache"]["provenance"], "stale-rejected")
        self.assertFalse(out["_cache"]["merge_eligible"])

    def test_does_not_mutate_input(self):
        payload = {"model": "sale.order"}
        sc.mark_provenance(payload, "warm")
        self.assertNotIn("_cache", payload)  # original untouched
        # and the copy carries the original keys
        self.assertEqual(
            sc.mark_provenance(payload, "cold")["model"], "sale.order")


# ---------------------------------------------------------------------------
# Local CLI
# ---------------------------------------------------------------------------

class CliTests(unittest.TestCase):
    def test_stats_and_clear(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["ODOO_AI_CACHE_DIR"] = d
            try:
                sc.store("k1", {"x": 1}, {}, cache_dir=d)
                sc.store("k2", {"y": 2}, {}, cache_dir=d)
                stats = sc._cmd_stats()
                self.assertEqual(stats["count"], 2)
                self.assertGreater(stats["total_bytes"], 0)
                self.assertIsNotNone(stats["oldest"])
                cleared = sc._cmd_clear()
                self.assertEqual(cleared["removed"], 2)
                self.assertEqual(sc._cmd_stats()["count"], 0)
            finally:
                os.environ.pop("ODOO_AI_CACHE_DIR", None)

    def test_get_missing_and_present(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["ODOO_AI_CACHE_DIR"] = d
            try:
                self.assertIn("error", sc._cmd_get("nope"))
                sc.store("k1", {"x": 1}, {"step": "facts"}, cache_dir=d)
                rec = sc._cmd_get("k1")
                self.assertEqual(rec["payload"], {"x": 1})
                self.assertEqual(rec["meta"]["step"], "facts")
            finally:
                os.environ.pop("ODOO_AI_CACHE_DIR", None)


if __name__ == "__main__":
    unittest.main()
