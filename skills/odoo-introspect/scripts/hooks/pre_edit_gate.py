#!/usr/bin/env python3
"""
Claude Code PreToolUse hook — enforce no-introspect-no-edit for Odoo files.

Wire this on Edit|Write|MultiEdit (see references/enforcement-hooks.md). Before
the agent edits a file INSIDE an Odoo module, it runs `odoo-ai gate-edit` and, if
the touched model has no introspection evidence (or the patch has a blocking
anti-pattern), BLOCKS the edit and tells the agent the exact `odoo-ai` command to
run first. This is the Oracle's #1 lever: it converts the suite's tools from
"available" to "inevitable" without adding prompt friction.

Protocol: reads the tool call as JSON on stdin; exit 0 = allow, exit 2 = block
(stderr is fed back to the agent as the reason). Fail-OPEN: any hook error or
missing odoo-ai allows the edit (a guardrail must never brick legitimate work).

Protocol: reads the tool call as JSON on stdin; exit 0 = allow, exit 2 = block
(stderr is fed back to the agent as the reason). Fail-OPEN by default: any hook
error or missing odoo-ai allows the edit (a guardrail must never brick legitimate
work). Set ODOO_AI_GATE_STRICT=1 to fail-CLOSED instead — if the gate itself can't
run, block the edit (for teams that want hard enforcement over convenience).

Config (env, all optional):
    ODOO_AI_BIN   path to the odoo-ai CLI (default: ../odoo-ai next to this hook)
    ODOO_AI_OUT   evidence dir gate-edit reads (default: /tmp/odoo-ai)
    ODOO_AI_GATE_DISABLE=1   bypass entirely
    ODOO_AI_GATE_STRICT=1    fail closed (block) when the gate can't run
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def _allow():
    sys.exit(0)


def _gate_error(msg):
    """The gate itself could not run. Default: fail open (allow). STRICT: block."""
    if os.environ.get("ODOO_AI_GATE_STRICT") == "1":
        print(f"🛑 odoo-ai gate could not run and ODOO_AI_GATE_STRICT=1 → blocking. {msg}",
              file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


def main():
    if os.environ.get("ODOO_AI_GATE_DISABLE") == "1":
        _allow()
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        _allow()
    ti = data.get("tool_input") or data.get("toolInput") or {}
    path = ti.get("file_path") or ti.get("path")
    if not path:
        _allow()
    p = Path(path)
    if p.suffix not in (".py", ".xml"):
        _allow()
    # only gate files that live inside an Odoo module (has a manifest ancestor)
    in_module = any((anc / "__manifest__.py").exists() or (anc / "__openerp__.py").exists()
                    for anc in [p] + list(p.parents))
    if not in_module:
        _allow()

    odoo_ai = os.environ.get("ODOO_AI_BIN") or str(Path(__file__).resolve().parent.parent / "odoo-ai")
    argv = [sys.executable, odoo_ai, "--json", "gate-edit", str(p)]
    if os.environ.get("ODOO_AI_OUT"):
        argv += ["--evidence-dir", os.environ["ODOO_AI_OUT"]]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        res = json.loads(proc.stdout or "{}")
    except Exception as e:  # noqa: BLE001 — the gate couldn't run (missing odoo-ai,
        _gate_error(f"{type(e).__name__}: {e}")  # timeout, bad JSON): fail open, or
        return                                    # fail closed under ODOO_AI_GATE_STRICT

    # A CLI that ran but produced no usable decision (empty/garbage stdout, a crash
    # that still exited 0) must not be read as "allow" — that's the silent-bypass the
    # audit flagged. Treat it as a gate error (fail open by default, closed under STRICT).
    decision = res.get("decision")
    if decision not in ("allow", "block"):
        _gate_error("gate-edit returned no valid decision (empty/garbage output)")
        return
    if decision == "block":
        lines = ["🛑 odoo-ai: read ground truth before editing this Odoo model "
                 "(no-introspect-no-edit)."]
        for r in res.get("reasons", []):
            lines.append(f"  - {r}")
        if res.get("required_commands"):
            lines.append("Run first, then retry the edit:")
            for c in res["required_commands"]:
                lines.append(f"    {c}")
        print("\n".join(lines), file=sys.stderr)
        sys.exit(2)   # block the tool call; stderr is shown to the agent
    _allow()


if __name__ == "__main__":
    main()
