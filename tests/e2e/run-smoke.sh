#!/bin/bash
# docker-compose E2E entrypoint: init a clean DB (base + sale, no demo) then run
# the introspection integration smoke (all layers + the new Layer K: surface /
# esg / eval) against it. Invoked per Odoo version by docker-compose.e2e.yml.
set -euo pipefail
DB="${ODOO_DB:-e2e}"
CONF=/repo/tests/e2e/odoo-e2e.conf

echo "================================================================"
echo "  E2E [$DB]  ·  $(odoo --version 2>/dev/null || echo odoo) "
echo "================================================================"

echo "--- init: install base,sale (no demo) ---"
odoo -d "$DB" -c "$CONF" -i base,sale --stop-after-init --log-level=warn

echo "--- smoke: all introspection layers + gates + Layer K (surface/esg/eval) ---"
exec env ODOO_DB="$DB" ODOO_CONF="$CONF" ODOO_BIN=odoo SMOKE_MODEL=res.partner \
  python3 /repo/skills/odoo-introspect/scripts/tests/integration_smoke.py
