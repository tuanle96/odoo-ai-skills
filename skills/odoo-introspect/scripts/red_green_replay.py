"""
Red -> green replay verifier (Layer L, CI-side) — proves the "test fails before
the patch, passes after" ritual by actually DOING it, instead of trusting an
agent's claim. An agent can always assert "I confirmed the test was red before
my fix" without it being true; this script makes CI re-derive that fact.

Two ways to use it:

  1. RUN mode (orchestration, real git + subprocess): checkout the BASE commit,
     apply ONLY the PR's test files from HEAD onto that base tree, run the
     targeted tests (must FAIL — a real behavioral red, not a TODO stub or an
     import error), then checkout HEAD fully and re-run the same tests (must
     PASS, same test identities). This defeats a faked before/after where the
     agent never actually ran the pre-fix test, or swapped in a different,
     always-green test at head.

  2. ASSEMBLY mode (local, pure, unit-testable): CI already captured the raw
     base/head test output to two files; this just parses + classifies them.
     RUN mode is a thin wrapper that captures output via `run_tests_fn` and
     hands it to the same assembly path.

Pure stdlib only (json, sys, os, re, argparse, subprocess, pathlib). No Odoo
import, no third-party deps. Python 3.8+ (no match statements).

Usage
-----
    # Assembly (CI already ran the tests and captured raw output to files):
    python3 red_green_replay.py --base-output base.txt --head-output head.txt [--additive]

    # Orchestration (this script does the git checkouts + test runs itself;
    # MUST run in a CI-dedicated worktree, never a developer's working tree —
    # it performs real `git checkout` and will discard uncommitted changes):
    python3 red_green_replay.py --run --base <base_sha> --head <head_sha> \\
        --tests tests/test_x.py --db DB --module M [--repo DIR] [--additive]

Output: pure JSON to stdout. Exit code is always 0 — the caller (e.g.
deploy_gate.py) reads the top-level ``ok`` boolean; a broken/incomplete replay
must NEVER emit ``ok: true``.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_ID_RE = re.compile(r"^(FAIL|ERROR):\s+(.+?)\s+\(([\w.]+)\)\s*$", re.MULTILINE)
_BLOCK_SPLIT_RE = re.compile(r"^=+$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo needed — unit-testable)
# ---------------------------------------------------------------------------

def is_legit_red_failure(failure_text):
    """Classify whether a BASE (pre-fix) test failure looks like a genuine
    behavioral red, or a "fake red" an agent could game to satisfy the ritual
    without proving anything (a TODO stub, an import/collection error, a
    setUp crash, ...).

    Returns (bool, reason). Conservative: text that doesn't clearly match a
    known-legit pattern is NOT trusted — (False, "could not confirm...").
    """
    text = failure_text or ""
    low = text.lower()

    if not low.strip():
        return False, "empty failure text — nothing to confirm a behavioral failure"

    if 'self.fail("todo' in low or "self.fail('todo" in low or "todo:" in low:
        return False, "explicit TODO/self.fail() stub failure, not a real behavioral red"

    if "modulenotfounderror" in low or "importerror" in low:
        return False, "ImportError/ModuleNotFoundError — an environment/collection failure, not a behavioral red"

    if "syntaxerror" in low or "indentationerror" in low:
        return False, "SyntaxError/IndentationError — the test file does not even parse"

    if ("unittest.loader" in low or "failed to import test module" in low
            or "collection error" in low or "error while loading" in low):
        return False, "test collection error — the test never ran"

    if "in setup" in low or "in setupclass" in low:
        return False, "error raised in setUp/setUpClass — the test body itself never executed"

    if ("no tests ran" in low or "0 tests" in low
            or re.search(r"\bran 0 tests\b", low)):
        return False, "no tests ran — can't be a legitimate red without a test executing"

    if "assertionerror" in low or "usererror" in low or "validationerror" in low or "not raised" in low:
        return True, "behavioral assertion failure"

    return False, "could not confirm a real behavioral failure"


def parse_odoo_test_result(output_text):
    """Parse Odoo/unittest-style test-runner output. Robust to partial/garbled
    text — never raises, always returns the full shape (best-effort).

    Returns {"failed": bool, "passed": bool, "test_ids": [...], "failure_texts": [...]}.
    ``test_ids`` are dotted ids built from ``FAIL: <method> (<class>)`` /
    ``ERROR: <method> (<class>)`` lines. ``failure_texts`` are the raw blocks
    (delimited by ``====...====`` separator lines) containing each such line.
    """
    result = {"failed": False, "passed": False, "test_ids": [], "failure_texts": []}
    text = output_text or ""
    try:
        id_matches = list(_ID_RE.finditer(text))
        ids = [f"{m.group(3).strip()}.{m.group(2).strip()}" for m in id_matches]
        result["test_ids"] = list(dict.fromkeys(ids))  # de-dup, preserve order

        result["failure_texts"] = [
            block.strip() for block in _BLOCK_SPLIT_RE.split(text) if _ID_RE.search(block)
        ]

        has_fail_or_error = bool(id_matches)
        has_failed_summary = bool(re.search(r"^FAILED\b", text, re.MULTILINE))
        has_ok_summary = bool(re.search(r"^OK\s*$", text, re.MULTILINE))
        no_tests = ("no tests ran" in text.lower()
                    or bool(re.search(r"\bran 0 tests\b", text, re.IGNORECASE)))

        if has_fail_or_error or has_failed_summary:
            result["failed"] = True
        elif has_ok_summary and not no_tests:
            result["passed"] = True
    except Exception:  # noqa: BLE001 — best-effort parse, never raise
        pass
    return result


def _is_missing_symbol_red(text):
    """True when a BASE failure is an AttributeError/ImportError naming a
    symbol that simply doesn't exist yet — the expected shape of "red" for an
    ADDITIVE feature (the new field/method genuinely doesn't exist yet), as
    opposed to a bugfix red (which must be a real behavioral assertion)."""
    low = (text or "").lower()
    if "attributeerror" in low and "has no attribute" in low:
        return True
    if ("importerror" in low or "modulenotfounderror" in low) and (
            "cannot import name" in low or "no module named" in low):
        return True
    return False


def classify_replay(base_result, head_result, is_bugfix=True):
    """Compare a BASE (pre-fix) and HEAD (post-fix) parsed test-result pair and
    decide whether the red -> green transition is genuine. Never raises (bad
    input is coerced to a safe empty shape, which yields ok=False).

    ``same_identity``: the base run's failing test ids must be a non-empty
    subset of the ids head actually ran — this defeats swapping in a
    DIFFERENT, always-green test at head while claiming the same test now
    passes.

    ``red_is_legit``: at least one base failure_text must look like a real
    behavioral assertion (see ``is_legit_red_failure``). EXCEPTION: when
    ``is_bugfix=False`` (an additive feature, not a bugfix), a red caused by a
    not-yet-existing symbol (AttributeError "has no attribute" / ImportError
    "cannot import name") is ALSO accepted — that is the expected shape of
    "red" before an additive feature is implemented, not a fake ritual.

    Returns the full report schema: {"ok", "base_failed", "head_passed",
    "same_identity", "red_is_legit", "tests", "reasons", "_warnings"}.
    """
    base_result = base_result if isinstance(base_result, dict) else {}
    head_result = head_result if isinstance(head_result, dict) else {}
    reasons = []

    base_failed = bool(base_result.get("failed"))
    head_failed = bool(head_result.get("failed"))
    head_passed = bool(head_result.get("passed")) and not head_failed

    base_ids = {x for x in (base_result.get("test_ids") or []) if isinstance(x, str)}
    head_ids = {x for x in (head_result.get("test_ids") or []) if isinstance(x, str)}
    same_identity = bool(base_ids) and base_ids.issubset(head_ids)

    failure_texts = [t for t in (base_result.get("failure_texts") or []) if isinstance(t, str)]

    red_is_legit = False
    legit_reason = None
    for t in failure_texts:
        ok, why = is_legit_red_failure(t)
        if ok:
            red_is_legit, legit_reason = True, why
            break
    if not red_is_legit and not is_bugfix:
        for t in failure_texts:
            if _is_missing_symbol_red(t):
                red_is_legit = True
                legit_reason = (
                    "additive change (is_bugfix=False): missing-symbol "
                    "(AttributeError/ImportError) red accepted for a "
                    "not-yet-implemented feature"
                )
                break

    if not base_failed:
        reasons.append("base did not fail — there is no red to prove (base_failed=False)")
    if not head_passed:
        reasons.append("head did not pass cleanly — the fix does not resolve the tests (head_passed=False)")
    if not same_identity:
        reasons.append(
            "failing base test id(s) are not a non-empty subset of the tests head ran — "
            "could not confirm the SAME test(s) went red->green (same_identity=False)"
        )
    if not red_is_legit:
        if failure_texts:
            _, why = is_legit_red_failure(failure_texts[0])
            reasons.append(f"base failure is not a legitimate behavioral red: {why} (red_is_legit=False)")
        else:
            reasons.append("no base failure text captured to confirm a behavioral red (red_is_legit=False)")
    elif legit_reason:
        reasons.append(f"red confirmed legitimate: {legit_reason}")

    ok = base_failed and head_passed and same_identity and red_is_legit

    return {
        "ok": ok,
        "base_failed": base_failed,
        "head_passed": head_passed,
        "same_identity": same_identity,
        "red_is_legit": red_is_legit,
        "tests": sorted(base_ids | head_ids),
        "reasons": reasons,
        "_warnings": [],
    }


def build_report(base_result, head_result, is_bugfix=True):
    """Wrap classify_replay. Never raises, even on malformed/empty inputs — a
    broken replay must NEVER emit ok=true, so any internal error still yields
    a well-formed ok=False report instead of propagating."""
    try:
        return classify_replay(base_result, head_result, is_bugfix=is_bugfix)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "base_failed": False, "head_passed": False,
            "same_identity": False, "red_is_legit": False, "tests": [],
            "reasons": [f"internal error assembling replay report: {type(exc).__name__}: {exc}"],
            "_warnings": [],
        }


# ---------------------------------------------------------------------------
# Runner (orchestration; subprocess/git — NOT unit-tested)
#
# This section MUTATES GIT STATE in `repo` (checkouts base_sha, overlays test
# files, checks out head_sha). It MUST run in a CI-dedicated worktree, never a
# developer's normal working tree — uncommitted changes there will be
# discarded by `git checkout --force`.
# ---------------------------------------------------------------------------

def _current_ref(repo):
    """Best-effort current branch name, or the raw commit sha if detached."""
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    ref = proc.stdout.strip()
    if ref == "HEAD":
        proc2 = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        ref = proc2.stdout.strip()
    return ref


def _git_checkout(repo, ref):
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "checkout", "--force", ref],
            capture_output=True, text=True, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, proc.stderr.strip() or f"checkout {ref} failed (exit {proc.returncode})"
    return True, None


def _git_checkout_paths(repo, ref, paths):
    """`git checkout <ref> -- <paths>` — overlay ONLY these paths from `ref`
    onto the currently checked-out tree (used to apply the PR's test files
    from head onto the base tree, without pulling in the fix itself)."""
    paths = list(paths or [])
    if not paths:
        return False, "no test_paths given to apply from head"
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "checkout", ref, "--"] + paths,
            capture_output=True, text=True, check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, proc.stderr.strip() or f"checkout paths from {ref} failed (exit {proc.returncode})"
    return True, None


def run_replay(base_sha, head_sha, test_paths, run_tests_fn, repo=".", apply_tests_fn=None,
                is_bugfix=True):
    """Orchestrate the full red -> green replay against a real git repo.

    1. Record the current ref (so it can be restored).
    2. `git checkout base_sha` (force — discards uncommitted changes).
    3. Apply ONLY the PR's test files onto the base tree: by default
       `git checkout head_sha -- <test_paths>` (override with
       ``apply_tests_fn(repo, head_sha, test_paths)`` for a custom overlay
       strategy). This must NOT bring in the fix itself, only the tests.
    4. `run_tests_fn()` -> base_output (raw combined stdout+stderr text).
    5. `git checkout head_sha` (force) — the real, full post-fix tree.
    6. `run_tests_fn()` -> head_output.
    7. ALWAYS restore the original ref in a `finally` — the repo must never
       be left detached/on base, even if a step above raised or returned early.

    Returns the same report schema as ``build_report``. Never raises: git or
    subprocess failures are captured and returned as ``{"ok": False, ...}``.
    """
    try:
        original_ref = _current_ref(repo)
    except Exception as exc:  # noqa: BLE001 — git/subprocess unavailable or not a repo
        return {
            "ok": False, "base_failed": False, "head_passed": False,
            "same_identity": False, "red_is_legit": False, "tests": [],
            "reasons": [f"could not determine current git ref in {repo!r}: {type(exc).__name__}: {exc}"],
            "_warnings": [str(exc)],
        }

    try:
        ok, err = _git_checkout(repo, base_sha)
        if not ok:
            return {
                "ok": False, "base_failed": False, "head_passed": False,
                "same_identity": False, "red_is_legit": False, "tests": [],
                "reasons": [f"git checkout base ({base_sha}) failed: {err}"],
                "_warnings": [err] if err else [],
            }

        if apply_tests_fn is not None:
            try:
                apply_tests_fn(repo, head_sha, test_paths)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False, "base_failed": False, "head_passed": False,
                    "same_identity": False, "red_is_legit": False, "tests": [],
                    "reasons": [f"apply_tests_fn failed: {type(exc).__name__}: {exc}"],
                    "_warnings": [],
                }
        else:
            ok, err = _git_checkout_paths(repo, head_sha, test_paths)
            if not ok:
                return {
                    "ok": False, "base_failed": False, "head_passed": False,
                    "same_identity": False, "red_is_legit": False, "tests": [],
                    "reasons": [f"applying PR test files from head onto base failed: {err}"],
                    "_warnings": [err] if err else [],
                }

        try:
            base_output = run_tests_fn()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False, "base_failed": False, "head_passed": False,
                "same_identity": False, "red_is_legit": False, "tests": [],
                "reasons": [f"run_tests_fn (base) raised: {type(exc).__name__}: {exc}"],
                "_warnings": [],
            }

        ok, err = _git_checkout(repo, head_sha)
        if not ok:
            return {
                "ok": False, "base_failed": False, "head_passed": False,
                "same_identity": False, "red_is_legit": False, "tests": [],
                "reasons": [f"git checkout head ({head_sha}) failed: {err}"],
                "_warnings": [err] if err else [],
            }

        try:
            head_output = run_tests_fn()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False, "base_failed": False, "head_passed": False,
                "same_identity": False, "red_is_legit": False, "tests": [],
                "reasons": [f"run_tests_fn (head) raised: {type(exc).__name__}: {exc}"],
                "_warnings": [],
            }

        base_result = parse_odoo_test_result(base_output)
        head_result = parse_odoo_test_result(head_output)
        return build_report(base_result, head_result, is_bugfix=is_bugfix)
    finally:
        # Never leave the repo detached/on base — best-effort restore, and
        # never let a restore failure raise out of this function.
        try:
            _git_checkout(repo, original_ref)
        except Exception:  # noqa: BLE001
            pass


def make_odoo_test_runner(db, module, odoo_bin, test_tags):
    """Return a zero-arg callable suitable for ``run_tests_fn`` that shells out
    to Odoo's test runner and returns its combined stdout+stderr text.

    The returned callable does NOT touch git — call it only after the repo is
    already checked out to the ref you want tested (``run_replay`` does this).
    """
    def _runner():
        cmd = [odoo_bin, "-d", db, "-u", module, "--test-enable",
               "--stop-after-init", "--test-tags", test_tags]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except Exception as exc:  # noqa: BLE001
            return f"RUNNER_ERROR: {type(exc).__name__}: {exc}"
        return (proc.stdout or "") + "\n" + (proc.stderr or "")
    return _runner


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_text(path, warnings, label):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append(f"{label}: could not read {path!r} — {type(exc).__name__}: {exc}")
        return None


def main(argv=None):
    """Entry point: two modes, see module docstring for full usage.

        assembly (local, default): --base-output <base.txt> --head-output <head.txt> [--additive]
        run (orchestration):       --run --base <sha> --head <sha> --tests a.py b.py --db DB --module M [--repo DIR] [--additive]
    """
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="red_green_replay.py",
        description="CI-side proof that a test fails before the patch and passes after.",
    )
    parser.add_argument("--run", action="store_true",
                        help="Orchestration mode: git checkout base/head and run the tests")
    parser.add_argument("--base-output", default=None,
                        help="Assembly mode: path to captured BASE (pre-fix) test output")
    parser.add_argument("--head-output", default=None,
                        help="Assembly mode: path to captured HEAD (post-fix) test output")
    parser.add_argument("--base", default=None, help="Run mode: base git ref/sha (pre-fix)")
    parser.add_argument("--head", default=None, help="Run mode: head git ref/sha (post-fix)")
    parser.add_argument("--tests", nargs="+", default=None,
                        help="Run mode: PR test file path(s) to apply onto the base tree")
    parser.add_argument("--db", default=None, help="Run mode: Odoo database name")
    parser.add_argument("--module", default=None, help="Run mode: Odoo module to update/test")
    parser.add_argument("--repo", default=".", help="Run mode: repo directory (default: cwd)")
    parser.add_argument("--odoo-bin", default="odoo-bin", help="Run mode: path to odoo-bin")
    parser.add_argument("--test-tags", default=None,
                        help="Run mode: --test-tags value passed to odoo-bin (default: /<module>)")
    parser.add_argument("--additive", action="store_true",
                        help="Treat as an additive feature, not a bugfix — accept a "
                             "missing-field/method red as legitimate")
    ns = parser.parse_args(args)
    is_bugfix = not ns.additive

    if ns.run:
        missing = [name for name, val in
                   (("--base", ns.base), ("--head", ns.head), ("--tests", ns.tests),
                    ("--db", ns.db), ("--module", ns.module)) if not val]
        if missing:
            print(json.dumps({
                "ok": False, "base_failed": False, "head_passed": False,
                "same_identity": False, "red_is_legit": False, "tests": [],
                "reasons": [f"--run requires: {', '.join(missing)}"],
                "_warnings": [],
            }, indent=2))
            return
        test_tags = ns.test_tags or f"/{ns.module}"
        try:
            run_tests_fn = make_odoo_test_runner(ns.db, ns.module, ns.odoo_bin, test_tags)
            report = run_replay(ns.base, ns.head, ns.tests, run_tests_fn,
                                repo=ns.repo, is_bugfix=is_bugfix)
        except Exception as exc:  # noqa: BLE001 — git/subprocess unavailable, etc: never crash
            report = {
                "ok": False, "base_failed": False, "head_passed": False,
                "same_identity": False, "red_is_legit": False, "tests": [],
                "reasons": [f"replay failed: {type(exc).__name__}: {exc}"],
                "_warnings": [str(exc)],
            }
        print(json.dumps(report, indent=2, default=str))
        return

    # Assembly mode (default / local).
    if not ns.base_output or not ns.head_output:
        print(json.dumps({
            "ok": False, "base_failed": False, "head_passed": False,
            "same_identity": False, "red_is_legit": False, "tests": [],
            "reasons": ["assembly mode requires --base-output and --head-output"],
            "_warnings": [],
        }, indent=2))
        return

    warnings = []
    base_text = _read_text(ns.base_output, warnings, "base-output")
    head_text = _read_text(ns.head_output, warnings, "head-output")
    if base_text is None or head_text is None:
        print(json.dumps({
            "ok": False, "base_failed": False, "head_passed": False,
            "same_identity": False, "red_is_legit": False, "tests": [],
            "reasons": ["one or more output files could not be read"],
            "_warnings": warnings,
        }, indent=2))
        return

    base_result = parse_odoo_test_result(base_text)
    head_result = parse_odoo_test_result(head_text)
    report = build_report(base_result, head_result, is_bugfix=is_bugfix)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
