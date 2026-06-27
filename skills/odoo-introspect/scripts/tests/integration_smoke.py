"""
Integration smoke test — runs the introspection layers against a REAL Odoo and
asserts structural invariants on the JSON they emit. Unlike test_pure_functions
(no Odoo needed), this needs a live instance, so it is OPT-IN: it runs only when
`ODOO_DB` is set, and is skipped otherwise (so the pure-function CI stays green).

It validates the env-bound paths the unit tests can't reach: selection-literal
extraction, the manifest by-location split, the view inheritance chain, seeded
`noupdate` records, the graph-resolved field reverse-impact (Layer E), the
effective-security simulation (Layer G), and Layer F redaction.

How it shells out
-----------------
Same idea as the `odoo-ai` CLI: build `odoo shell` and pipe each script to it.

    # Local Odoo on PATH:
    ODOO_DB=ci_smoke python integration_smoke.py

    # Against a Docker container (point ODOO_BIN at a wrapper that runs
    # `docker exec -i <container> odoo "$@"` and forwards the env vars below):
    ODOO_DB=bestmix_14_6 ODOO_CONF=/etc/odoo/odoo.conf \
        ODOO_BIN=/tmp/odoo-docker python integration_smoke.py

Config (env):
    ODOO_DB      (required)  database name
    ODOO_BIN     (opt)       odoo binary / wrapper            (default: odoo)
    ODOO_CONF    (opt)       path to odoo.conf
    SMOKE_MODEL  (opt)       model to introspect              (default: res.partner)
    SMOKE_RECORD_ID (opt)    record id for Layer F (state); auto-resolved from
                             the DB when unset, so redaction is always exercised
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent          # .../odoo-introspect/scripts
DB = os.environ.get("ODOO_DB")
ODOO_BIN = os.environ.get("ODOO_BIN", "odoo")
CONF = os.environ.get("ODOO_CONF")
MODEL = os.environ.get("SMOKE_MODEL", "res.partner")
RECORD_ID = os.environ.get("SMOKE_RECORD_ID")
TIMEOUT = int(os.environ.get("SMOKE_TIMEOUT", "600"))

SENTINELS = {
    "model_brief.py":   ("===ODOO_BRIEF_START===", "===ODOO_BRIEF_END==="),
    "entrypoints.py":   ("===ODOO_EP_START===", "===ODOO_EP_END==="),
    "metadata.py":      ("===ODOO_META_START===", "===ODOO_META_END==="),
    "field_refs.py":    ("===ODOO_REFS_START===", "===ODOO_REFS_END==="),
    "security_sim.py":  ("===ODOO_SECURITY_START===", "===ODOO_SECURITY_END==="),
    "state_capture.py": ("===ODOO_STATE_START===", "===ODOO_STATE_END==="),
}


def _shell(script_name, env_extra):
    cmd = [ODOO_BIN, "shell", "-d", DB, "--no-http", "--log-level=error"]
    if CONF:
        cmd += ["-c", CONF]
    env = {**os.environ, **{k: str(v) for k, v in env_extra.items()}}
    with open(SCRIPTS / script_name) as fh:
        proc = subprocess.run(cmd, stdin=fh, env=env, capture_output=True,
                              text=True, timeout=TIMEOUT)
    start, end = SENTINELS[script_name]
    out = proc.stdout
    if start not in out or end not in out:
        tail = "\n".join((proc.stderr or out).strip().splitlines()[-12:])
        raise AssertionError(f"{script_name}: no JSON between sentinels. Tail:\n{tail}")
    body = out.split(start, 1)[1].rsplit(end, 1)[0].strip()
    return json.loads(body)


def _shell_code(code):
    """Run a short inline snippet in `odoo shell` (stdin) and return stdout."""
    cmd = [ODOO_BIN, "shell", "-d", DB, "--no-http", "--log-level=error"]
    if CONF:
        cmd += ["-c", CONF]
    proc = subprocess.run(cmd, input=code, env=os.environ,
                          capture_output=True, text=True, timeout=TIMEOUT)
    return proc.stdout


def resolve_record_id():
    """Pick any existing record of MODEL so Layer F (state) can run unattended."""
    out = _shell_code(
        f'r = env["{MODEL}"].search([], limit=1)\n'
        f'print("===RID===", r.id if r else 0)\n')
    m = re.search(r"===RID===\s+(\d+)", out)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def resolve_nonsuper_uid():
    """Pick a non-superuser (id != 1) so Layer G isn't just the bypassed root."""
    out = _shell_code(
        'u = env["res.users"].search([("id","!=",1)], order="id", limit=1)\n'
        'print("===UID===", u.id if u else 0)\n')
    m = re.search(r"===UID===\s+(\d+)", out)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'} {name}{(' — ' + detail) if detail and not cond else ''}")


def smoke_brief():
    print("Layer A — model_brief")
    d = _shell("model_brief.py", {"MODEL": MODEL, "METHODS": "write,create"})
    check("brief.field_count>0", d.get("field_count", 0) > 0, str(d.get("field_count")))
    check("brief.identity.model matches", d.get("identity", {}).get("model") == MODEL)
    # selection extraction: at least one selection field exposes a value list
    sel_fields = [n for n, f in d["fields"].items()
                  if f.get("type") == "selection" and isinstance(f.get("selection"), list)]
    check("brief.selection literals present", sel_fields, "no selection field with a value list")
    if sel_fields:
        first = d["fields"][sel_fields[0]]["selection"][0]
        check("brief.selection has value key", "value" in first, str(first))
    # manifest by-location split (path-based)
    md = d.get("manifest_depends", {})
    check("brief.manifest by_location present",
          set(md.get("by_location", {})) >= {"core", "enterprise", "local", "unknown"})
    check("brief.module_paths present", isinstance(md.get("module_paths"), dict))


def smoke_entrypoints():
    print("Layer B — entrypoints")
    d = _shell("entrypoints.py", {"MODEL": MODEL, "VIEWS": "form"})
    form = d.get("views", {}).get("form", {})
    check("entrypoints.form has no error", "_error" not in form, str(form.get("_error")))
    check("entrypoints.inheritance_chain is a list",
          isinstance(form.get("inheritance_chain"), list))
    chain = form.get("inheritance_chain") or []
    if chain and isinstance(chain[0], dict):
        check("entrypoints.chain entries have xmlid+priority",
              "priority" in chain[0] and "xmlid" in chain[0], str(chain[0]))


def smoke_metadata():
    print("Layer C — metadata")
    d = _shell("metadata.py", {"MODEL": MODEL})
    check("metadata.menu_graph present", "menu_graph" in d)
    check("metadata.seeded_data present", "seeded_data" in d)
    check("metadata.noupdate_records is a list",
          isinstance(d.get("seeded_data", {}).get("noupdate_records"), list))


def smoke_refs():
    # Layer E — reverse impact, exercised in graph-resolved mode. Uses a field
    # guaranteed to exist on res.partner so the env-bound path of the resolver
    # runs against a real registry (relation hops + comodel_name), independent
    # of whatever dependents happen to exist on a clean base DB.
    field = "country_id" if MODEL == "res.partner" else "display_name"
    print(f"Layer E — field_refs (graph-resolved) on {MODEL}.{field}")
    d = _shell("field_refs.py", {"MODEL": MODEL, "FIELD": field, "RESOLVE_PATHS": "1"})
    check("refs.path_resolution graph-resolved",
          d.get("path_resolution") == "graph-resolved", str(d.get("path_resolution")))
    check("refs.field_exists true", d.get("field_exists") is True)
    check("refs.reference_count is int", isinstance(d.get("reference_count"), int))
    check("refs.severity_counts has buckets",
          set(d.get("severity_counts", {})) >= {"high", "medium", "low"})
    # Every graph-resolved field reference must carry a resolved_via that lands
    # on exactly this model.field (no last-segment false positives).
    bad = [r for r in d.get("references", [])
           if r["kind"] in ("stored_compute_depends", "related_field")
           and r.get("resolved_via", {}).get("terminal_field") not in (field, None)]
    check("refs.resolved_via lands on target field", not bad, str(bad[:2]))


def smoke_security():
    uid = resolve_nonsuper_uid()
    if not uid:
        print("Layer G — security (skipped: no non-superuser found)")
        return
    print(f"Layer G — security (effective ACL + rules) on {MODEL} as uid {uid}")
    d = _shell("security_sim.py", {"MODEL": MODEL, "AS_USER": uid})
    ar = d.get("access_rights", {})
    check("security.access_rights has modes",
          all(m in ar for m in ("read", "write", "create", "unlink")), str(list(ar)))
    check("security.odoo_check present", isinstance(ar.get("odoo_check"), dict))
    check("security.record_rules has read.effective_domain",
          "effective_domain" in d.get("record_rules", {}).get("read", {}))
    check("security.user resolved (not superuser)",
          d.get("user", {}).get("id") == uid and d.get("user", {}).get("is_superuser") is False)
    check("security.field_access.restricted is a list",
          isinstance(d.get("field_access", {}).get("restricted"), list))


def smoke_state():
    global RECORD_ID
    if not RECORD_ID:
        RECORD_ID = resolve_record_id()
    if not RECORD_ID:
        print("Layer F — state (skipped: no record of "
              f"{MODEL} found and SMOKE_RECORD_ID unset)")
        return
    print(f"Layer F — state (redaction) on {MODEL}({RECORD_ID})")
    d = _shell("state_capture.py", {
        "MODEL": MODEL, "RECORD_ID": RECORD_ID, "METHOD": "_compute_display_name",
        "BREAK_AT": f"{MODEL}._compute_display_name",
        "FIELDS": "display_name", "REDACT_EXTRA": "display_name",
    })
    check("state.redaction enabled by default", d.get("redaction", {}).get("enabled") is True)
    check("state rolled back", d.get("committed") is False)
    # display_name was forced into the redact set → must be masked, not leaked
    bps = d.get("breakpoints") or []
    masked = any(bp.get("self_fields", {}).get("display_name") == "<redacted>" for bp in bps)
    check("state.redaction masks forced field", masked or not bps,
          "display_name not masked")


def main():
    if not DB:
        print("SKIP integration_smoke: ODOO_DB not set (pure-function CI is unaffected).")
        return 0
    print(f"integration_smoke · db={DB} · model={MODEL} · odoo_bin={ODOO_BIN}\n")
    for fn in (smoke_brief, smoke_entrypoints, smoke_metadata, smoke_refs, smoke_security, smoke_state):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(fn.__name__, False, f"{type(e).__name__}: {e}")
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
