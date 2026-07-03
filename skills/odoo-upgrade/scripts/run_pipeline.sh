#!/usr/bin/env bash
# run_pipeline.sh — thin orchestrator for one module. Stages skip gracefully
# when an external tool is absent. AGPL tools (odoo-module-migrate) are ONLY
# invoked as external CLIs — never vendor their code into this repo.
set -uo pipefail
MODULE_PATH=${1:?usage: run_pipeline.sh <module_path> [manifest.json]}
MANIFEST=${2:-references/manifest_18_19.json}
MOD=$(basename "$MODULE_PATH"); OUT=/tmp/odoo-ai/upgrade/$MOD; mkdir -p "$OUT"
HERE=$(cd "$(dirname "$0")/.." && pwd)

banner(){ printf '\n=== [%s] %s ===\n' "$1" "$2"; }

banner 0 "TAQAT precheck (MIT, vendored) — syntax-pattern scan"
( cd "$HERE/references/vendor/taqat-odoo-upgrade" && \
  python3 -m scripts.precheck "$MODULE_PATH" | tee "$OUT/precheck.txt" ) || true

banner 1a "official odoo-bin upgrade_code (needs ODOO19_SRC)"
if [ -n "${ODOO19_SRC:-}" ] && [ -x "$ODOO19_SRC/odoo-bin" ]; then
  python3 "$ODOO19_SRC/odoo-bin" upgrade_code --from 18.0 --to 19.0 \
    --addons-path "$(dirname "$MODULE_PATH")" 2>&1 | tee "$OUT/upgrade_code.txt" || true
else echo "skip: set ODOO19_SRC to your odoo 19 checkout"; fi

banner 1b "OCA odoo-module-migrate (AGPL — external CLI only)"
if command -v odoo-module-migrate >/dev/null; then
  odoo-module-migrate --directory "$(dirname "$MODULE_PATH")" --modules "$MOD" \
    --init-version-name 18.0 --target-version-name 19.0 --no-commit \
    2>&1 | tee "$OUT/module_migrate.txt" || true
else echo "skip: pip install odoo-module-migrator"; fi

banner 2 "manifest-driven brief (this skill)"
case "$MANIFEST" in /*) MF="$MANIFEST";; *) MF="$HERE/$MANIFEST";; esac
python3 "$HERE/scripts/upgrade_brief.py" --module "$MODULE_PATH" \
  --manifest "$MF" --out "$OUT/brief.json"

banner 3 "NEXT (agent loop — see SKILL.md)"
echo "fix findings in $OUT/brief.json, then:"
echo "  python3 scripts/upgrade_verify.py --module $MOD --db verify19 --docker-compose docker/docker-compose.verify.yml"
