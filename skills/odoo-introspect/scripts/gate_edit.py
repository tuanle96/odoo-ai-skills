"""
Odoo edit-precondition gate (Layer K — enforcement) — LOCAL, no DB / no odoo-bin.

The Oracle's #1 finding: "even perfect tools ≠ used tools." The suite's whole
value collapses if the agent edits an Odoo model from memory without ever reading
ground truth. This makes the suite's core rule — READ GROUND TRUTH BEFORE YOU
WRITE — an EXECUTABLE precondition instead of a prompt the agent can skip.

Given the files an agent is about to edit, it:
  1. extracts the Odoo models they touch (`_name` / `_inherit` in .py; the
     `<field name="model">` / `model=` targets in .xml),
  2. checks the evidence dir (default /tmp/odoo-ai) for an introspection brief of
     each touched model — i.e. did the agent actually run `odoo-ai all <model>`?,
  3. runs the static validator (patch_validator) on the changed files,
  4. decides allow / block, and emits the EXACT `odoo-ai` commands to unblock.

Wire it as a Claude Code PreToolUse hook on Edit|Write (see
odoo-introspect/references/enforcement-hooks.md) so the edit is *inevitably*
preceded by introspection — "no-introspect-no-edit". Pure decision logic
(extract_models_*, evidence_for_model, decide) is unit-testable without Odoo.

Usage (via the CLI):
    odoo-ai gate-edit addons/my_module/models/sale_order.py
    odoo-ai gate-edit <files...> --evidence-dir /tmp/odoo-ai

Output: pure JSON on stdout (LOCAL tool — captured by the CLI, no sentinels).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# A model name: dotted, lowercase-ish identifiers (sale.order, account.move.line).
_MODEL_RE = r"[a-zA-Z_][\w.]*\.[\w.]+"
_NAME_RE = re.compile(r"_name\s*=\s*['\"](" + _MODEL_RE + r")['\"]")
_INHERIT_SINGLE_RE = re.compile(r"_inherit\s*=\s*['\"](" + _MODEL_RE + r")['\"]")
_INHERIT_LIST_RE = re.compile(r"_inherit\s*=\s*\[([^\]]*)\]", re.S)
_QUOTED_RE = re.compile(r"['\"](" + _MODEL_RE + r")['\"]")
_XML_MODEL_FIELD_RE = re.compile(r"<field\s+name=['\"]model['\"]\s*>\s*(" + _MODEL_RE + r")\s*</field>")
_XML_MODEL_ATTR_RE = re.compile(r"\bmodel=['\"](" + _MODEL_RE + r")['\"]")

# Technical/plumbing models we never demand a business brief for (a view record's
# own model is ir.ui.view, etc.). Mirrors entrypoint_surface's filter.
_TECHNICAL_PREFIXES = ("ir.", "base.", "bus.", "report.", "mail.message",
                       "mail.followers", "res.config")


# --- Pure helpers (no Odoo / no FS — unit-testable) --------------------------
def is_technical_model(name):
    return (not name) or any(name == p or name.startswith(p) for p in _TECHNICAL_PREFIXES)


def extract_models_from_python(source):
    """Models a .py touches: `_name`/`_inherit` (single or list). Returns a set."""
    out = set()
    for m in _NAME_RE.finditer(source or ""):
        out.add(m.group(1))
    for m in _INHERIT_SINGLE_RE.finditer(source or ""):
        out.add(m.group(1))
    for block in _INHERIT_LIST_RE.finditer(source or ""):
        for q in _QUOTED_RE.finditer(block.group(1)):
            out.add(q.group(1))
    return out


def extract_models_from_xml(source):
    """Business models an .xml targets: `<field name="model">x</field>` and
    `model="x"` attrs (filtering the technical ir.* the record itself is)."""
    out = set()
    for m in _XML_MODEL_FIELD_RE.finditer(source or ""):
        out.add(m.group(1))
    for m in _XML_MODEL_ATTR_RE.finditer(source or ""):
        if not is_technical_model(m.group(1)):
            out.add(m.group(1))
    return {x for x in out if not is_technical_model(x)}


def evidence_for_model(model, evidence_names):
    """True if the evidence dir holds an introspection brief for `model`.

    `odoo-ai all/brief/surface` write `<model_with_dots_as_underscores>.<step>.json`.
    We accept brief / all-derived / surface / capabilities as 'was introspected'.
    """
    stem = model.replace(".", "_")
    wanted = {f"{stem}.brief.json", f"{stem}.capabilities.json",
              f"{stem}.surface.json", f"{stem}.metadata.json", f"{stem}.entrypoints.json"}
    return any(n in evidence_names for n in wanted)


def decide(touched_models, evidence_names, validate_blocking, has_validator):
    """Allow/block decision + the commands to unblock. Pure.

    Block when a touched business model has NO introspection evidence, or the
    validator found a blocking anti-pattern. Technical models are never required.
    """
    business = sorted(m for m in touched_models if not is_technical_model(m))
    missing = [m for m in business if not evidence_for_model(m, evidence_names)]
    reasons, required = [], []
    for m in missing:
        reasons.append(f"no introspection evidence for {m} — read ground truth before editing it")
        required.append(f"odoo-ai all {m}")
    if has_validator and validate_blocking > 0:
        reasons.append(f"{validate_blocking} blocking anti-pattern(s) in the patch")
        required.append("odoo-ai validate <changed files>  # fix the blocking findings")
    allow = not missing and not (has_validator and validate_blocking > 0)
    return {
        "decision": "allow" if allow else "block",
        "touched_models": business,
        "missing_evidence": missing,
        "validate_blocking": validate_blocking if has_validator else None,
        "required_commands": required,
        "reasons": reasons or ["all touched models have introspection evidence; "
                               "no blocking anti-patterns"],
    }


# --- IO glue (kept thin; the decision above is pure) -------------------------
def _classify(path):
    p = path.lower()
    if p.endswith(".py"):
        return "py"
    if p.endswith(".xml"):
        return "xml"
    return "other"


def _run_validator(files):
    """Best-effort: import patch_validator and count blocking findings.

    Returns (has_validator, blocking_count). Never raises — if the validator
    isn't importable the gate still enforces the evidence precondition.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import patch_validator as PV
    except Exception:  # noqa: BLE001
        return False, 0
    blocking = 0
    fn = getattr(PV, "validate_paths", None) or getattr(PV, "run_paths", None)
    try:
        if fn:
            report = fn(files)
            blocking = (report.get("summary", {}) or {}).get("blocking", 0)
        else:
            # fall back to a subprocess call to the validator's CLI
            import subprocess
            proc = subprocess.run([sys.executable, str(Path(__file__).resolve().parent /
                                   "patch_validator.py"), *files],
                                  capture_output=True, text=True, timeout=120)
            data = json.loads(proc.stdout or "{}")
            blocking = (data.get("summary", {}) or {}).get("blocking", 0)
        return True, blocking
    except Exception:  # noqa: BLE001
        return False, 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gate-edit")
    ap.add_argument("cmd", nargs="?", default="gate")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--evidence-dir", default=os.environ.get("ODOO_AI_OUT", "/tmp/odoo-ai"))
    ap.add_argument("--no-validate", action="store_true")
    a = ap.parse_args(argv)

    touched = set()
    read_errors = []
    for f in a.files:
        try:
            src = Path(f).read_text(errors="replace")
        except Exception as e:  # noqa: BLE001
            read_errors.append(f"{f}: {type(e).__name__}")
            continue
        kind = _classify(f)
        if kind == "py":
            touched |= extract_models_from_python(src)
        elif kind == "xml":
            touched |= extract_models_from_xml(src)

    # collect every filename anywhere under the evidence dir (CLI writes to
    # /tmp/odoo-ai/<label>/<model>.<step>.json, so walk it)
    evidence_names = set()
    ev_root = Path(a.evidence_dir)
    if ev_root.is_dir():
        for p in ev_root.rglob("*.json"):
            evidence_names.add(p.name)

    py_files = [f for f in a.files if _classify(f) == "py"]
    has_validator, blocking = (False, 0)
    if not a.no_validate and py_files:
        has_validator, blocking = _run_validator(py_files)

    result = decide(touched, evidence_names, blocking, has_validator)
    result["mode"] = "gate-edit"
    result["evidence_dir"] = str(a.evidence_dir)
    result["files"] = a.files
    if py_files and not has_validator and not a.no_validate:
        # don't downgrade silently: say the validator step was skipped, so a clean
        # "allow" isn't mistaken for "validator passed".
        result["_validator_note"] = ("static validator did not run (patch_validator "
                                     "unavailable) — this is an evidence-presence check only")
    if read_errors:
        result["_read_errors"] = read_errors
    result["_note"] = ("Wire as a Claude Code PreToolUse hook on Edit|Write so this "
                       "runs BEFORE every Odoo edit. decision=block → run the "
                       "required_commands first. See enforcement-hooks.md.")
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
