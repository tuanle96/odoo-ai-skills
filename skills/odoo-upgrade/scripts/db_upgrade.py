#!/usr/bin/env python3
"""db_upgrade.py — Whole-DATABASE upgrade rehearsal (Community path, OpenUpgrade).

Orchestrates the docker/docker-compose.upgrade.yml harness:

  seed     create a fresh source-version db (rehearsal without a prod dump)
  restore  load a pg_dump into the harness postgres
  upgrade  run OpenUpgrade on the db: odoo19 -u all with --upgrade-path
  check    plain post-upgrade `-u <modules>` on the target (no OpenUpgrade) —
           this is where your PORTED custom modules must come up clean
  full     restore -> upgrade -> check

Every step writes a structured JSON verdict (same traceback parser as
upgrade_verify.py) to <out>/<step>.json + <step>.log.

HONESTY CONTRACT
----------------
- This is a REHEARSAL harness. A green `full` run on a copy is the entry ticket
  to planning production cutover (backups, filestore, low-activity window,
  tested rollback) — it is not the cutover itself.
- Enterprise / Odoo.sh / Online databases: do NOT use this path; request an
  upgraded test db from upgrade.odoo.com (see SKILL.md Phase 5). OpenUpgrade
  covers Community.
- `verdict=ok` means the step's Odoo process exited clean on this copy; data
  CORRECTNESS (amounts, reconciliations, stock levels) still needs functional
  checks by a human.
- OpenUpgrade is AGPL and is only ever invoked as an external tool from a
  user-provided checkout; openupgradelib is pip-installed inside the throwaway
  container, not vendored.

Usage:
  export OPENUPGRADE=~/src/OpenUpgrade CUSTOM_ADDONS=/path/to/custom
  python3 db_upgrade.py seed    --db up19 --modules base,contacts -C docker/docker-compose.upgrade.yml
  python3 db_upgrade.py restore --db up19 --dump prod.dump        -C docker/docker-compose.upgrade.yml
  python3 db_upgrade.py upgrade --db up19                         -C docker/docker-compose.upgrade.yml
  python3 db_upgrade.py check   --db up19 --modules all           -C docker/docker-compose.upgrade.yml
  python3 db_upgrade.py full    --db up19 --dump prod.dump        -C docker/docker-compose.upgrade.yml

Stdlib only. License: MIT (part of the odoo-upgrade skill).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("upgrade_verify", HERE / "upgrade_verify.py")
_uv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_uv)
parse_output = _uv.parse_output

ODOO_BASE_ARGS = ["--stop-after-init", "--log-level=info", "--max-cron-threads=0"]
# odoo:19 image default addons_path is /mnt/extra-addons (+ its own dist-packages);
# passing --addons-path OVERRIDES it, so list everything explicitly.
OU_ADDONS_PATH = "/opt/openupgrade,/mnt/extra-addons,/usr/lib/python3/dist-packages/odoo/addons"


# --------------------------------------------------------------------------- #
# Command builders (pure — unit-tested)
# --------------------------------------------------------------------------- #

def compose(compose_file: str, *tail: str) -> list[str]:
    return ["docker", "compose", "-f", compose_file, *tail]


def cmd_seed(compose_file: str, db: str, modules: str) -> list[str]:
    return compose(compose_file, "run", "--rm", "odoo18", "odoo",
                   "-d", db, "-i", modules, *ODOO_BASE_ARGS)


def cmd_restore(compose_file: str, db: str, dump: Path) -> list[list[str]]:
    """Three steps: db up, createdb, pg_restore/psql (streamed via stdin)."""
    loader = (["psql", "-q", "-U", "odoo", "-d", db]
              if dump.suffix == ".sql"
              else ["pg_restore", "-U", "odoo", "-d", db, "--no-owner"])
    return [
        compose(compose_file, "up", "-d", "--wait", "db"),
        compose(compose_file, "exec", "-T", "db", "createdb", "-U", "odoo", db),
        compose(compose_file, "exec", "-T", "db", *loader),
    ]


def cmd_upgrade(compose_file: str, db: str) -> list[str]:
    """OpenUpgrade pass: -u all on the target version with the migration scripts.

    Entrypoint is bypassed (we need the pip step), so the db connection params
    the image entrypoint would derive from HOST/USER/PASSWORD are passed
    explicitly. openupgradelib installs --user into the odoo home volume."""
    inner = ("pip install --quiet --user --break-system-packages openupgradelib && "
             f"odoo -d {db} --db_host=db --db_user=odoo --db_password=odoo -u all "
             "--upgrade-path=/opt/openupgrade/openupgrade_scripts/scripts "
             "--load=base,web,openupgrade_framework "
             f"--addons-path={OU_ADDONS_PATH} "
             + " ".join(ODOO_BASE_ARGS))
    return compose(compose_file, "run", "--rm",
                   "--entrypoint", "bash", "odoo19", "-c", inner)


def cmd_check(compose_file: str, db: str, modules: str) -> list[str]:
    return compose(compose_file, "run", "--rm", "odoo19", "odoo",
                   "-d", db, "-u", modules, *ODOO_BASE_ARGS)


def verdict_of(rc: int, parsed: dict) -> str:
    if rc != 0 or parsed["tracebacks"] or \
            any(e["level"] == "CRITICAL" for e in parsed["log_errors"]):
        return "failed"
    return "ok" if not parsed["log_errors"] else "ok_with_log_errors"


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run_step(name: str, cmd: list[str], out_dir: Path, timeout: int,
             stdin_file: Path | None = None, hints: list[str] | None = None) -> dict:
    started = _dt.datetime.now(_dt.timezone.utc)
    stdin = stdin_file.open("rb") if stdin_file else None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, stdin=stdin)
        rc, output = proc.returncode, proc.stdout + "\n" + proc.stderr
    except subprocess.TimeoutExpired as e:
        rc = -1
        output = ((e.stdout or "") if isinstance(e.stdout, str) else "") + \
                 f"\n[db_upgrade] TIMEOUT after {timeout}s"
    finally:
        if stdin:
            stdin.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.log").write_text(output)
    parsed = parse_output(output, hints or [])
    result = {
        "step": name,
        "command": cmd,
        "returncode": rc,
        "verdict": verdict_of(rc, parsed),
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round((_dt.datetime.now(_dt.timezone.utc) - started).total_seconds(), 1),
        **parsed,
        "log_file": str(out_dir / f"{name}.log"),
    }
    (out_dir / f"{name}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[{name}] verdict={result['verdict']} rc={rc} "
          f"tracebacks={len(parsed['tracebacks'])} log_errors={len(parsed['log_errors'])} "
          f"({result['duration_s']}s)")
    for tb in parsed["tracebacks"][:3]:
        loc = tb["custom_frame"] or (tb["frames"][-1] if tb["frames"] else {})
        print(f"    [{tb['exception']}] {tb['message'][:90]} @ {loc.get('file','?')}:{loc.get('line','?')}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", choices=["seed", "restore", "upgrade", "check", "full"])
    ap.add_argument("--db", required=True)
    ap.add_argument("-C", "--docker-compose", required=True)
    ap.add_argument("--dump", type=Path, help="pg_dump file (.dump/-Fc or .sql) for restore/full")
    ap.add_argument("--modules", default="base",
                    help="seed: modules to install; check: modules to update (or 'all')")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--out", type=Path, default=Path("/tmp/odoo-ai/upgrade/_db"))
    args = ap.parse_args()
    cf, db = args.docker_compose, args.db

    def restore() -> dict:
        if not args.dump or not args.dump.is_file():
            raise SystemExit("error: restore needs --dump <file>")
        up, createdb, load = cmd_restore(cf, db, args.dump)
        subprocess.run(up, check=True)
        subprocess.run(createdb)  # may already exist — loader will tell
        return run_step("restore", load, args.out, args.timeout, stdin_file=args.dump)

    steps = {
        "seed": lambda: run_step("seed", cmd_seed(cf, db, args.modules), args.out, args.timeout),
        "restore": restore,
        "upgrade": lambda: run_step("upgrade", cmd_upgrade(cf, db), args.out, args.timeout,
                                    hints=["/mnt/extra-addons"]),
        "check": lambda: run_step("check", cmd_check(cf, db, args.modules), args.out,
                                  args.timeout, hints=["/mnt/extra-addons"]),
    }
    order = ["restore", "upgrade", "check"] if args.step == "full" else [args.step]
    for s in order:
        result = steps[s]()
        if not result["verdict"].startswith("ok"):
            print(f"stopping at step '{s}' — see {result['log_file']}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
