"""
Diff → change-target mapper (local, no Odoo) — parses a `git diff` into
structured "change targets": {model, method, file, changed line ranges}.

This is the anchor everything else in the tool-chain binds to: CI produces it
FROM GIT (never the agent), and downstream gates (scenario_gen, deploy_gate,
trace_flow, ...) key off each target's ``id`` / ``model`` / ``method`` /
``changed_exec_lines`` to scope their checks to exactly what changed.

All helpers are pure Python (ast, re — no Odoo, no third-party deps) and are
unit-testable by injecting a unified-diff string and in-memory source text.
Only ``main()`` touches the filesystem/subprocess.

Usage
-----
    python3 diff_targets.py --base HEAD~1 --head HEAD [--repo DIR]
    python3 diff_targets.py --diff-file some.diff --repo DIR

Output: pure JSON to stdout. Exit code is always 0 (git/subprocess failure is
reported as a warning, not a crash) so a non-zero return never suppresses the
JSON output.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys

# --- Constants ----------------------------------------------------------------
_HUNK_RE = re.compile(r"^@@ -(?:\d+)(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_TEST_PATH_RE = re.compile(r"(^|/)tests/|(^|/)test_")

_DECORATOR_TRIGGERS = {
    "depends": "compute",
    "onchange": "onchange",
    "constrains": "constrains",
}
_FIELD_CALL_KWARGS = ("compute", "inverse", "search")


# --- Pure helpers (no Odoo needed — unit-testable) -----------------------------

def parse_diff_changed_lines(diff_text):
    """Parse a `git diff --unified=0` string into {file_path: set(changed_lines)}.

    For each `+++ b/<path>` file header and each following `@@ -a,b +c,d @@`
    hunk, records the added/changed line numbers on the NEW side (c..c+d-1;
    d defaults to 1 when omitted). Hunks with `d == 0` (deletions only, no
    added lines) contribute no line numbers. Files whose path contains
    `/tests/` (or starts with `tests/`) or whose basename matches `test_*`
    are skipped entirely.
    """
    files = {}
    current_path = None
    current_skip = False

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].strip()
            if raw == "/dev/null":
                current_path = None
                current_skip = True
                continue
            path = raw[2:] if raw.startswith("b/") else raw
            current_path = path
            current_skip = bool(_TEST_PATH_RE.search(path))
            if not current_skip:
                files.setdefault(path, set())
            continue

        if current_path is None or current_skip:
            continue

        m = _HUNK_RE.match(line)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        if count == 0:
            continue
        files[current_path].update(range(start, start + count))

    return files


def _decorator_name(node):
    """Extract the trailing attribute/name of a decorator expression, e.g.
    `@api.depends(...)` -> 'depends', `@api.onchange` -> 'onchange'."""
    target = node
    if isinstance(target, ast.Call):
        target = target.func
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return None


def _decorator_full(node):
    """Best-effort dotted decorator name, e.g. 'api.model_create_multi'."""
    target = node
    if isinstance(target, ast.Call):
        target = target.func
    parts = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def _method_triggers(func_node):
    """Return (triggers, requires_form) derived from the method's decorators."""
    triggers = []
    requires_form = False
    for dec in func_node.decorator_list:
        name = _decorator_name(dec)
        if name in _DECORATOR_TRIGGERS:
            trig = _DECORATOR_TRIGGERS[name]
            if trig not in triggers:
                triggers.append(trig)
            if name == "onchange":
                requires_form = True
        elif name in ("model_create_multi", "model"):
            full = _decorator_full(dec)
            note = f"@{full}"
            if note not in triggers:
                triggers.append(note)
    return triggers, requires_form


def _end_lineno(node):
    """end_lineno is always populated by ast on Python 3.8+ for parsed source."""
    return getattr(node, "end_lineno", node.lineno)


def _model_of_class(class_node):
    """Detect Odoo model identity for a ClassDef: `_name` wins over `_inherit`
    (list or string); returns None when neither is present."""
    name_val = None
    inherit_val = None
    for stmt in class_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
        if "_name" in targets and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
            name_val = stmt.value.value
        if "_inherit" in targets:
            if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                inherit_val = stmt.value.value
            elif isinstance(stmt.value, (ast.List, ast.Tuple)) and stmt.value.elts:
                first = stmt.value.elts[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    inherit_val = first.value
    return name_val or inherit_val


def _computed_field_targets(class_node, model, file_path, changed_lines):
    """Best-effort scan of `x = fields.X(..., compute="_compute_y")` (also
    inverse=/search=) class attributes whose line is in changed_lines; emit a
    target pointing at the referenced method (in the same class)."""
    targets = []
    method_by_name = {
        n.name: n for n in class_node.body if isinstance(n, ast.FunctionDef)
    }
    for stmt in class_node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if stmt.lineno not in changed_lines:
            continue
        call = stmt.value
        if not isinstance(call, ast.Call):
            continue
        for kw in call.keywords or []:
            if kw.arg not in _FIELD_CALL_KWARGS:
                continue
            if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                continue
            method_name = kw.value.value
            target_node = method_by_name.get(method_name)
            firstlineno = target_node.lineno if target_node else stmt.lineno
            span = [target_node.lineno, _end_lineno(target_node)] if target_node else [stmt.lineno, stmt.lineno]
            targets.append({
                "id": f"{model or file_path}:{method_name}:{file_path}:{firstlineno}",
                "kind": "computed_field",
                "model": model,
                "method": method_name,
                "file": file_path,
                "method_span": span,
                "changed_lines": [stmt.lineno],
                "changed_exec_lines": [stmt.lineno],
            })
    return targets


def _function_target(func_node, model, file_path, changed_lines, kind):
    lo, hi = func_node.lineno, _end_lineno(func_node)
    span = set(range(lo, hi + 1))
    hit = sorted(span & changed_lines)
    if not hit:
        return None
    target = {
        "id": f"{model or file_path}:{func_node.name}:{file_path}:{func_node.lineno}",
        "kind": kind,
        "model": model,
        "method": func_node.name,
        "file": file_path,
        "method_span": [lo, hi],
        "changed_lines": hit,
        "changed_exec_lines": hit,
    }
    triggers, requires_form = _method_triggers(func_node)
    if triggers:
        target["triggers"] = triggers
    if requires_form:
        target["requires_form"] = True
    return target


def map_lines_to_targets(source_code, changed_lines, file_path):
    """Parse `source_code` with ast; map `changed_lines` (a set/iterable of
    1-based line numbers) to a list of change-target dicts. Raises SyntaxError
    if `source_code` can't be parsed (callers should catch and warn)."""
    changed_lines = set(changed_lines)
    tree = ast.parse(source_code, filename=file_path)
    targets = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            model = _model_of_class(node)
            for stmt in node.body:
                if isinstance(stmt, ast.FunctionDef):
                    t = _function_target(stmt, model, file_path, changed_lines, "model_method")
                    if t:
                        targets.append(t)
            targets.extend(_computed_field_targets(node, model, file_path, changed_lines))

    # tree.body holds only the module's direct children, so a class's methods
    # (nested in ClassDef.body) never appear here — no extra filtering needed.
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef):
            t = _function_target(stmt, None, file_path, changed_lines, "function")
            if t:
                targets.append(t)

    return targets


def build_report(files_map, file_reader):
    """Assemble the full change-target report.

    Parameters
    ----------
    files_map : dict
        ``{path: set(changed_line_numbers)}`` as produced by
        `parse_diff_changed_lines`.
    file_reader : callable
        ``file_reader(path) -> source_str | None``. `None` means the file
        can't be read (deleted, binary, non-Python, ...) — skipped with a
        warning.

    Returns
    -------
    dict
        ``{"targets": [...], "summary": {"targets", "files", "models"},
        "_warnings": [...]}``
    """
    warnings = []
    targets = []
    touched_files = set()

    for path, changed_lines in files_map.items():
        if not path.endswith(".py"):
            continue
        source = file_reader(path)
        if source is None:
            warnings.append(f"{path}: could not read file — skipped")
            continue
        try:
            file_targets = map_lines_to_targets(source, changed_lines, path)
        except SyntaxError as exc:
            warnings.append(f"{path}: SyntaxError — {exc}")
            continue
        if file_targets:
            touched_files.add(path)
        targets.extend(file_targets)

    models = sorted({t["model"] for t in targets if t.get("model")})
    return {
        "targets": targets,
        "summary": {
            "targets": len(targets),
            "files": len(touched_files),
            "models": models,
        },
        "_warnings": warnings,
    }


# --- Filesystem/subprocess-dependent work (main only) --------------------------

def _run_git_diff(repo, base, head):
    """Run `git -C <repo> diff --unified=0 <base> <head>`; returns (text, error)."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "diff", "--unified=0", base, head],
            capture_output=True, text=True, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"git: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return None, f"git: {proc.stderr.strip() or 'diff failed with code ' + str(proc.returncode)}"
    return proc.stdout, None


def _read_repo_file(repo, path):
    full = os.path.join(repo, path)
    try:
        with open(full, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="diff_targets.py",
        description="Parse a git diff into structured change targets (model/method/file/lines)",
    )
    parser.add_argument("--base", default="HEAD~1", help="Base git ref (default: HEAD~1)")
    parser.add_argument("--head", default="HEAD", help="Head git ref (default: HEAD)")
    parser.add_argument("--diff-file", default=None, help="Read a unified diff from this path instead of running git")
    parser.add_argument("--repo", default=".", help="Repo directory (default: cwd)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    warnings = []
    diff_text = None

    if args.diff_file:
        try:
            with open(args.diff_file, "r", encoding="utf-8") as fh:
                diff_text = fh.read()
        except OSError as exc:
            warnings.append(f"diff-file: {exc}")
    else:
        diff_text, err = _run_git_diff(args.repo, args.base, args.head)
        if err:
            warnings.append(err)

    if diff_text is None:
        print(json.dumps({
            "targets": [],
            "summary": {"targets": 0, "files": 0, "models": []},
            "_warnings": warnings,
        }, indent=2))
        return

    files_map = parse_diff_changed_lines(diff_text)
    report = build_report(files_map, lambda p: _read_repo_file(args.repo, p))
    report["_warnings"] = warnings + report["_warnings"]
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
