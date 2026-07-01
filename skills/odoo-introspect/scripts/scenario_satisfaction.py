"""
Scenario satisfaction gate (local, no Odoo) — turns the risk scenarios that
scenario_gen.py emits as TODO stubs into an ENFORCED check.

scenario_gen.py only tells the agent which scenario keys (non_admin,
multi_company, batch, ...) a change requires and renders self.fail() stubs for
each — nothing stops a developer from leaving the stub unimplemented, or
implementing it in a way that never actually exercises the scenario (e.g. a
"non_admin" test that never switches away from uid 1). This script closes that
gap: given the REQUIRED scenario keys and OBSERVATIONS of what the test run
actually did at runtime (collected by the sibling runtime_path_probe script),
it decides whether each required scenario was genuinely satisfied and gates on
``required - satisfied == empty``.

No Odoo connection required. All helpers are pure Python, unit-testable, and
read only from the filesystem.

Usage
-----
    python3 scenario_satisfaction.py --scenarios <scenarios.json> \\
        --observations <observations.json>

``scenarios.json`` is either the output of scenario_gen.py (a dict with a
top-level ``"scenarios": [{"key": ...}, ...]`` list) or a plain
``{"required": [...]}`` dict of keys.

``observations.json`` summarises runtime evidence collected by the CI test
run:
    {
      "uids_seen": [1, 17],
      "max_recordset_len": 2,
      "max_create_vals_len": 2,
      "companies_seen": [1, 2],
      "allowed_company_sets": [[1], [2]],
      "phases_covered": ["at_install", "post_install"],
      "install_modes": ["-i", "-u"],
      "raised_exceptions": ["UserError"],
      "access_errors_seen": true,
      "locked_period_usererror": true
    }
All keys are optional; a missing key is treated as "not observed".

Output: pure JSON to stdout. Exit code is always 0 so a non-zero return never
suppresses the JSON output.
"""
import argparse
import json
import sys
from pathlib import Path

_SUPERUSER_UID = 1


# ---------------------------------------------------------------------------
# Pure helpers (no Odoo, unit-testable)
# ---------------------------------------------------------------------------

def required_from_scenarios(doc):
    """Extract the required scenario key list from a scenarios.json-shaped doc.

    Accepts either scenario_gen.py's ``{"scenarios": [{"key": ...}, ...]}``
    shape or a plain ``{"required": [...]}`` list of keys. Returns [] for any
    other/missing shape (never raises).
    """
    if not isinstance(doc, dict):
        return []
    scenarios = doc.get("scenarios")
    if isinstance(scenarios, list):
        return [s["key"] for s in scenarios if isinstance(s, dict) and isinstance(s.get("key"), str)]
    required = doc.get("required")
    if isinstance(required, list):
        return [k for k in required if isinstance(k, str)]
    return []


def _non_admin_uids(uids_seen):
    """uids_seen entries that are neither the superuser (1) nor falsy/invalid."""
    if not isinstance(uids_seen, list):
        return []
    return [u for u in uids_seen if isinstance(u, int) and not isinstance(u, bool) and u != _SUPERUSER_UID]


def _check_non_admin(obs):
    uids = _non_admin_uids(obs.get("uids_seen"))
    if uids:
        return True, f"non-admin uid(s) seen: {uids}"
    return False, "no non-admin uid observed in uids_seen"


def _check_at_install_vs_post_install(obs):
    phases = obs.get("phases_covered")
    phases = set(phases) if isinstance(phases, list) else set()
    needed = {"at_install", "post_install"}
    if needed <= phases:
        return True, "both at_install and post_install phases covered"
    missing = sorted(needed - phases)
    return False, f"missing phase(s): {missing}"


def _check_multi_company(obs):
    companies = obs.get("companies_seen")
    companies = companies if isinstance(companies, list) else []
    distinct = sorted(set(companies))
    if len(distinct) >= 2:
        return True, f"companies_seen: {distinct}"
    sets = obs.get("allowed_company_sets")
    if isinstance(sets, list):
        distinct_sets = {tuple(s) for s in sets if isinstance(s, list)}
        if len(distinct_sets) >= 2:
            return True, f"allowed_company_sets variety: {sorted(distinct_sets)}"
    return False, f"companies_seen: {distinct} (need >= 2 distinct companies or allowed_company_sets variety)"


def _check_batch(obs):
    max_rec = obs.get("max_recordset_len")
    max_rec = max_rec if isinstance(max_rec, int) and not isinstance(max_rec, bool) else 0
    max_vals = obs.get("max_create_vals_len")
    max_vals = max_vals if isinstance(max_vals, int) and not isinstance(max_vals, bool) else 0
    if max_rec >= 2:
        return True, f"max_recordset_len: {max_rec}"
    if max_vals >= 2:
        return True, f"max_create_vals_len: {max_vals}"
    return False, f"max_recordset_len: {max_rec}, max_create_vals_len: {max_vals} (need >= 2)"


def _check_upgrade_i_and_u(obs):
    modes = obs.get("install_modes")
    modes = set(modes) if isinstance(modes, list) else set()
    needed = {"-i", "-u"}
    if needed <= modes:
        return True, "both -i and -u install modes covered"
    missing = sorted(needed - modes)
    return False, f"missing install mode(s): {missing}"


def _check_locked_period(obs):
    # Require the SPECIFIC locked-period signal — a bare UserError anywhere in the
    # run is far too loose (Oracle: any UserError would satisfy this). The observer
    # must set this flag only when the operation on a lock-dated/hash-secured record
    # actually raised.
    if obs.get("locked_period_usererror") is True:
        return True, "locked_period_usererror observed"
    return False, ("no locked_period_usererror flag — a generic UserError does NOT "
                   "prove the locked-period path was exercised")


def _check_record_rules(obs):
    if obs.get("access_errors_seen") is True:
        return True, "access_errors_seen observed"
    companies = obs.get("companies_seen")
    companies = companies if isinstance(companies, list) else []
    non_admin = _non_admin_uids(obs.get("uids_seen"))
    if len(set(companies)) >= 2 and non_admin:
        return True, f"companies_seen: {sorted(set(companies))}, non-admin uid(s): {non_admin}"
    return False, "no access_errors_seen and no (multi-company + non-admin) combination observed"


_PREDICATES = {
    "non_admin": _check_non_admin,
    "at_install_vs_post_install": _check_at_install_vs_post_install,
    "multi_company": _check_multi_company,
    "batch": _check_batch,
    "upgrade_i_and_u": _check_upgrade_i_and_u,
    "locked_period": _check_locked_period,
    "record_rules": _check_record_rules,
}


def satisfied_predicate(key, observations):
    """Evaluate whether scenario *key* is satisfied by *observations*.

    Returns ``(ok: bool, evidence: str)``. Never raises: a missing/None
    observations dict or an unknown key both resolve to ``ok=False`` with a
    clear evidence string.
    """
    obs = observations if isinstance(observations, dict) else {}
    predicate = _PREDICATES.get(key)
    if predicate is None:
        return False, f"no predicate for {key}"
    return predicate(obs)


def evaluate(required_keys, observations):
    """Evaluate every required scenario key against *observations*.

    Returns::

        {
          "required": [...],
          "satisfied": {key: {"ok": bool, "evidence": str}},
          "unsatisfied": [keys with ok False],
          "summary": {"required": N, "satisfied": M},
          "ok": (unsatisfied == []),
        }
    """
    required = [k for k in (required_keys or []) if isinstance(k, str)]
    satisfied = {}
    unsatisfied = []
    for key in required:
        ok, evidence = satisfied_predicate(key, observations)
        satisfied[key] = {"ok": ok, "evidence": evidence}
        if not ok:
            unsatisfied.append(key)
    satisfied_count = sum(1 for v in satisfied.values() if v["ok"])
    return {
        "required": required,
        "satisfied": satisfied,
        "unsatisfied": unsatisfied,
        "summary": {"required": len(required), "satisfied": satisfied_count},
        "ok": unsatisfied == [],
    }


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_json(path):
    """Read + parse a JSON file. Returns (doc, warning_or_None)."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        return None, f"{path}: could not read file — {type(exc).__name__}: {exc}"
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, f"{path}: parse error — {type(exc).__name__}: {exc}"


def main(argv=None):
    """Entry point: ``scenario-satisfaction --scenarios <f> --observations <f>``."""
    parser = argparse.ArgumentParser(prog="scenario-satisfaction")
    parser.add_argument("--scenarios", required=True)
    parser.add_argument("--observations", required=True)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    warnings = []

    scenarios_doc, warn = _load_json(args.scenarios)
    if warn:
        warnings.append(warn)
    required = required_from_scenarios(scenarios_doc) if scenarios_doc is not None else []

    observations, warn = _load_json(args.observations)
    if warn:
        warnings.append(warn)

    if warnings:
        report = {"ok": False, "_warnings": warnings}
        report.update(evaluate(required, observations))
        report["ok"] = False
    else:
        report = evaluate(required, observations)
        report["_warnings"] = warnings

    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    main()
