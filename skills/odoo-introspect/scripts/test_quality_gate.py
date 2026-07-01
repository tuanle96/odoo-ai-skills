"""
Static test-quality linter (Layer I) — LOCAL, no Odoo, no execution.

An AI agent under pressure to make a "test gate" go green will sometimes write a
test that passes without ever proving anything: ``self.assertTrue(True)``, a
test method with no assert at all, a ``try/except: pass`` around the very call
being tested, ``assertRaises(Exception)`` (too broad to prove the RIGHT error
fired), or mocking the Odoo model under test so production code never actually
runs. This scans test SOURCE with ``ast`` (never imports/executes it) and flags
those patterns before they reach a green CI run.

Also checks the Odoo test-discovery trap: a ``test_*.py`` file that exists but
is never imported from ``tests/__init__.py`` is silently never collected/run —
a permanently-green test suite that never ran the new test.

Pure module-level functions (no Odoo) — unit-tested. Runs as a normal script:

    python3 test_quality_gate.py <path> [<path>...]     # prints JSON to stdout
    odoo-ai test-quality <path...>                       # via the CLI

A directory path is walked recursively for ``test_*.py`` files; if a
``tests/__init__.py`` is found under a walked directory, its imports are also
checked. Exit code is always 0 — the blocking count is in the summary; the
caller (e.g. deploy_gate) decides.

Output: JSON {summary, findings, _warnings}. Each finding:
    {"file", "line", "rule", "severity": "blocking"|"warning", "message"}

Design note on assertIsNotNone(...)/assertTrue(<call>) as the only assert in a
test: this is real but WEAK coverage (something ran, nothing about its result
was checked) — it is a ``weak_only_assert`` WARNING, not a blocking finding.
Only a literally-tautological assert (``assertTrue(True)``, ``assertEqual(x,
x)``, ...) is blocking ``vacuous_assert``.
"""
import ast
import json
import os
import sys
from pathlib import Path

_EXCLUDED_PATCH_KEYWORDS = ("requests", "smtplib", "socket", "http", "sandbox", "payment")
_WEAK_LOGGING_ATTRS = frozenset({
    "error", "warning", "info", "debug", "exception", "warn", "critical", "log",
})
_MOCK_CTOR_NAMES = frozenset({"Mock", "MagicMock"})


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo needed — unit-testable)
# ---------------------------------------------------------------------------

def _f(path, line, rule, severity, message):
    return {"file": path, "line": line, "rule": rule, "severity": severity, "message": message}


def _is_const(node, value):
    return isinstance(node, ast.Constant) and node.value is value


def _is_assert_call(node):
    """True for a `self.assertXxx(...)` / `self.failXxx(...)` method call."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and (node.func.attr.startswith("assert") or node.func.attr == "fail"))


def _is_weak_assert(node):
    """assertIsNotNone(...) always weak; assertTrue(x) weak unless x is a Compare
    (an equality/relational check is a real assertion, not just truthiness)."""
    if isinstance(node, ast.Assert):
        return False
    attr = node.func.attr
    if attr == "assertIsNotNone":
        return True
    if attr == "assertTrue":
        arg0 = node.args[0] if node.args else None
        return not isinstance(arg0, ast.Compare)
    return False


# Odoo business exceptions: a test that silently swallows one of these is hiding
# the exact runtime failure the method under test raises (Oracle: `except UserError:
# pass` was a real bypass). Treat them like a broad catch for the swallow rule.
_SWALLOWABLE_EXC = frozenset({
    "Exception", "BaseException",
    "UserError", "ValidationError", "AccessError", "AccessDenied",
    "MissingError", "RedirectWarning", "CacheMiss", "except_orm",
})


def _is_broad_exception_type(t):
    if isinstance(t, ast.Name):
        return t.id in _SWALLOWABLE_EXC
    if isinstance(t, ast.Attribute):
        # exceptions.UserError, odoo.exceptions.ValidationError, ...
        return t.attr in _SWALLOWABLE_EXC
    if isinstance(t, ast.Tuple):
        return any(_is_broad_exception_type(e) for e in t.elts)
    return False


def _is_noop_handler_body(body):
    """True when every statement is `pass`, `print(...)`, or a `.<logging-attr>(...)`
    call (e.g. _logger.error(...)) — i.e. the exception is observed, not handled."""
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if isinstance(call.func, ast.Name) and call.func.id == "print":
                continue
            if isinstance(call.func, ast.Attribute) and call.func.attr in _WEAK_LOGGING_ATTRS:
                continue
            return False
        return False
    return True


def _is_patch_object_call(call):
    """True for `patch.object(...)` / `mock.patch.object(...)`."""
    fn = call.func
    if not (isinstance(fn, ast.Attribute) and fn.attr == "object"):
        return False
    base = fn.value
    return ((isinstance(base, ast.Name) and base.id == "patch")
            or (isinstance(base, ast.Attribute) and base.attr == "patch"))


def _is_patch_call(call):
    """True for `patch(...)` / `mock.patch(...)` (NOT the `.object` form)."""
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id == "patch"
    if isinstance(fn, ast.Attribute):
        return fn.attr == "patch"
    return False


def _is_env_subscript(node):
    """True for `self.env[...]` (or `<anything>.env[...]`)."""
    return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
            and node.value.attr == "env")


def _patch_target_flagged(arg0):
    """True when a `patch("...")` string target clearly names Odoo model code and
    isn't an allowed external-service mock (requests/smtplib/socket/http/payment)."""
    if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)):
        return False
    s = arg0.value.lower()
    if any(k in s for k in _EXCLUDED_PATCH_KEYWORDS):
        return False
    return "odoo.addons" in s or ".models." in s


def _is_mock_ctor(func):
    if isinstance(func, ast.Name):
        return func.id in _MOCK_CTOR_NAMES
    if isinstance(func, ast.Attribute):
        return func.attr in _MOCK_CTOR_NAMES
    return False


def _assign_target_name(tgt):
    if isinstance(tgt, ast.Name):
        return tgt.id
    if isinstance(tgt, ast.Attribute):
        return tgt.attr
    return None


def _scan_mock_usage(path, tree):
    """Module-wide scan for mock-the-model-under-test patterns (decorators, `with`
    context managers, and env/recordset replaced by a bare Mock/MagicMock)."""
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_patch_object_call(node) and node.args and _is_env_subscript(node.args[0]):
                findings.append(_f(path, node.lineno, "mock_model_under_test", "blocking",
                    "patch.object() targets self.env[...] — the model under test is "
                    "mocked, so production ORM behaviour never runs"))
            elif _is_patch_call(node) and node.args and _patch_target_flagged(node.args[0]):
                findings.append(_f(path, node.lineno, "mock_model_under_test", "blocking",
                    f"patch({node.args[0].value!r}) targets Odoo model code — the "
                    "behaviour under test is mocked instead of exercised"))
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                and _is_mock_ctor(node.value.func):
            for tgt in node.targets:
                name = _assign_target_name(tgt)
                if name and "env" in name.lower():
                    findings.append(_f(path, node.lineno, "mock_model_under_test", "blocking",
                        f"{name} = Mock()/MagicMock() — an Odoo env/recordset is "
                        "replaced by a mock instead of the real ORM"))
                    break
    return findings


def _analyze_test_function(path, node):
    """Per-`def test_*` analysis: vacuous/no/weak asserts, broad assertRaises,
    swallowed exceptions. Returns a list of findings."""
    findings = []
    assert_nodes = [n for n in ast.walk(node)
                     if isinstance(n, ast.Assert) or _is_assert_call(n)]
    vacuous_ids = set()

    for n in assert_nodes:
        if isinstance(n, ast.Assert):
            if _is_const(n.test, True):
                findings.append(_f(path, n.lineno, "vacuous_assert", "blocking",
                    "`assert True` always passes — vacuous, catches nothing"))
                vacuous_ids.add(id(n))
            continue

        attr = n.func.attr
        args = n.args
        if attr == "assertTrue" and args and _is_const(args[0], True):
            findings.append(_f(path, n.lineno, "vacuous_assert", "blocking",
                "assertTrue(True) always passes — vacuous, catches nothing"))
            vacuous_ids.add(id(n))
        elif attr == "assertFalse" and args and _is_const(args[0], False):
            findings.append(_f(path, n.lineno, "vacuous_assert", "blocking",
                "assertFalse(False) always passes — vacuous, catches nothing"))
            vacuous_ids.add(id(n))
        elif attr == "assertEqual" and len(args) >= 2 and ast.dump(args[0]) == ast.dump(args[1]):
            findings.append(_f(path, n.lineno, "vacuous_assert", "blocking",
                "assertEqual(x, x) compares an expression to itself — always true"))
            vacuous_ids.add(id(n))
        elif attr == "assertIn" and len(args) >= 2 and isinstance(args[1], (ast.List, ast.Tuple, ast.Set)) \
                and any(ast.dump(args[0]) == ast.dump(elt) for elt in args[1].elts):
            findings.append(_f(path, n.lineno, "vacuous_assert", "blocking",
                "assertIn(x, [x, ...]) — the container was built from x, always true"))
            vacuous_ids.add(id(n))
        elif attr == "assertRaises" and args and isinstance(args[0], ast.Name) \
                and args[0].id in ("Exception", "BaseException"):
            findings.append(_f(path, n.lineno, "broad_assertRaises", "blocking",
                f"assertRaises({args[0].id}) is too broad to prove the RIGHT error "
                "fired — use assertRaisesRegex(SpecificError, \"...\")"))

    if not assert_nodes:
        findings.append(_f(path, node.lineno, "no_assertion", "blocking",
            f"{node.name}() calls production code but never asserts — it can never fail"))
    else:
        remaining = [n for n in assert_nodes if id(n) not in vacuous_ids]
        if remaining and all(_is_weak_assert(n) for n in remaining):
            findings.append(_f(path, remaining[0].lineno, "weak_only_assert", "warning",
                f"{node.name}() only checks assertIsNotNone/assertTrue(<call>) — "
                "confirms something ran, not that it behaved correctly"))

    for n in ast.walk(node):
        if not isinstance(n, ast.Try):
            continue
        for h in n.handlers:
            if (h.type is None or _is_broad_exception_type(h.type)) and _is_noop_handler_body(h.body):
                findings.append(_f(path, h.lineno, "swallowed_exception", "blocking",
                    "except (Exception/bare) with a no-op handler silently swallows "
                    "failures from the code under test"))

    return findings


def lint_source(source, filename):
    """AST-lint one test file's source. Never raises on SyntaxError.

    Returns a list of findings (see module docstring for shape).
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [_f(filename, exc.lineno or 1, "parse_error", "warning",
                    f"{filename}: SyntaxError: {exc.msg}")]

    findings = _scan_mock_usage(filename, tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            findings.extend(_analyze_test_function(filename, node))
    return findings


def check_tests_init(init_source, present_test_stems):
    """Warn (blocking, since it's a silent green) for any `test_*.py` stem NOT
    imported (`from . import test_x`) in a `tests/__init__.py` source — Odoo only
    collects imported tests.
    """
    try:
        tree = ast.parse(init_source, filename="tests/__init__.py")
    except SyntaxError as exc:
        return [_f("tests/__init__.py", exc.lineno or 1, "parse_error", "warning",
                    f"tests/__init__.py: SyntaxError: {exc.msg}")]

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level >= 1:
            for alias in node.names:
                imported.add(alias.name)
            if node.module:
                imported.add(node.module)

    findings = []
    for stem in present_test_stems:
        if stem not in imported:
            findings.append(_f("tests/__init__.py", 1, "not_imported", "blocking",
                f"{stem}.py exists but is not imported in tests/__init__.py — Odoo "
                "will never collect/run it (a silent green)"))
    return findings


def build_report(files):
    """Lint every file in `files` ({path: source}). Returns {summary, findings,
    _warnings}."""
    findings = []
    for path, source in files.items():
        findings.extend(lint_source(source, path))
    findings.sort(key=lambda f: (f["file"], f["line"]))
    blocking = sum(1 for f in findings if f["severity"] == "blocking")
    warning = sum(1 for f in findings if f["severity"] == "warning")
    return {
        "summary": {"files": len(files), "blocking": blocking, "warning": warning},
        "findings": findings,
        "_warnings": [],
    }


def _gather_test_files(paths):
    """Expand paths to a sorted list of test_*.py files (dirs walked recursively)."""
    out = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            for root, _dirs, fns in os.walk(pp):
                for fn in fns:
                    if fn.startswith("test_") and fn.endswith(".py"):
                        out.append(str(Path(root) / fn))
        elif pp.is_file():
            out.append(str(pp))
    return sorted(out)


def _check_init_files(paths):
    """For every walked directory containing a tests/__init__.py, run check_tests_init
    against the test_*.py siblings in that same directory."""
    findings = []
    for p in paths:
        pp = Path(p)
        if not pp.is_dir():
            continue
        for root, _dirs, fns in os.walk(pp):
            rootp = Path(root)
            init_fp = rootp / "__init__.py"
            if rootp.name != "tests" or not init_fp.exists():
                continue
            stems = sorted({Path(fn).stem for fn in fns if fn.startswith("test_") and fn.endswith(".py")})
            try:
                init_src = init_fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for finding in check_tests_init(init_src, stems):
                finding["file"] = str(init_fp)
                findings.append(finding)
    return findings


def main(argv=None):
    """Entry point: ``test_quality_gate.py <path> [<path>...]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(json.dumps({"summary": {"files": 0, "blocking": 0, "warning": 0},
                           "findings": [], "_warnings": ["no paths given"]}, indent=2))
        return

    file_paths = _gather_test_files(args)
    files = {}
    for fp in file_paths:
        try:
            files[fp] = Path(fp).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

    report = build_report(files)
    report["findings"].extend(_check_init_files(args))
    report["findings"].sort(key=lambda f: (f["file"], f["line"]))
    report["summary"]["blocking"] = sum(1 for f in report["findings"] if f["severity"] == "blocking")
    report["summary"]["warning"] = sum(1 for f in report["findings"] if f["severity"] == "warning")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
