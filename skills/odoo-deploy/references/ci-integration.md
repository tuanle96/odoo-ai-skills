# CI integration — running odoo-ai gates

The enforcement gates split cleanly by what they need:

| gates | need | run where |
|---|---|---|
| `validate` · `redact` · `scan-secrets` · `deploy-gate` · `evidence` | Python 3 only | CI (GitHub Actions, any runner), laptop, anywhere |
| `scenarios` · `env-fingerprint` · `upgrade-check` · `all` · `security` · `trace` | `odoo-bin shell` + DB | odoo.sh staging, Docker instance, self-hosted dev |

The pattern: **run the instance-bound layers on a staging environment, commit the evidence bundle, let CI run the local gates against it.**

---

## 1 — GitHub Actions (the bundled workflow)

`.github/workflows/odoo-ai-gate.yml` ships ready to use.  Copy it into any Odoo repo that uses this suite.

### What it does

On every PR that touches `.py` or `.xml` files:

1. **`validate`** — runs the `odoo-review` checklist as a static linter (no DB).  Exits non-zero on blocking issues; the PR comment reflects the outcome either way.
2. **`scan-secrets`** — scans each changed file for accidentally committed secrets before they merge.
3. **`deploy-gate` + `evidence`** — if an `evidence_bundle/` directory is present on the branch (committed there by the staging run described in §2), it aggregates the JSON into an `approve / needs-human / block` verdict and renders the bundle as a PR comment.

Result is posted as a **sticky comment** (one comment per PR, updated on each push — no spam).

### What it does NOT do

It does not run `odoo-bin shell` and cannot read the live registry.  The layers that require a running instance (A–D, G, and the instance-side Layer-I gates) must run on staging:

```
# run these on odoo.sh staging or Docker — NOT in the Actions runner:
odoo-ai --db <DB> all sale.order --methods action_confirm
odoo-ai --db <DB> scenarios sale.order --methods action_confirm
odoo-ai --db <DB> env-fingerprint > evidence_bundle/fingerprint.json
odoo-ai --db <DB> upgrade-check sale.order --against old_brief.json
odoo-ai --db <DB> security sale.order --user <affected_user>
odoo-ai --db <DB> trace sale.order <id> action_confirm
```

Commit the `evidence_bundle/` directory to the branch; the workflow picks it up automatically on the next push.

### Minimal manual trigger

To run `validate` against a specific path from any branch:

```
GitHub → Actions → odoo-ai gate → Run workflow → targets: addons/my_module
```

---

## 2 — odoo.sh recipe

odoo.sh gives you a shell and a copy of production data on every staging build — the right place for the instance-bound gates.

### Setup: add a build hook

In your repo root, create `.odoo_hooks/post_install.sh` (odoo.sh executes it after the module update):

```sh
#!/bin/sh
# .odoo_hooks/post_install.sh
# Runs the instance-bound Layer-I gates on the staging build.
# Output is written to evidence_bundle/ and committed back to the branch
# so the GitHub Actions gate can run deploy-gate + evidence against it.

set -e
DB="${ODOO_DATABASE}"        # injected by odoo.sh
PLUGIN_ROOT="${ODOO_HOME}/custom/odoo-ai-skills"   # adjust to your addons_path
CLI="${PLUGIN_ROOT}/skills/odoo-introspect/scripts/odoo-ai"
BUNDLE="${ODOO_HOME}/evidence_bundle"

mkdir -p "$BUNDLE"

# Capture the environment fingerprint (module list, edition, config key names).
"$CLI" --db "$DB" env-fingerprint > "$BUNDLE/fingerprint_staging.json"

# If a production fingerprint was committed to the repo, diff against it.
if [ -f "${PLUGIN_ROOT}/evidence_bundle/fingerprint_prod.json" ]; then
  "$CLI" env-diff \
    "${PLUGIN_ROOT}/evidence_bundle/fingerprint_prod.json" \
    "$BUNDLE/fingerprint_staging.json" \
    > "$BUNDLE/env_diff.json"
fi

# Risk-based scenario generator for the models you changed.
# Replace sale.order with the models your PR touches.
"$CLI" --db "$DB" scenarios sale.order > "$BUNDLE/scenarios_sale_order.json"

# Upgrade safety: rename vs drop, new-required, noupdate risks.
# Requires a committed old_brief.json from the previous release.
if [ -f "${PLUGIN_ROOT}/evidence_bundle/old_brief.json" ]; then
  "$CLI" --db "$DB" upgrade-check sale.order \
    --against "${PLUGIN_ROOT}/evidence_bundle/old_brief.json" \
    > "$BUNDLE/upgrade_check.json"
fi

# Effective security check for the affected user/company.
"$CLI" --db "$DB" security sale.order --user 2 > "$BUNDLE/security.json"

# Commit the bundle back so GitHub Actions can run deploy-gate + evidence.
git -C "${ODOO_HOME}" add evidence_bundle/
git -C "${ODOO_HOME}" commit -m "chore: update evidence bundle [skip ci]" || true
git -C "${ODOO_HOME}" push origin HEAD || true
```

### Promotion gate

Merge the staging branch to production **only** after:

1. The odoo.sh staging build is green (tests pass, migration ran cleanly).
2. The `odoo-ai gate` GitHub Actions workflow shows `deploy-gate: ✅ approve`.
   - `needs-human` → a human must review the evidence and approve explicitly.
   - `block` → the gate is hard-stopped; do not merge until the blocking issue is resolved.

The `deploy-gate` verdict aggregates `env-diff` drift, `upgrade-check` data-loss risk, `scenarios` coverage, and `security` posture.  An `approve` from `deploy-gate` does not replace a human code review; it closes the mechanical verification gap that review alone misses.

---

## 3 — Docker wrapper

When Odoo runs in a container, point `odoo-ai`'s `--odoo-bin` flag at a thin wrapper that forwards stdin into the container.  This is the same pattern documented in `skills/odoo-introspect/references/introspection.md`.

```sh
# /usr/local/bin/odoo-docker   (chmod +x)
#!/bin/sh
# Wrapper: run odoo-bin shell inside the container, forwarding the env vars
# that the introspection scripts read.
EFLAGS=""
for v in MODEL METHODS SOURCE CODE CODE_PREVIEW OUT VIEWS VIEW_ID VIEW_XMLID \
         FIELD RESOLVE_PATHS MODULE AS_USER AS_COMPANY AS_ALLOWED_COMPANIES \
         RECORD_ID METHOD COMMIT BREAK_AT BREAK_LINE FIELDS MAX_HITS \
         ON_EXCEPTION NO_REDACT REDACT_EXTRA MAX_DEPTH MAX_STRING \
         MAX_RECORDS DATA_LIMIT; do
  EFLAGS="$EFLAGS -e $v"
done
exec docker exec -i $EFLAGS <container-name> odoo "$@"
```

```sh
# Now all odoo-ai commands run against the containerized instance:
odoo-ai --db mydb \
        --conf /etc/odoo/odoo.conf \
        --odoo-bin /usr/local/bin/odoo-docker \
        all sale.order --methods action_confirm

odoo-ai --db mydb \
        --odoo-bin /usr/local/bin/odoo-docker \
        scenarios sale.order
```

`--conf` and `--db` are resolved **inside** the container, so use the container's paths.  The `odoo-ai` CLI itself runs on the host; the Python scripts stream through `docker exec -i` on stdin.

In a `docker-compose` setup, replace `docker exec -i <container-name>` with `docker compose exec -T odoo` (the `-T` disables pseudo-TTY, which conflicts with piped stdin).

---

## 4 — MCP wrapper (agent tools)

The [`tuanle96/mcp-odoo`](https://github.com/tuanle96/mcp-odoo) MCP server exposes the introspection layers as agent tools, so the AI agent can call `get_model_fields`, `get_model_mro`, `run_native_check`, and friends without leaving the assistant session.

This is the highest-leverage integration for agentic workflows: instead of the agent guessing Odoo internals, it calls a deterministic tool and gets ground truth back in the same turn.

```python
# In your mcp-odoo server (example tool additions):
@mcp.tool()
def get_model_brief(model: str, methods: list[str] | None = None) -> dict:
    """Layer A: fields, MRO, security, auto-triggers for MODEL."""
    return odoo_ai_cli("brief", model, methods=methods)

@mcp.tool()
def run_deploy_gate(bundle_dir: str) -> dict:
    """Layer I: aggregate evidence bundle → approve / needs-human / block (local)."""
    # deploy-gate is local — no Odoo call needed.
    import subprocess, json
    r = subprocess.run(
        ["python3", CLI_PATH, "deploy-gate", bundle_dir],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout)
```

See `skills/odoo-introspect/references/introspection.md` → "Option B — wire it into mcp-odoo as a tool" for the full MRO tool pattern.

---

## 5 — RPC degraded mode (Odoo Online / no shell)

When you only have RPC access (Odoo Online / SaaS, or a remote box you can't shell into), each introspection layer loses some capability.  The table below is honest about the gaps — do not claim full coverage where it says Partial or None.

| Layer | Name | RPC | Why |
|---|---|---|---|
| **A** | fields · MRO · super · security · auto-triggers | **Partial** | `fields_get` / `ir.model.fields.search_read` give the full field inventory and `ir.model.access` / `ir.rule` give raw ACL rows; MRO / `has_super` / `super_position` / method source require Python `inspect` inside `odoo-bin shell` — no RPC equivalent |
| **B** | views · buttons · inheritance chain | **Partial** | `ir.actions.act_window` and `ir.ui.view` are searchable; `get_view()` is callable via RPC but context normalization differs; `inheritance_chain` is assembled in Python from the live view tree — the RPC result may not match the fully rendered arch |
| **C** | menu · seeded data · report wiring | **Partial** | `ir.ui.menu`, `ir.model.data`, `ir.actions.report` are readable; QWeb template bodies, paperformat, and parser class resolution (the "deep" wiring) are not exposed as ORM fields |
| **D** | runtime trace | **None** | Requires `sys.settrace` inside an `odoo-bin shell` execution; there is no RPC substitute for the real call sequence + SQL |
| **E** | field reverse-impact | **Partial** | Can `search_read` `ir.model.fields` for `related` / `depends` text matches; graph-resolution walks the registry class in Python (`comodel_name` hops) and cannot be done over plain RPC |
| **F** | runtime state / post-mortem | **None** | Breakpoint capture and exception post-mortem require live Python execution inside the shell; no RPC path |
| **G** | effective security simulator | **Partial** | Raw ACL rows readable via `ir.model.access` / `ir.rule`; `ir.rule._compute_domain` (effective domain eval under `with_user(user).with_company(co)`) is server-side Python, not an RPC endpoint — you see the rule text but not the evaluated domain for a given user+company pair |
| **H** | native capabilities + native-check | **Partial** | Module / model surface queryable via `ir.model.data` / `ir.actions.*`; probe kinds `mixin_inherited`, `method_exists`, and any MRO-based check need Python `inspect` on the server |
| **I** | enforcement gates | **Full (local)** | `validate` / `redact` / `scan-secrets` / `deploy-gate` / `evidence` need no instance; `env-fingerprint` / `upgrade-check` / `scenarios` need `odoo-bin shell` + DB |

**Bottom line for Odoo Online:** you can read fields, menus, actions, raw ACL, and raw record rules over the External API.  You cannot read the actual MRO, run a runtime trace, evaluate effective record-rule domains for a user, or capture runtime state.  The four local Layer-I gates (`validate` / `redact` / `scan-secrets` / `deploy-gate`) — plus `evidence` — run on any machine without a DB at all.

If the task requires a module (custom code), the instance must move to odoo.sh or self-hosted first — Odoo Online does not allow custom modules.
