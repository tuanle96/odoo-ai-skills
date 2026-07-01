"""
Targeted mutation testing (Layer L) — flips a comparison / removes a raise /
swaps a boolean op on the CHANGED lines of a diff target, re-runs only the
tests that cover that target, and reports which mutants "survived" (the tests
still passed despite the behaviour-breaking edit). A surviving mutant proves
the covering test asserts nothing about the mutated behaviour — the cheapest
strong antidote to vacuous assertions (`assertTrue(True)`, missing asserts,
swallowed exceptions, ...).

Mutation operators (fired only on AST nodes whose ``lineno`` is in the
target's ``changed_exec_lines``):
    eq_to_neq      ``==`` <-> ``!=``
    lt_to_ge       ``<`` <-> ``>=``,  ``>`` <-> ``<=``
    and_to_or      boolean ``and`` <-> ``or``
    in_to_notin    ``in`` <-> ``not in``
    true_to_false  constant ``True`` <-> ``False``
    remove_raise   a ``raise ...`` statement -> ``pass  # mutated: raise removed``
                   (guard-removal — the highest-value mutant for Odoo
                   UserError/ValidationError guards)

CAUTION — never mutated (skipped with a note, see ``_skip_reason``):
    - a line containing ``super(`` (mutating it risks breaking the MRO chain
      in a way no test can meaningfully attribute)
    - a line containing ``sudo(`` (mutating a privilege-escalation call is
      unsafe to auto-flip)
    - any target whose ``file`` is under a ``migrations/`` directory or ends
      in ``.xml`` (not a Python behaviour mutation)

No Odoo connection required for generation/assembly. All PART A helpers are
pure Python (``ast``-based), read no filesystem, and are unit-testable. PART B
(``run`` / ``make_odoo_test_runner``) shells out to ``odoo-bin`` and is
orchestration only — documented, not unit-tested.

Usage
-----
    # Assembly (local, testable): fold a precomputed results map/list into the
    # canonical report — lets CI run mutants elsewhere and just assemble here.
    python3 mutation_smoke.py --targets diff_targets.json --results results.json

    # Generate-only: emit the mutant plan (no execution).
    python3 mutation_smoke.py --targets diff_targets.json --generate

Output: pure JSON to stdout. Exit code is always 0 (a missing/unparseable
input is reported as a warning, never a crash) so a broken run never
suppresses the JSON output — and never fabricates ``survived > 0`` either
(default 0, so a broken run can't spuriously block a deploy gate).
"""
import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

MAX_MUTANTS_PER_TARGET = 8

_CMP_FLIP = {
    ast.Eq: ("eq_to_neq", "==", "!="),
    ast.NotEq: ("eq_to_neq", "!=", "=="),
    ast.Lt: ("lt_to_ge", "<", ">="),
    ast.Gt: ("lt_to_ge", ">", "<="),
    ast.LtE: ("lt_to_ge", "<=", ">"),
    ast.GtE: ("lt_to_ge", ">=", "<"),
    ast.In: ("in_to_notin", "in", "not in"),
    ast.NotIn: ("in_to_notin", "not in", "in"),
}


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo needed — unit-testable)
# ---------------------------------------------------------------------------

def _skip_reason(text, file_path=""):
    """Return a human-readable reason to SKIP mutating *text* (a line, or a
    multi-line span joined with '\\n'), or None when it's safe to mutate.
    See the module docstring CAUTION list."""
    if "super(" in text:
        return "contains super(...) — mutating it risks an unattributable MRO break"
    if "sudo(" in text:
        return "contains sudo(...) — mutating a privilege-escalation call is unsafe"
    norm = (file_path or "").replace("\\", "/")
    if "migrations/" in norm:
        return "file is under migrations/ — not a behaviour-mutation target"
    if norm.endswith(".xml"):
        return "file is XML, not Python source"
    return None


def _find_token(window, window_start_col):
    """Locate the single non-whitespace token inside *window* (the source
    slice strictly between two AST operands). Returns (start_col, end_col,
    token) or (None, None, None) when the window is blank. Because *window*
    is bounded by adjacent node col_offsets it contains only the operator
    (plus surrounding whitespace), so a strip() reliably isolates it without
    needing a regex."""
    token = window.strip()
    if not token:
        return None, None, None
    idx = window.find(token)
    return window_start_col + idx, window_start_col + idx + len(token), token


def _emit(mutants, counts, operator, line, original_line, mutated_source, target_id, file):
    key = (operator, line)
    i = counts.get(key, 0)
    counts[key] = i + 1
    mutants.append({
        "id": f"{operator}_L{line}_{i}",
        "operator": operator,
        "line": line,
        "original": original_line,
        "mutated": mutated_source,
        "target_id": target_id,
        "file": file,
    })


def _splice_line(lines, lineno, start_col, end_col, replacement):
    """Return the whole mutated source (lines joined with '\\n') with
    ``lines[lineno-1][start_col:end_col]`` replaced by *replacement*."""
    line_text = lines[lineno - 1]
    new_line = line_text[:start_col] + replacement + line_text[end_col:]
    mutated = list(lines)
    mutated[lineno - 1] = new_line
    return "\n".join(mutated)


def _handle_compare(node, lines, target_id, file, mutants, counts):
    """Compare with exactly one op (a chained `a < b < c` is skipped — no
    single unambiguous operator token to flip)."""
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return
    op_type = type(node.ops[0])
    flip = _CMP_FLIP.get(op_type)
    if flip is None:
        return
    operator, symbol, replacement = flip
    lineno = node.lineno
    left, right = node.left, node.comparators[0]
    if getattr(left, "end_lineno", left.lineno) != lineno or right.lineno != lineno:
        return  # operator not confined to a single physical line — skip
    line_text = lines[lineno - 1]
    if _skip_reason(line_text, file):
        return
    window = line_text[left.end_col_offset:right.col_offset]
    start, end, token = _find_token(window, left.end_col_offset)
    if token != symbol:
        return
    mutated_source = _splice_line(lines, lineno, start, end, replacement)
    _emit(mutants, counts, operator, lineno, line_text, mutated_source, target_id, file)


def _handle_boolop(node, lines, target_id, file, mutants, counts):
    """BoolOp with exactly two operands (`a and b and c` is skipped — the
    "and" appears twice, no single unambiguous token)."""
    if len(node.values) != 2:
        return
    left, right = node.values
    lineno = node.lineno
    if getattr(left, "end_lineno", left.lineno) != lineno or right.lineno != lineno:
        return
    line_text = lines[lineno - 1]
    if _skip_reason(line_text, file):
        return
    window = line_text[left.end_col_offset:right.col_offset]
    start, end, token = _find_token(window, left.end_col_offset)
    if token not in ("and", "or"):
        return
    replacement = "or" if token == "and" else "and"
    mutated_source = _splice_line(lines, lineno, start, end, replacement)
    _emit(mutants, counts, "and_to_or", lineno, line_text, mutated_source, target_id, file)


def _handle_constant_bool(node, lines, target_id, file, mutants, counts):
    lineno = node.lineno
    line_text = lines[lineno - 1]
    if _skip_reason(line_text, file):
        return
    start, end = node.col_offset, node.end_col_offset
    token = line_text[start:end]
    if token not in ("True", "False"):
        return
    replacement = "False" if token == "True" else "True"
    mutated_source = _splice_line(lines, lineno, start, end, replacement)
    _emit(mutants, counts, "true_to_false", lineno, line_text, mutated_source, target_id, file)


def _handle_raise(node, lines, target_id, file, mutants, counts):
    lineno = node.lineno
    end_lineno = getattr(node, "end_lineno", lineno)
    span_text = "\n".join(lines[lineno - 1:end_lineno])
    if _skip_reason(span_text, file):
        return
    original_line = lines[lineno - 1]
    indent = original_line[:len(original_line) - len(original_line.lstrip())]
    mutated = list(lines)
    mutated[lineno - 1:end_lineno] = [indent + "pass  # mutated: raise removed"]
    mutated_source = "\n".join(mutated)
    _emit(mutants, counts, "remove_raise", lineno, original_line, mutated_source, target_id, file)


def generate_mutants(source, changed_lines, target_id="", file=""):
    """Parse *source* with ``ast`` and emit small mutants on AST nodes whose
    ``lineno`` is in *changed_lines* (a set/iterable of 1-based line numbers).

    Returns ``[mutant dict]`` — each ``{"id","operator","line","original",
    "mutated","target_id","file"}``. Never raises: malformed/unparseable
    *source* returns ``[]``. Lines matching ``_skip_reason`` are left
    untouched (see module docstring CAUTION list)."""
    changed = set(changed_lines or [])
    if not changed or not isinstance(source, str) or not source.strip():
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []

    lines = source.split("\n")
    mutants = []
    counts = {}
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        if lineno is None or lineno not in changed:
            continue
        if isinstance(node, ast.Raise):
            _handle_raise(node, lines, target_id, file, mutants, counts)
        elif isinstance(node, ast.Compare):
            _handle_compare(node, lines, target_id, file, mutants, counts)
        elif isinstance(node, ast.BoolOp):
            _handle_boolop(node, lines, target_id, file, mutants, counts)
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            _handle_constant_bool(node, lines, target_id, file, mutants, counts)
    return mutants


def _cap_mutants(mutants):
    """Cap to MAX_MUTANTS_PER_TARGET; returns (kept, n_discarded). Discarding
    is always reported by the caller — never silent."""
    if len(mutants) <= MAX_MUTANTS_PER_TARGET:
        return mutants, 0
    return mutants[:MAX_MUTANTS_PER_TARGET], len(mutants) - MAX_MUTANTS_PER_TARGET


def classify_results(mutants):
    """Roll up a single target's mutant list (each carrying a "status" of
    "killed"/"survived"/"skipped") into counts. A missing/unrecognised status
    is treated as "skipped" (conservative — never counted as proof either
    way). Returns {"mutants","survived","killed","skipped"}."""
    counts = {"mutants": len(mutants or []), "survived": 0, "killed": 0, "skipped": 0}
    for m in mutants or []:
        status = m.get("status") if isinstance(m, dict) else None
        if status in ("survived", "killed", "skipped"):
            counts[status] += 1
        else:
            counts["skipped"] += 1
    return counts


def build_report(targets_results):
    """Assemble the final mutation-smoke report from per-target results.

    *targets_results*: ``[{"target_id","file","mutants":[mutant dict with a
    "status" and "killed_by" already attached]}]``.

    Returns the schema deploy_gate.py consumes:
        {"summary":{"targets","mutants","survived","killed","skipped"},
         "targets":[...], "decision":"pass"|"block", "_warnings":[]}
    ``decision`` is "block" iff any mutant survived — a broken/empty run
    (0 targets, 0 mutants) always decides "pass" so it can never fabricate
    a block."""
    warnings = []
    targets_out = []
    total = {"mutants": 0, "survived": 0, "killed": 0, "skipped": 0}

    for tr in targets_results or []:
        if not isinstance(tr, dict):
            warnings.append("skipped a malformed target entry (not an object)")
            continue
        target_id = tr.get("target_id")
        file = tr.get("file")
        mutants = tr.get("mutants")
        if not isinstance(mutants, list):
            warnings.append(f"{target_id}: 'mutants' is not a list — treated as empty")
            mutants = []
        counts = classify_results(mutants)
        for k in total:
            total[k] += counts[k]
        targets_out.append({"target_id": target_id, "file": file, "mutants": mutants})

    return {
        "summary": {
            "targets": len(targets_out),
            "mutants": total["mutants"],
            "survived": total["survived"],
            "killed": total["killed"],
            "skipped": total["skipped"],
        },
        "targets": targets_out,
        "decision": "block" if total["survived"] > 0 else "pass",
        "_warnings": warnings,
    }


# ---------------------------------------------------------------------------
# PART B — Runner (orchestration; subprocess/filesystem — NOT unit-tested)
# ---------------------------------------------------------------------------

def run(targets, run_tests_fn):
    """Orchestrate mutation testing for each target: generate mutants, apply
    each to *file* IN PLACE (backup-and-restore via try/finally so a mutated
    file is never left on disk, even if run_tests_fn raises), call
    ``run_tests_fn(mutated_file_path, target) -> (passed, failing_test_ids)``,
    and classify the mutant "survived" (tests still passed) or "killed"
    (tests failed — killed_by = the failing ids).

    Returns ``(targets_results, warnings)`` — feed ``targets_results`` to
    ``build_report``."""
    warnings = []
    targets_results = []

    for t in targets or []:
        if not isinstance(t, dict):
            continue
        target_id = t.get("id")
        file_path = t.get("file")
        changed = t.get("changed_exec_lines") or []
        path = Path(file_path) if file_path else None
        if path is None or not path.exists():
            warnings.append(f"{target_id}: file not found — skipped")
            targets_results.append({"target_id": target_id, "file": file_path, "mutants": []})
            continue

        source = path.read_text()
        mutants = generate_mutants(source, changed, target_id=target_id, file=file_path)
        mutants, n_capped = _cap_mutants(mutants)
        if n_capped:
            warnings.append(f"{target_id}: capped to {MAX_MUTANTS_PER_TARGET} mutants "
                             f"({n_capped} discarded)")

        original_bytes = path.read_bytes()
        for m in mutants:
            try:
                path.write_text(m["mutated"])
                passed, failing_ids = run_tests_fn(str(path), t)
            finally:
                path.write_bytes(original_bytes)
            if passed:
                m["status"], m["killed_by"] = "survived", []
            else:
                m["status"], m["killed_by"] = "killed", list(failing_ids or [])

        targets_results.append({"target_id": target_id, "file": file_path, "mutants": mutants})

    return targets_results, warnings


def _parse_failing_test_ids(output):
    """Best-effort scrape of `unittest`/Odoo test-runner output for failing
    test identifiers (lines like 'FAIL: test_x' / 'ERROR: test_x ...')."""
    ids = []
    for line in (output or "").splitlines():
        line = line.strip()
        for prefix in ("FAIL:", "ERROR:"):
            if line.startswith(prefix):
                rest = line[len(prefix):].strip()
                ids.append(rest.split(" ", 1)[0] if rest else rest)
    return ids


def make_odoo_test_runner(db, module, odoo_bin="odoo-bin", test_tags_for=None):
    """Build a ``run_tests_fn(mutated_file_path, target) -> (passed,
    failing_ids)`` that shells out to:

        odoo-bin -d DB -u MODULE --test-enable --stop-after-init --test-tags <tags>

    ``test_tags_for(target) -> str`` picks the ``--test-tags`` value for a
    target; defaults to ``/MODULE`` (run the whole module's tests) or
    ``/MODULE:MODEL`` when the target carries a ``model``. ``passed`` is
    ``proc.returncode == 0``; on a non-zero exit, failing ids are scraped
    from stdout/stderr (best-effort — an empty list still correctly marks
    the mutant "killed")."""
    def _default_tags(target):
        model = (target or {}).get("model")
        return f"/{module}:{model}" if model else f"/{module}"

    tags_for = test_tags_for or _default_tags

    def run_tests_fn(mutated_file_path, target):
        cmd = [odoo_bin, "-d", db, "-u", module, "--test-enable",
               "--stop-after-init", "--test-tags", tags_for(target)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as exc:  # noqa: BLE001
            return False, [f"runner-error:{type(exc).__name__}: {exc}"]
        passed = proc.returncode == 0
        failing = [] if passed else _parse_failing_test_ids(proc.stdout + "\n" + proc.stderr)
        return passed, failing

    return run_tests_fn


# ---------------------------------------------------------------------------
# Filesystem-dependent CLI plumbing (main only)
# ---------------------------------------------------------------------------

def _load_json(path):
    try:
        text = Path(path).read_text()
    except OSError as exc:
        return None, f"{path}: {type(exc).__name__}: {exc}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{path}: JSONDecodeError: {exc}"


def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _targets_list(targets_doc, warnings):
    targets = targets_doc.get("targets") if isinstance(targets_doc, dict) else None
    if not isinstance(targets, list):
        warnings.append("targets: missing/invalid 'targets' list — treated as empty")
        return []
    return [t for t in targets if isinstance(t, dict)]


def _generate_plan(targets_doc, file_reader, warnings):
    """generate-only mode: for each target, read its file and generate
    mutants (status "skipped" — nothing executed), no execution."""
    targets_results = []
    for t in _targets_list(targets_doc, warnings):
        target_id, file_path = t.get("id"), t.get("file")
        changed = t.get("changed_exec_lines") or []
        source = file_reader(file_path) if file_path else None
        if source is None:
            warnings.append(f"{target_id}: could not read {file_path} — skipped")
            targets_results.append({"target_id": target_id, "file": file_path, "mutants": []})
            continue
        try:
            ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            warnings.append(f"{file_path}: SyntaxError — {exc}")

        mutants = generate_mutants(source, changed, target_id=target_id, file=file_path)
        mutants, n_capped = _cap_mutants(mutants)
        if n_capped:
            warnings.append(f"{target_id}: capped to {MAX_MUTANTS_PER_TARGET} mutants "
                             f"({n_capped} discarded)")
        for m in mutants:
            m["status"], m["killed_by"] = "skipped", []
        targets_results.append({"target_id": target_id, "file": file_path, "mutants": mutants})
    return targets_results


def _assemble_targets_results(targets_doc, results_doc, file_reader, warnings):
    """Assembly mode: fold a precomputed *results_doc* into per-target
    results ready for build_report.

    *results_doc* is either:
      (a) already a list of per-target result dicts (CI ran the mutants
          elsewhere and carries the full mutant dicts with "status" set) —
          passed through unchanged; or
      (b) ``{target_id: {mutant_id: {"status","killed_by"}}}`` — mutants are
          regenerated locally from *targets_doc* + *file_reader* and the
          statuses overlaid by id. A target/mutant absent from the map is
          "skipped" (never fabricated as killed/survived)."""
    if isinstance(results_doc, list):
        return results_doc

    results_map = results_doc if isinstance(results_doc, dict) else {}
    if not isinstance(results_doc, dict):
        warnings.append("results: not an object or list — treated as empty")

    targets_results = []
    for t in _targets_list(targets_doc, warnings):
        target_id, file_path = t.get("id"), t.get("file")
        changed = t.get("changed_exec_lines") or []
        source = file_reader(file_path) if file_path else None
        if source is None:
            warnings.append(f"{target_id}: could not read {file_path} — skipped")
            targets_results.append({"target_id": target_id, "file": file_path, "mutants": []})
            continue

        mutants = generate_mutants(source, changed, target_id=target_id, file=file_path)
        mutants, n_capped = _cap_mutants(mutants)
        if n_capped:
            warnings.append(f"{target_id}: capped to {MAX_MUTANTS_PER_TARGET} mutants "
                             f"({n_capped} discarded)")

        submap = results_map.get(target_id)
        if submap is not None and not isinstance(submap, dict):
            warnings.append(f"{target_id}: results entry is not an object — all mutants skipped")
            submap = {}
        submap = submap or {}

        for m in mutants:
            entry = submap.get(m["id"])
            if isinstance(entry, dict) and entry.get("status") in ("killed", "survived", "skipped"):
                m["status"] = entry["status"]
                kb = entry.get("killed_by")
                m["killed_by"] = kb if isinstance(kb, list) else []
            else:
                m["status"], m["killed_by"] = "skipped", []

        targets_results.append({"target_id": target_id, "file": file_path, "mutants": mutants})
    return targets_results


def main(argv=None):
    """Entry point: ``mutation_smoke.py --targets diff_targets.json
    (--results results.json | --generate)``."""
    parser = argparse.ArgumentParser(
        prog="mutation_smoke.py",
        description="Targeted mutation testing over the CHANGED lines of each diff target",
    )
    parser.add_argument("--targets", required=True, help="diff_targets.py JSON report")
    parser.add_argument("--results", default=None,
                         help="Precomputed results map/list JSON (assembly mode)")
    parser.add_argument("--generate", action="store_true",
                         help="Generate-only: emit the mutant plan, no execution")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    warnings = []
    targets_doc, err = _load_json(args.targets)
    if err:
        warnings.append(err)
        targets_doc = {}

    if args.generate:
        targets_results = _generate_plan(targets_doc, _read_file, warnings)
    elif args.results:
        results_doc, err = _load_json(args.results)
        if err:
            warnings.append(err)
            results_doc = {}
        targets_results = _assemble_targets_results(targets_doc, results_doc, _read_file, warnings)
    else:
        warnings.append("neither --results nor --generate supplied — nothing to assemble")
        targets_results = []

    report = build_report(targets_results)
    report["_warnings"] = warnings + report["_warnings"]
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
