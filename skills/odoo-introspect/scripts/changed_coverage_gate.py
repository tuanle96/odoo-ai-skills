"""
Changed-line coverage gate (local, no Odoo) — proves the CHANGED lines of each
changed method were actually EXECUTED BY A TEST, not just that overall
coverage is high.

Intersects a `diff_targets.py` report (change targets with per-method
``changed_exec_lines``) with a coverage.py JSON report produced with
``coverage json --show-contexts -o coverage.json``, and gates each target on
a per-risk-tier threshold: critical/high targets require 100% of their
changed lines to be covered by a real test context; normal targets require
>= 90%. A line "covered" only by a setUp/import/no-test context does not
count — that would let a change ship with its actual new behaviour never
exercised.

No Odoo connection required. All helpers are pure Python, unit-testable, and
read only from the filesystem (main() only). The caller/agent reads the
``ok`` field; exit code is always 0 so a non-zero return never suppresses the
JSON output.

Usage
-----
    python3 changed_coverage_gate.py --targets diff_targets.json --coverage coverage.json

Output: pure JSON to stdout.
"""
import argparse
import json
import sys
from pathlib import Path

try:
    import scenario_gen
except Exception:  # noqa: BLE001 — module unavailable → default to "normal" tier
    scenario_gen = None

_DEFAULT_RISK = "normal"
_THRESHOLDS = {"critical": 1.0, "high": 1.0, "normal": 0.9}

# Context-name substrings that mean "not a real test exercising this line"
# (setup/teardown fixtures and module import execution both run every time,
# regardless of whether the actual behaviour under test is correct).
_NON_TEST_MARKERS = ("setup", "import")


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo, unit-testable)
# ---------------------------------------------------------------------------

def _is_test_context(name):
    """True when *name* is a real test-execution context (not empty, not a
    setUp/setup/import fixture context). coverage.py's own "no context"
    marker is the empty string — never a real test."""
    if not name:
        return False
    low = str(name).lower()
    return not any(marker in low for marker in _NON_TEST_MARKERS)


def _match_file(cov_files, target_file):
    """Match *target_file* (repo-relative path) to a key in coverage.py's
    ``files`` dict. Tries exact match first, then a suffix match in either
    direction plus a basename match — Odoo addons paths measured by
    coverage.py are often absolute or rooted differently than the
    repo-relative diff path. Returns the matched key, or None."""
    if target_file in cov_files:
        return target_file
    target_norm = target_file.replace("\\", "/")
    target_base = target_norm.rsplit("/", 1)[-1]
    for key in cov_files:
        key_norm = str(key).replace("\\", "/")
        if key_norm.endswith(target_norm) or target_norm.endswith(key_norm):
            if key_norm.rsplit("/", 1)[-1] == target_base:
                return key
    return None


def covered_lines_for_file(cov_files, target_file):
    """Return the set of line numbers in the matched coverage file that were
    executed by at least one real test context. Falls back to ALL executed
    lines when the file has no "contexts" data (coverage run without
    --show-contexts)."""
    key = _match_file(cov_files, target_file)
    if key is None:
        return set()
    entry = cov_files.get(key) or {}
    executed = set(entry.get("executed_lines") or [])
    contexts = entry.get("contexts")
    if not isinstance(contexts, dict):
        return executed
    covered = set()
    for lineno_str, names in contexts.items():
        if not isinstance(names, list):
            continue
        if any(_is_test_context(n) for n in names):
            try:
                covered.add(int(lineno_str))
            except (TypeError, ValueError):
                continue
    # Only lines coverage.py actually marked executed AND test-attributed.
    return covered & executed


def _risk_tier_for_target(target):
    """Return the risk tier ('critical'|'high'|'normal') for *target*: an
    explicit "risk" key wins; otherwise tier via scenario_gen.classify_model_risk
    on the target's model; falls back to "normal" if scenario_gen or the
    model is unavailable."""
    risk = target.get("risk")
    if isinstance(risk, str) and risk in _THRESHOLDS:
        return risk
    if scenario_gen is not None:
        try:
            return scenario_gen.classify_model_risk(target.get("model"))["tier"]
        except Exception:  # noqa: BLE001
            return _DEFAULT_RISK
    return _DEFAULT_RISK


def evaluate_target(target, cov_files):
    """Evaluate a single diff target against the coverage report.

    Returns ``{"target_id","risk","threshold","changed_exec_lines",
    "covered_changed_exec_lines","missing_changed_exec_lines","ratio","ok"}``.
    """
    risk = _risk_tier_for_target(target)
    threshold = _THRESHOLDS.get(risk, _THRESHOLDS[_DEFAULT_RISK])
    changed = sorted(set(target.get("changed_exec_lines") or []))

    if not changed:
        return {
            "target_id": target.get("id"),
            "risk": risk,
            "threshold": threshold,
            "changed_exec_lines": [],
            "covered_changed_exec_lines": [],
            "missing_changed_exec_lines": [],
            "ratio": 1.0,
            "ok": True,
        }

    covered_in_file = covered_lines_for_file(cov_files, target.get("file"))
    covered = sorted(l for l in changed if l in covered_in_file)
    missing = sorted(l for l in changed if l not in covered_in_file)
    ratio = len(covered) / len(changed)

    return {
        "target_id": target.get("id"),
        "risk": risk,
        "threshold": threshold,
        "changed_exec_lines": changed,
        "covered_changed_exec_lines": covered,
        "missing_changed_exec_lines": missing,
        "ratio": ratio,
        "ok": ratio >= threshold,
    }


def build_report(diff_targets_doc, coverage_doc):
    """Assemble the full changed-coverage gate report.

    Returns ``{"summary":{"targets","fully_covered","gate"},"targets":[...],
    "ok":bool,"_warnings":[...]}``.
    """
    warnings = []
    targets = diff_targets_doc.get("targets") if isinstance(diff_targets_doc, dict) else None
    if not isinstance(targets, list):
        warnings.append("diff_targets: missing/invalid 'targets' list — treated as empty")
        targets = []

    cov_files = coverage_doc.get("files") if isinstance(coverage_doc, dict) else None
    if not isinstance(cov_files, dict):
        warnings.append("coverage: missing/invalid 'files' dict — treated as empty")
        cov_files = {}

    results = [evaluate_target(t, cov_files) for t in targets if isinstance(t, dict)]
    fully_covered = sum(1 for r in results if r["ok"])
    ok = all(r["ok"] for r in results)

    return {
        "summary": {
            "targets": len(results),
            "fully_covered": fully_covered,
            "gate": "pass" if ok else "fail",
        },
        "targets": results,
        "ok": ok,
        "_warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Filesystem-dependent work (main only)
# ---------------------------------------------------------------------------

def _load_json(path):
    """Read and parse a JSON file; returns (doc, error_str). error_str is
    None on success."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        return None, f"{path}: {type(exc).__name__}: {exc}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{path}: JSONDecodeError: {exc}"


def main(argv=None):
    """Entry point: ``changed_coverage_gate.py --targets <diff_targets.json>
    --coverage <coverage.json>``."""
    parser = argparse.ArgumentParser(
        prog="changed_coverage_gate.py",
        description="Gate on whether CHANGED lines of each changed method were executed by a test",
    )
    parser.add_argument("--targets", required=True, help="diff_targets.py JSON report")
    parser.add_argument("--coverage", required=True, help="coverage.py JSON report (--show-contexts)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    warnings = []
    targets_doc, err = _load_json(args.targets)
    if err:
        warnings.append(err)
        targets_doc = {}
    coverage_doc, err = _load_json(args.coverage)
    if err:
        warnings.append(err)
        coverage_doc = {}

    report = build_report(targets_doc, coverage_doc)
    report["_warnings"] = warnings + report["_warnings"]
    if warnings:
        report["ok"] = False
        report["summary"]["gate"] = "fail"
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
