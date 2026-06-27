"""
Unit tests for doc_index (Layer J local doc-index tool).

Covers: strip_images, chunk_rst, canonical_url, build_index, query_index.
All tests use in-memory RST fixtures — no network, no git, no Odoo required.
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import doc_index  # noqa: E402  (local tool — no Odoo env needed)
from doc_index import (  # noqa: E402
    strip_images, chunk_rst, canonical_url, build_index, query_index,
)


# ---------------------------------------------------------------------------
# Shared RST fixture
# ---------------------------------------------------------------------------

RST_FIXTURE = """\
Introduction
============

This section introduces the ORM module for database access in Odoo.
The ORM provides methods like create, write, search, and unlink.

.. image:: /_static/orm_diagram.png
   :alt: ORM Architecture

Fields Reference
----------------

Fields define the columns of a model. Common types include Char,
Integer, Float, Boolean, Date, Datetime, Many2one, One2many, Many2many.
Each field can carry attributes such as required, readonly, and default.

Methods
-------

Decorated methods form the business logic layer of your Odoo module.
Use @api.model for class-level operations and @api.depends for computed fields.
The ORM dispatches calls through the method resolution order of Python classes.
"""

_REL = "content/developer/reference/orm.rst"


# ---------------------------------------------------------------------------
# strip_images
# ---------------------------------------------------------------------------

class TestStripImages(unittest.TestCase):

    def test_removes_image_directive_and_options(self):
        text = "Before.\n\n.. image:: /img.png\n   :alt: foo\n\nAfter.\n"
        out  = strip_images(text)
        self.assertNotIn(".. image::", out)
        self.assertNotIn(":alt:", out)
        self.assertIn("Before.", out)
        self.assertIn("After.", out)

    def test_removes_figure_with_caption(self):
        text = (
            "Before.\n\n"
            ".. figure:: /img.png\n"
            "   :width: 100%\n\n"
            "   Caption text.\n\n"
            "After.\n"
        )
        out = strip_images(text)
        self.assertNotIn(".. figure::", out)
        self.assertNotIn("Caption text.", out)
        self.assertIn("Before.", out)
        self.assertIn("After.", out)

    def test_removes_image_substitution_definition(self):
        text = ".. |logo| image:: /logo.png\n   :alt: Logo\n\nText.\n"
        out  = strip_images(text)
        self.assertNotIn(".. |logo| image::", out)
        self.assertNotIn(":alt:", out)
        self.assertIn("Text.", out)

    def test_plain_text_unchanged(self):
        text = "No images here.\nJust plain text.\n"
        self.assertEqual(strip_images(text), text)

    def test_does_not_eat_following_paragraph(self):
        text = ".. image:: x.png\n   :alt: x\n\nKeep this paragraph.\n"
        out  = strip_images(text)
        self.assertIn("Keep this paragraph.", out)
        self.assertNotIn(".. image::", out)


# ---------------------------------------------------------------------------
# chunk_rst
# ---------------------------------------------------------------------------

class TestChunkRst(unittest.TestCase):

    def setUp(self):
        self.chunks = chunk_rst(RST_FIXTURE, _REL)

    def test_splits_into_expected_headings(self):
        headings = [c["heading"] for c in self.chunks]
        self.assertIn("Introduction",     headings)
        self.assertIn("Fields Reference", headings)
        self.assertIn("Methods",          headings)

    def test_images_absent_from_chunk_text(self):
        combined = " ".join(c["text"] for c in self.chunks)
        self.assertNotIn(".. image::", combined)
        self.assertNotIn(":alt:",      combined)

    def test_tiny_chunks_are_skipped(self):
        # "Short." is well below _MIN_CHUNK_CHARS
        tiny   = "Tiny\n====\n\nShort.\n"
        chunks = chunk_rst(tiny, "test.rst")
        self.assertFalse(any(c["heading"] == "Tiny" for c in chunks))

    def test_anchor_is_slugified_heading(self):
        fields = next(c for c in self.chunks if c["heading"] == "Fields Reference")
        self.assertEqual(fields["anchor"], "fields-reference")

    def test_methods_anchor(self):
        methods = next(c for c in self.chunks if c["heading"] == "Methods")
        self.assertEqual(methods["anchor"], "methods")

    def test_rel_path_preserved_in_every_chunk(self):
        for c in self.chunks:
            self.assertEqual(c["rel_path"], _REL)

    def test_no_false_heading_from_code_block(self):
        # Indented underline-like lines must NOT be detected as headings
        text = (
            "Normal paragraph with enough content to exceed the minimum.\n\n"
            "    indented underline lookalike\n"
            "    ============================\n\n"
            "More text here to pad length and avoid tiny-chunk cutoff.\n"
        )
        chunks = chunk_rst(text, "x.rst")
        # Only a single chunk: the filename-based initial heading
        self.assertTrue(all("indented" not in c["heading"] for c in chunks))

    def test_empty_file_returns_no_chunks(self):
        self.assertEqual(chunk_rst("", "empty.rst"), [])

    def test_file_with_only_heading_returns_no_chunks(self):
        self.assertEqual(chunk_rst("Title\n=====\n", "t.rst"), [])


# ---------------------------------------------------------------------------
# canonical_url
# ---------------------------------------------------------------------------

class TestCanonicalUrl(unittest.TestCase):

    def test_standard_developer_path(self):
        url = canonical_url("content/developer/reference/backend/orm.rst", "18")
        self.assertEqual(
            url,
            "https://www.odoo.com/documentation/18.0/developer/reference/backend/orm.html",
        )

    def test_with_anchor(self):
        url = canonical_url(
            "content/developer/reference/backend/orm.rst", "18", "create-method"
        )
        self.assertEqual(
            url,
            "https://www.odoo.com/documentation/18.0/"
            "developer/reference/backend/orm.html#create-method",
        )

    def test_path_without_content_prefix(self):
        url = canonical_url("developer/reference/orm.rst", "17")
        self.assertEqual(
            url,
            "https://www.odoo.com/documentation/17.0/developer/reference/orm.html",
        )

    def test_bare_int_version_normalized_to_point_zero(self):
        # "18" → "18.0", "17" → "17.0"
        self.assertIn("/18.0/", canonical_url("content/developer/x.rst", "18"))
        self.assertIn("/17.0/", canonical_url("content/developer/x.rst", "17"))

    def test_dotted_version_not_double_suffixed(self):
        # "18.0" must stay "18.0", not become "18.0.0"
        url = canonical_url("content/developer/x.rst", "18.0")
        self.assertIn("/18.0/", url)
        self.assertNotIn("/18.0.0/", url)

    def test_no_anchor_omits_hash(self):
        url = canonical_url("content/developer/x.rst", "18")
        self.assertNotIn("#", url)


# ---------------------------------------------------------------------------
# build_index + query_index
# ---------------------------------------------------------------------------

class TestBuildAndQueryIndex(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.chunks = chunk_rst(RST_FIXTURE, _REL)
        cls.index  = build_index(cls.chunks)

    def test_index_has_required_keys(self):
        self.assertIn("idf",   self.index)
        self.assertIn("docs",  self.index)
        self.assertIn("_meta", self.index)

    def test_doc_count_matches_chunks(self):
        self.assertEqual(self.index["_meta"]["doc_count"], len(self.chunks))
        self.assertEqual(len(self.index["docs"]), len(self.chunks))

    def test_vocab_is_non_empty(self):
        self.assertGreater(self.index["_meta"]["vocab_size"], 10)

    def test_each_doc_has_required_fields(self):
        for d in self.index["docs"]:
            for key in ("rel_path", "heading", "anchor", "vec", "preview"):
                self.assertIn(key, d, f"doc missing key: {key}")

    def test_field_query_ranks_fields_chunk_first(self):
        results = query_index(self.index, "field types Char Integer Float", top=5)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["heading"], "Fields Reference")

    def test_method_query_ranks_methods_chunk_first(self):
        results = query_index(self.index, "api.model api.depends computed", top=5)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["heading"], "Methods")

    def test_nonsense_query_returns_empty(self):
        results = query_index(self.index, "xyzzy plugh frobnicate zork", top=5)
        # Tokens absent from vocab → cosine 0 → filtered out
        self.assertEqual(results, [])

    def test_result_keys(self):
        results = query_index(self.index, "ORM database access", top=3)
        if results:
            r = results[0]
            for key in ("heading", "rel_path", "anchor", "score", "preview"):
                self.assertIn(key, r)

    def test_scores_are_between_zero_and_one(self):
        results = query_index(self.index, "ORM model fields methods", top=10)
        for r in results:
            self.assertGreater(r["score"], 0.0)
            self.assertLessEqual(r["score"], 1.0)

    def test_build_empty_chunks(self):
        idx = build_index([])
        self.assertEqual(idx["docs"], [])
        self.assertEqual(idx["idf"],  {})
        self.assertEqual(query_index(idx, "anything"), [])


if __name__ == "__main__":
    unittest.main()
