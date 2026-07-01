"""
Unit tests for mutation_smoke.py — pure-function tests (Part A generation +
classification + assembly). Part B (run/make_odoo_test_runner) shells out to
odoo-bin and is intentionally NOT covered here (see module docstring).
"""
import ast
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import mutation_smoke as ms  # noqa: E402


def _compiles(source):
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


# ---------------------------------------------------------------------------
# generate_mutants — operator coverage
# ---------------------------------------------------------------------------

class TestGenerateMutantsEq(unittest.TestCase):
    SRC = (
        "def foo(x):\n"
        "    if x == 1:\n"
        "        return True\n"
        "    return False\n"
    )

    def test_eq_to_neq_fires_and_compiles(self):
        mutants = ms.generate_mutants(self.SRC, {2}, target_id="t1", file="foo.py")
        eq_mutants = [m for m in mutants if m["operator"] == "eq_to_neq"]
        self.assertEqual(len(eq_mutants), 1)
        m = eq_mutants[0]
        self.assertIn("x != 1", m["mutated"])
        self.assertTrue(_compiles(m["mutated"]))
        self.assertEqual(m["line"], 2)
        self.assertEqual(m["target_id"], "t1")
        self.assertEqual(m["file"], "foo.py")

    def test_not_eq_flips_back_to_eq(self):
        src = "def foo(x):\n    if x != 1:\n        return True\n"
        mutants = ms.generate_mutants(src, {2})
        self.assertEqual(len(mutants), 1)
        self.assertIn("x == 1", mutants[0]["mutated"])


class TestGenerateMutantsLt(unittest.TestCase):
    def test_lt_to_ge_fires(self):
        src = "def foo(a, b):\n    if a < b:\n        return 1\n"
        mutants = ms.generate_mutants(src, {2})
        lt_mutants = [m for m in mutants if m["operator"] == "lt_to_ge"]
        self.assertEqual(len(lt_mutants), 1)
        self.assertIn("a >= b", lt_mutants[0]["mutated"])
        self.assertTrue(_compiles(lt_mutants[0]["mutated"]))

    def test_gt_lte_gte_flip_correctly(self):
        cases = [
            ("if a > b:", "a <= b"),
            ("if a <= b:", "a > b"),
            ("if a >= b:", "a < b"),
        ]
        for cond, expect in cases:
            src = f"def foo(a, b):\n    {cond}\n        return 1\n"
            mutants = ms.generate_mutants(src, {2})
            self.assertEqual(len(mutants), 1, cond)
            self.assertIn(expect, mutants[0]["mutated"], cond)


class TestGenerateMutantsAnd(unittest.TestCase):
    def test_and_to_or_fires(self):
        src = "def foo(a, b):\n    if a and b:\n        return 1\n"
        mutants = ms.generate_mutants(src, {2})
        and_mutants = [m for m in mutants if m["operator"] == "and_to_or"]
        self.assertEqual(len(and_mutants), 1)
        self.assertIn("a or b", and_mutants[0]["mutated"])
        self.assertTrue(_compiles(and_mutants[0]["mutated"]))

    def test_or_to_and_fires(self):
        src = "def foo(a, b):\n    if a or b:\n        return 1\n"
        mutants = ms.generate_mutants(src, {2})
        self.assertEqual(len(mutants), 1)
        self.assertIn("a and b", mutants[0]["mutated"])


class TestGenerateMutantsIn(unittest.TestCase):
    def test_in_to_notin_fires(self):
        src = "def foo(a, b):\n    if a in b:\n        return 1\n"
        mutants = ms.generate_mutants(src, {2})
        in_mutants = [m for m in mutants if m["operator"] == "in_to_notin"]
        self.assertEqual(len(in_mutants), 1)
        self.assertIn("a not in b", in_mutants[0]["mutated"])
        self.assertTrue(_compiles(in_mutants[0]["mutated"]))

    def test_not_in_to_in_fires(self):
        src = "def foo(a, b):\n    if a not in b:\n        return 1\n"
        mutants = ms.generate_mutants(src, {2})
        self.assertEqual(len(mutants), 1)
        self.assertIn("a in b", mutants[0]["mutated"])
        self.assertNotIn("not in", mutants[0]["mutated"])


class TestGenerateMutantsBool(unittest.TestCase):
    def test_true_to_false_fires(self):
        src = "def foo():\n    flag = True\n    return flag\n"
        mutants = ms.generate_mutants(src, {2})
        bool_mutants = [m for m in mutants if m["operator"] == "true_to_false"]
        self.assertEqual(len(bool_mutants), 1)
        self.assertIn("flag = False", bool_mutants[0]["mutated"])
        self.assertTrue(_compiles(bool_mutants[0]["mutated"]))

    def test_false_to_true_fires(self):
        src = "def foo():\n    flag = False\n    return flag\n"
        mutants = ms.generate_mutants(src, {2})
        self.assertEqual(len(mutants), 1)
        self.assertIn("flag = True", mutants[0]["mutated"])


class TestGenerateMutantsRaise(unittest.TestCase):
    def test_remove_raise_fires(self):
        src = (
            "def foo(self, x):\n"
            "    if not x:\n"
            "        raise UserError('bad')\n"
            "    return x\n"
        )
        mutants = ms.generate_mutants(src, {3}, target_id="t2", file="m.py")
        raise_mutants = [m for m in mutants if m["operator"] == "remove_raise"]
        self.assertEqual(len(raise_mutants), 1)
        m = raise_mutants[0]
        self.assertNotIn("raise UserError", m["mutated"])
        self.assertIn("pass  # mutated: raise removed", m["mutated"])
        self.assertTrue(_compiles(m["mutated"]))
        self.assertEqual(m["line"], 3)

    def test_remove_raise_preserves_indent_and_compiles_multiline(self):
        src = (
            "def foo(self, x):\n"
            "    if not x:\n"
            "        raise UserError(\n"
            "            'bad'\n"
            "        )\n"
            "    return x\n"
        )
        mutants = ms.generate_mutants(src, {3})
        self.assertEqual(len(mutants), 1)
        self.assertTrue(_compiles(mutants[0]["mutated"]))
        self.assertIn("        pass  # mutated: raise removed", mutants[0]["mutated"])


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------

class TestSkipGuards(unittest.TestCase):
    def test_super_call_not_mutated(self):
        src = (
            "def create(self, vals):\n"
            "    return super().create(vals) == True\n"
        )
        mutants = ms.generate_mutants(src, {2}, file="m.py")
        self.assertEqual(mutants, [])
        reason = ms._skip_reason("    return super().create(vals) == True", "m.py")
        self.assertIsNotNone(reason)
        self.assertIn("super", reason)

    def test_sudo_call_not_mutated(self):
        src = "def foo(self):\n    if self.sudo().exists() == True:\n        return 1\n"
        mutants = ms.generate_mutants(src, {2}, file="m.py")
        self.assertEqual(mutants, [])
        self.assertIn("sudo", ms._skip_reason("    if self.sudo().exists() == True:", "m.py"))

    def test_migrations_path_not_mutated(self):
        src = "def migrate(cr, version):\n    if version == '1.0':\n        pass\n"
        mutants = ms.generate_mutants(src, {2}, file="addons/x/migrations/18.0/post.py")
        self.assertEqual(mutants, [])

    def test_xml_path_not_mutated(self):
        src = "def foo(x):\n    if x == 1:\n        return True\n"
        mutants = ms.generate_mutants(src, {2}, file="views/foo.xml")
        self.assertEqual(mutants, [])


# ---------------------------------------------------------------------------
# changed_lines scoping
# ---------------------------------------------------------------------------

class TestChangedLinesScoping(unittest.TestCase):
    def test_line_outside_changed_lines_not_mutated(self):
        src = (
            "def foo(a, b):\n"
            "    if a == 1:\n"
            "        return True\n"
            "    if b == 2:\n"
            "        return False\n"
        )
        # Only line 4 (b == 2) is "changed" — line 2 (a == 1) must be left alone.
        mutants = ms.generate_mutants(src, {4})
        self.assertEqual(len(mutants), 1)
        self.assertEqual(mutants[0]["line"], 4)
        self.assertIn("b != 2", mutants[0]["mutated"])
        self.assertIn("a == 1", mutants[0]["mutated"])  # untouched line preserved

    def test_empty_changed_lines_returns_nothing(self):
        src = "def foo(a):\n    if a == 1:\n        return True\n"
        self.assertEqual(ms.generate_mutants(src, set()), [])
        self.assertEqual(ms.generate_mutants(src, None), [])


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------

class TestMalformedSource(unittest.TestCase):
    def test_syntax_error_returns_empty_list_no_raise(self):
        bad = "def foo(:\n    this is not python\n"
        try:
            result = ms.generate_mutants(bad, {1, 2})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"generate_mutants raised on malformed source: {exc}")
        self.assertEqual(result, [])

    def test_non_string_source_returns_empty_list(self):
        self.assertEqual(ms.generate_mutants(None, {1}), [])
        self.assertEqual(ms.generate_mutants("", {1}), [])


# ---------------------------------------------------------------------------
# classify_results / build_report
# ---------------------------------------------------------------------------

def _mutant(status, mid="m1"):
    return {"id": mid, "operator": "eq_to_neq", "line": 1, "original": "x",
            "mutated": "y", "status": status, "killed_by": [] if status != "killed" else ["t"]}


class TestClassifyResults(unittest.TestCase):
    def test_counts_roll_up(self):
        mutants = [_mutant("killed", "a"), _mutant("killed", "b"), _mutant("survived", "c")]
        counts = ms.classify_results(mutants)
        self.assertEqual(counts, {"mutants": 3, "survived": 1, "killed": 2, "skipped": 0})

    def test_missing_status_treated_as_skipped(self):
        mutants = [{"id": "a"}]
        counts = ms.classify_results(mutants)
        self.assertEqual(counts["skipped"], 1)


class TestBuildReport(unittest.TestCase):
    def test_survived_blocks(self):
        targets_results = [{
            "target_id": "t1", "file": "f.py",
            "mutants": [_mutant("killed", "a"), _mutant("killed", "b"), _mutant("survived", "c")],
        }]
        report = ms.build_report(targets_results)
        self.assertEqual(report["summary"]["survived"], 1)
        self.assertEqual(report["summary"]["killed"], 2)
        self.assertEqual(report["summary"]["mutants"], 3)
        self.assertEqual(report["summary"]["targets"], 1)
        self.assertEqual(report["decision"], "block")

    def test_all_killed_passes(self):
        targets_results = [{
            "target_id": "t1", "file": "f.py",
            "mutants": [_mutant("killed", "a"), _mutant("killed", "b")],
        }]
        report = ms.build_report(targets_results)
        self.assertEqual(report["summary"]["survived"], 0)
        self.assertEqual(report["decision"], "pass")

    def test_empty_input_is_pass_not_block(self):
        report = ms.build_report([])
        self.assertEqual(report["summary"], {"targets": 0, "mutants": 0, "survived": 0,
                                              "killed": 0, "skipped": 0})
        self.assertEqual(report["decision"], "pass")

    def test_schema_shape(self):
        report = ms.build_report([{"target_id": "t1", "file": "f.py",
                                    "mutants": [_mutant("survived")]}])
        self.assertIn("summary", report)
        self.assertIsInstance(report["summary"]["survived"], int)
        self.assertIn("decision", report)
        self.assertIn(report["decision"], ("pass", "block"))
        self.assertIn("targets", report)
        self.assertIn("_warnings", report)


# ---------------------------------------------------------------------------
# Assembly mode
# ---------------------------------------------------------------------------

class TestAssembleTargetsResults(unittest.TestCase):
    SRC = "def foo(x):\n    if x == 1:\n        return True\n"

    def _file_reader(self, path):
        return self.SRC if path == "foo.py" else None

    def test_results_map_overlay_matches_build_report(self):
        targets_doc = {"targets": [{
            "id": "t1", "file": "foo.py", "model": "m", "method": "foo",
            "changed_exec_lines": [2],
        }]}
        warnings = []
        targets_results = ms._assemble_targets_results(
            targets_doc, {}, self._file_reader, warnings)
        self.assertEqual(len(targets_results), 1)
        mutants = targets_results[0]["mutants"]
        self.assertEqual(len(mutants), 1)
        mid = mutants[0]["id"]

        # Now overlay a results map marking that mutant survived.
        results_map = {"t1": {mid: {"status": "survived", "killed_by": []}}}
        warnings2 = []
        targets_results2 = ms._assemble_targets_results(
            targets_doc, results_map, self._file_reader, warnings2)
        report = ms.build_report(targets_results2)
        self.assertEqual(report["summary"]["survived"], 1)
        self.assertEqual(report["decision"], "block")

    def test_unmapped_mutant_defaults_to_skipped_not_killed(self):
        targets_doc = {"targets": [{
            "id": "t1", "file": "foo.py", "changed_exec_lines": [2],
        }]}
        warnings = []
        targets_results = ms._assemble_targets_results(
            targets_doc, {}, self._file_reader, warnings)
        report = ms.build_report(targets_results)
        self.assertEqual(report["summary"]["survived"], 0)
        self.assertEqual(report["summary"]["skipped"], 1)
        self.assertEqual(report["decision"], "pass")

    def test_list_form_results_doc_passed_through(self):
        targets_results_in = [{"target_id": "t1", "file": "f.py",
                                "mutants": [_mutant("survived")]}]
        out = ms._assemble_targets_results({"targets": []}, targets_results_in,
                                            self._file_reader, [])
        self.assertIs(out, targets_results_in)
        report = ms.build_report(out)
        self.assertEqual(report["decision"], "block")


if __name__ == "__main__":
    unittest.main()
