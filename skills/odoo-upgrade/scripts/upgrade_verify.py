#!/usr/bin/env python3
"""upgrade_verify.py — Runtime verification of a migrated module on the TARGET Odoo.

STATUS: integration-tested against a live Odoo 19 compose harness; that run
exposed the silent-skip false pass now guarded by the `module_not_loaded`
verdict. Re-run with Docker or a local Odoo 19 install for your own module.

What it does (single attempt — the fix LOOP is driven by the agent, see SKILL.md):
  1. Runs ``odoo-bin -d <db> -i|-u <module> --stop-after-init`` either via a local
     odoo-bin or through ``docker compose run``.
  2. Captures the full log, extracts every Python traceback and every
     ERROR/CRITICAL log line into structured JSON.
  3. Flags, per traceback, the deepest frame located inside YOUR addons path —
     that is where the agent should look first.
  4. Writes ``verify.json`` + raw ``verify.log`` to the output dir and exits with
     the Odoo process return code.

Verdicts (honest by construction):
  ok                  rc == 0, module actually loaded, no tracebacks, no CRITICAL
  ok_with_log_errors  rc == 0 but ERROR lines present — read them before celebrating
  module_not_loaded   Odoo exited 0 but never loaded the module (wrong version
                      series -> installable=False, unmet dependencies, or not on
                      the addons path) — see not_loaded_reasons. This is a FAIL.
  failed              rc != 0 or tracebacks/CRITICAL present
``ok`` means "the module installs/updates on the target runtime". It does NOT
mean business logic is correct — run the module's test tags next (--test-tags).

Usage (local odoo-bin):
  python upgrade_verify.py --module my_module --db verify19 \
      --odoo-bin ~/odoo/19.0/odoo-bin \
      --addons-path ~/odoo/19.0/addons,~/enterprise/19.0,~/custom \
      [--update] [--test-tags /my_module] [--out /tmp/odoo-ai/upgrade/my_module]

Usage (docker):
  python upgrade_verify.py --module my_module --db verify19 \
      --docker-compose docker/docker-compose.verify.yml \
      [--update] [--test-tags /my_module]

Stdlib only. License: MIT (part of the odoo-upgrade skill).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess
import sys
from pathlib import Path

RE_FRAME = re.compile(r'^  File "(?P<file>.+)", line (?P<line>\d+), in (?P<func>\S+)')
RE_LOGLINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} [\d:,]+ \d+ (?P<level>ERROR|CRITICAL|WARNING) "
    r"(?P<db>\S+) (?P<logger>\S+): (?P<msg>.*)$"
)
MAX_TB = 25
MAX_LOG_ERRORS = 60


def parse_output(text: str, addons_hints: list[str]) -> dict:
    lines = text.splitlines()
    tracebacks, log_errors = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "Traceback (most recent call last):":
            frames, j = [], i + 1
            while j < len(lines):
                m = RE_FRAME.match(lines[j])
                if m:
                    frames.append({"file": m["file"], "line": int(m["line"]), "func": m["func"]})
                    j += 1
                    # skip the source-echo line under the frame, if present
                    if j < len(lines) and lines[j].startswith("    "):
                        j += 1
                elif lines[j].startswith(("  ", "\t")) or lines[j].strip() == "":
                    j += 1  # chained-exception glue, blank lines
                else:
                    break
            exc_line = lines[j].strip() if j < len(lines) else ""
            exc_type, _, exc_msg = exc_line.partition(": ")
            custom = next(
                (f for f in reversed(frames)
                 if any(h and h in f["file"] for h in addons_hints)),
                None,
            )
            tracebacks.append({
                "exception": exc_type or "UnknownError",
                "message": exc_msg.strip(),
                "frames": frames[-8:],           # deepest frames are what matters
                "custom_frame": custom,          # None => failure is outside your addons
            })
            i = j + 1
            if len(tracebacks) >= MAX_TB:
                break
            continue
        if m := RE_LOGLINE.match(line):
            if m["level"] in ("ERROR", "CRITICAL") and len(log_errors) < MAX_LOG_ERRORS:
                log_errors.append({"level": m["level"], "logger": m["logger"],
                                   "message": m["msg"][:500]})
        i += 1
    return {"tracebacks": tracebacks, "log_errors": log_errors}


def build_command(args) -> list[str]:
    action = "-u" if args.update else "-i"
    odoo_args = ["-d", args.db, action, args.module, "--stop-after-init",
                 "--log-level=info", "--max-cron-threads=0"]
    if args.addons_path:
        odoo_args += [f"--addons-path={args.addons_path}"]
    if args.test_tags:
        odoo_args += ["--test-enable", f"--test-tags={args.test_tags}"]
    if args.odoo_bin:
        return [args.odoo_bin, *odoo_args]
    if args.docker_compose:
        return ["docker", "compose", "-f", args.docker_compose,
                "run", "--rm", args.docker_service, "odoo", *odoo_args]
    raise SystemExit("error: provide --odoo-bin or --docker-compose")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--module", required=True)
    ap.add_argument("--db", default="verify19")
    ap.add_argument("--odoo-bin")
    ap.add_argument("--docker-compose")
    ap.add_argument("--docker-service", default="odoo")
    ap.add_argument("--addons-path", help="local mode only; in docker mode set it in the compose file")
    ap.add_argument("--update", action="store_true", help="-u instead of -i (re-run after fixes)")
    ap.add_argument("--test-tags", help="also run tests, e.g. /my_module")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    out_dir = args.out or Path(f"/tmp/odoo-ai/upgrade/{args.module}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Bring the db up and wait for it FIRST — otherwise a cold `compose run`
    # races postgres startup and dies on connect, which is not a real failure
    # but would burn an iteration (observed twice in the field). Best-effort.
    if args.docker_compose:
        subprocess.run(["docker", "compose", "-f", args.docker_compose,
                        "up", "-d", "--wait", "db"],
                       capture_output=True, timeout=180)

    cmd = build_command(args)
    started = _dt.datetime.now(_dt.timezone.utc)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
        rc, output = proc.returncode, proc.stdout + "\n" + proc.stderr
    except subprocess.TimeoutExpired as e:
        rc = -1
        output = ((e.stdout or "") if isinstance(e.stdout, str) else "") + \
                 "\n[upgrade_verify] TIMEOUT after %ss" % args.timeout
    except FileNotFoundError as e:
        print(f"error: cannot execute {cmd[0]!r}: {e}", file=sys.stderr)
        return 2

    (out_dir / "verify.log").write_text(output)
    hints = [args.module] + ([p.strip() for p in args.addons_path.split(",")]
                             if args.addons_path else [])
    parsed = parse_output(output, hints)

    # Odoo exits 0 even when it silently skips the module (installable=False,
    # unmet dependencies, not on addons path) — require positive proof of load.
    module_loaded = bool(re.search(
        rf"Loading module {re.escape(args.module)} \(\d+/\d+\)", output))
    not_loaded_reasons = [
        ln.strip() for ln in output.splitlines()
        if args.module in ln and re.search(
            r"incompatible version|invalid module names|unmet dependencies|installable", ln, re.I)
    ][:5]

    if not module_loaded:
        verdict = "module_not_loaded"
    elif rc == 0 and not parsed["tracebacks"] and \
            not any(e["level"] == "CRITICAL" for e in parsed["log_errors"]):
        verdict = "ok" if not parsed["log_errors"] else "ok_with_log_errors"
    else:
        verdict = "failed"

    result = {
        "module": args.module,
        "action": "update" if args.update else "install",
        "test_tags": args.test_tags,
        "command": cmd,
        "returncode": rc,
        "verdict": verdict,
        "module_loaded": module_loaded,
        **({"not_loaded_reasons": not_loaded_reasons} if not module_loaded else {}),
        "verdict_note": ("'ok' = installs on target runtime; business-logic "
                         "correctness still requires tests + human review."),
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round((_dt.datetime.now(_dt.timezone.utc) - started).total_seconds(), 1),
        **parsed,
        "log_file": str(out_dir / "verify.log"),
    }
    (out_dir / "verify.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))

    print(f"wrote {out_dir/'verify.json'}  verdict={verdict} rc={rc} "
          f"tracebacks={len(parsed['tracebacks'])} log_errors={len(parsed['log_errors'])}")
    for tb in parsed["tracebacks"][:3]:
        loc = tb["custom_frame"] or (tb["frames"][-1] if tb["frames"] else {})
        print(f"  [{tb['exception']}] {tb['message'][:100]}"
              f"  @ {loc.get('file','?')}:{loc.get('line','?')}")
    return 0 if verdict.startswith("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
