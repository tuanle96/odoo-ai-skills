"""
Unit tests for the pure `analyze_source` heuristic in model_brief.py.

model_brief.py is meant to be piped into `odoo-bin shell`: it runs at module
level and depends on the injected `env` global, so it can't be imported here.
Instead we lift just the `HOOK_RE` constant and the `analyze_source` function
out of the source via AST and exec them in an isolated namespace — this tests
the real code without refactoring the shell script.
"""
import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_BRIEF = REPO_ROOT / "skills" / "odoo-introspect" / "scripts" / "model_brief.py"


def _load_analyze_source():
    tree = ast.parse(MODEL_BRIEF.read_text())
    ns = {"re": re}
    wanted_assign = "HOOK_RE"
    wanted_func = "analyze_source"
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == wanted_assign for t in node.targets
        ):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<brief>", "exec"), ns)
        if isinstance(node, ast.FunctionDef) and node.name == wanted_func:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<brief>", "exec"), ns)
    return ns[wanted_func]


analyze_source = _load_analyze_source()


class AnalyzeSourceTests(unittest.TestCase):
    def test_empty_source_returns_defaults(self):
        out = analyze_source("")
        self.assertFalse(out["has_super"])
        self.assertIsNone(out["super_position"])
        self.assertEqual(out["hooks_called"], [])
        self.assertTrue(out["heuristic"])

    def test_no_super_detected(self):
        src = "def write(self, vals):\n    self._do_thing()\n    return True\n"
        out = analyze_source(src)
        self.assertFalse(out["has_super"])
        self.assertEqual(out["hooks_called"], ["_do_thing"])

    def test_super_detected_and_hooks_collected(self):
        src = (
            "def write(self, vals):\n"
            "    self._pre_hook()\n"
            "    res = super().write(vals)\n"
            "    self._post_hook()\n"
            "    return res\n"
        )
        out = analyze_source(src)
        self.assertTrue(out["has_super"])
        self.assertEqual(out["hooks_called"], ["_post_hook", "_pre_hook"])

    def test_early_super_position(self):
        src = "def f(self):\n" + "    x = super().f()\n" + "    a = 1\n" * 10
        out = analyze_source(src)
        self.assertIn("early", out["super_position"])

    def test_late_super_position(self):
        src = "def f(self):\n" + "    a = 1\n" * 10 + "    return super().f()\n"
        out = analyze_source(src)
        self.assertIn("late", out["super_position"])

    def test_return_before_super_flagged(self):
        src = (
            "def f(self):\n"
            "    if self.skip:\n"
            "        return False\n"
            "    return super().f()\n"
        )
        out = analyze_source(src)
        self.assertTrue(out["returns_before_super"])
        self.assertIn("conditional-or-early-return present", out["super_position"])

    def test_commented_super_is_ignored(self):
        # A '#'-commented super() must not count as a real super call.
        src = "def f(self):\n    # res = super().f()\n    self._hook()\n    return 1\n"
        out = analyze_source(src)
        self.assertFalse(out["has_super"])


if __name__ == "__main__":
    unittest.main()
