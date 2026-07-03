"""
Unit tests for facts.py — compact instance facts for agent context.

facts.py is a SHELL script (run inside `odoo-bin shell`), but its env-dependent
work is gated behind `if "env" in globals()`. So it imports WITHOUT Odoo, and its
pure helpers (module_hash, compact_field/compact_fields, selection_keys, acl_perms,
mentions_company, cap_str, module_of_xmlid, as_list, parse_buttons) are exercised
here with plain in-memory data. No Odoo dependency; no env is defined.
"""
import sys
import unittest
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"

_spec = importlib.util.spec_from_file_location("facts", SCRIPTS_DIR / "facts.py")
facts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(facts)  # import-safe: run() is skipped (no `env`)


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------

class ImportSafetyTests(unittest.TestCase):
    def test_imports_without_env(self):
        self.assertFalse(hasattr(facts, "env"))
        for name in ("run", "module_hash", "compact_field", "parse_buttons"):
            self.assertTrue(hasattr(facts, name), name)


# ---------------------------------------------------------------------------
# module_hash
# ---------------------------------------------------------------------------

class ModuleHashTests(unittest.TestCase):
    def test_deterministic_and_counts(self):
        digest, count = facts.module_hash([("sale", "16.0"), ("base", "16.0")])
        self.assertEqual(count, 2)
        self.assertEqual(len(digest), 64)
        self.assertEqual(facts.module_hash([("sale", "16.0"), ("base", "16.0")])[0],
                         digest)

    def test_order_independent(self):
        a = facts.module_hash([("a", "1"), ("b", "2")])[0]
        b = facts.module_hash([("b", "2"), ("a", "1")])[0]
        self.assertEqual(a, b)

    def test_version_change_changes_hash(self):
        a = facts.module_hash([("sale", "16.0")])[0]
        b = facts.module_hash([("sale", "17.0")])[0]
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------
# selection_keys
# ---------------------------------------------------------------------------

class SelectionKeysTests(unittest.TestCase):
    def test_pairs_return_keys_only(self):
        self.assertEqual(
            facts.selection_keys([("draft", "Draft"), ("done", "Done")]),
            ["draft", "done"])

    def test_method_string_returns_none(self):
        self.assertIsNone(facts.selection_keys("_compute_states"))

    def test_none_and_empty(self):
        self.assertIsNone(facts.selection_keys(None))
        self.assertIsNone(facts.selection_keys([]))

    def test_callable_returns_none(self):
        self.assertIsNone(facts.selection_keys(lambda self: []))


# ---------------------------------------------------------------------------
# compact_field / compact_fields
# ---------------------------------------------------------------------------

class CompactFieldTests(unittest.TestCase):
    def test_basic_char(self):
        out = facts.compact_field(
            {"type": "char", "required": True, "store": True})
        self.assertEqual(out["type"], "char")
        self.assertTrue(out["required"])
        self.assertFalse(out["compute"])
        self.assertFalse(out["related"])
        self.assertNotIn("relation", out)
        self.assertNotIn("selection", out)

    def test_relation_field(self):
        out = facts.compact_field({"type": "many2one", "relation": "res.partner"})
        self.assertEqual(out["relation"], "res.partner")

    def test_selection_field_keys_only(self):
        out = facts.compact_field(
            {"type": "selection", "selection": [("a", "A"), ("b", "B")]})
        self.assertEqual(out["selection"], ["a", "b"])

    def test_compute_and_related_bools(self):
        out = facts.compact_field(
            {"type": "float", "compute": "_compute_total", "related": ("order_id", "amt")})
        self.assertTrue(out["compute"])
        self.assertTrue(out["related"])


class CompactFieldsTests(unittest.TestCase):
    def test_skips_technical_fields(self):
        fields = {
            "id": {"type": "integer"},
            "create_date": {"type": "datetime"},
            "display_name": {"type": "char"},
            "name": {"type": "char", "required": True},
            "partner_id": {"type": "many2one", "relation": "res.partner"},
        }
        out = facts.compact_fields(fields)
        self.assertNotIn("id", out)
        self.assertNotIn("create_date", out)
        self.assertNotIn("display_name", out)
        self.assertIn("name", out)
        self.assertEqual(out["partner_id"]["relation"], "res.partner")


# ---------------------------------------------------------------------------
# acl_perms
# ---------------------------------------------------------------------------

class AclPermsTests(unittest.TestCase):
    def test_all_perms(self):
        row = {"perm_create": True, "perm_read": True,
               "perm_write": True, "perm_unlink": True}
        self.assertEqual(facts.acl_perms(row), "crwu")

    def test_read_write_only(self):
        self.assertEqual(
            facts.acl_perms({"perm_read": True, "perm_write": True}), "-rw-")

    def test_none(self):
        self.assertEqual(facts.acl_perms({}), "----")


# ---------------------------------------------------------------------------
# mentions_company / cap_str / module_of_xmlid / as_list
# ---------------------------------------------------------------------------

class MiscHelperTests(unittest.TestCase):
    def test_mentions_company(self):
        self.assertTrue(facts.mentions_company(
            "[('company_id','in',company_ids)]"))
        self.assertFalse(facts.mentions_company("[('state','=','draft')]"))
        self.assertFalse(facts.mentions_company(None))

    def test_cap_str(self):
        self.assertEqual(facts.cap_str("short", 10), "short")
        capped = facts.cap_str("x" * 20, 5)
        self.assertEqual(capped, "xxxxx…")
        self.assertEqual(facts.cap_str(None, 5), "")

    def test_module_of_xmlid(self):
        self.assertEqual(facts.module_of_xmlid("sale.view_order_form"), "sale")
        self.assertIsNone(facts.module_of_xmlid(None))
        self.assertIsNone(facts.module_of_xmlid("no_dot_here"))

    def test_as_list(self):
        self.assertEqual(facts.as_list(None), [])
        self.assertEqual(facts.as_list("sale.order"), ["sale.order"])
        self.assertEqual(facts.as_list(["a", "b"]), ["a", "b"])


# ---------------------------------------------------------------------------
# parse_buttons
# ---------------------------------------------------------------------------

class ParseButtonsTests(unittest.TestCase):
    ARCH = (
        "<form>"
        "<header>"
        "<button name='action_confirm' type='object' string='Confirm'/>"
        "<button name='%(act_win)d' type='action' string='Open'/>"
        "<button string='no-name-skipped'/>"
        "</header>"
        "<field name='state'/>"
        "</form>"
    )

    def test_extracts_named_buttons(self):
        buttons = facts.parse_buttons(self.ARCH)
        names = [b["name"] for b in buttons]
        self.assertIn("action_confirm", names)
        self.assertEqual(len(buttons), 2)  # the string-only button is skipped

    def test_button_shape(self):
        confirm = next(b for b in facts.parse_buttons(self.ARCH)
                       if b["name"] == "action_confirm")
        self.assertEqual(confirm["type"], "object")
        self.assertEqual(confirm["string"], "Confirm")

    def test_malformed_arch_returns_empty(self):
        self.assertEqual(facts.parse_buttons("<form><unclosed>"), [])

    def test_empty_form_no_buttons(self):
        self.assertEqual(facts.parse_buttons("<form><field name='x'/></form>"), [])


if __name__ == "__main__":
    unittest.main()
