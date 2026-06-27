"""
Integration smoke test — runs the introspection layers against a REAL Odoo and
asserts structural invariants on the JSON they emit. Unlike test_pure_functions
(no Odoo needed), this needs a live instance, so it is OPT-IN: it runs only when
`ODOO_DB` is set, and is skipped otherwise (so the pure-function CI stays green).

It validates the env-bound paths the unit tests can't reach: selection-literal
extraction, the manifest by-location split, the view inheritance chain, seeded
`noupdate` records, the graph-resolved field reverse-impact (Layer E), the
effective-security simulation (Layer G) — including the multi-company
`AS_COMPANY` record-rule scoping (the v0.4.1 fix) — and Layer F redaction.

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
    "capabilities.py":  ("===ODOO_CAP_START===", "===ODOO_CAP_END==="),
    "native_check.py":  ("===ODOO_NCHECK_START===", "===ODOO_NCHECK_END==="),
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


# Multi-company Layer G regression. Sets up two companies, a user allowed in
# both, and a company-scoped record rule, then runs the REAL security_sim.py
# in-process against each company and reports the effective read-domain. Locks
# the v0.4.1 fix: _compute_domain must resolve `company_ids` against the
# SIMULATED company (with_company + allowed_company_ids), not the user's default
# company. Everything is rolled back in `finally`, so it never persists — safe
# to run against a dev DB too.
_MC_SNIPPET = '''
import os, io, json, contextlib
res = {"error": None}
try:
    Comp = env["res.company"]
    cA = Comp.create({"name": "Smoke MC A"})
    cB = Comp.create({"name": "Smoke MC B"})
    grp = env.ref("base.group_user").id
    Users = env["res.users"]
    groups_field = "group_ids" if "group_ids" in Users._fields else "groups_id"
    user = Users.create({
        "name": "Smoke MC User", "login": "smoke_mc_user_%d" % cA.id,
        "company_id": cA.id, "company_ids": [(6, 0, [cA.id, cB.id])],
        groups_field: [(6, 0, [grp])],
    })
    model_id = env["ir.model"].search([("model", "=", "res.partner")], limit=1).id
    env["ir.rule"].create({
        "name": "Smoke MC partner rule",
        "model_id": model_id,
        "domain_force": "[('company_id','in',company_ids)]",
        "global": True,
    })

    def _eff_domain(company_id, allowed=None):
        # Fresh registry cache so the first company's eval can't leak into the
        # second via any cached rule/domain state.
        try:
            env.registry.clear_cache()
        except Exception:
            try:
                env.registry.clear_caches()
            except Exception:
                pass
        os.environ["MODEL"] = "res.partner"
        os.environ["AS_USER"] = str(user.id)
        os.environ["AS_COMPANY"] = str(company_id)
        if allowed:
            os.environ["AS_ALLOWED_COMPANIES"] = ",".join(str(i) for i in allowed)
        else:
            os.environ.pop("AS_ALLOWED_COMPANIES", None)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(open(SECPATH).read(), {"env": env})
        raw = buf.getvalue()
        body = raw.split("===ODOO_SECURITY_START===", 1)[1].rsplit("===ODOO_SECURITY_END===", 1)[0]
        data = json.loads(body)
        return data["record_rules"]["read"]["effective_domain"]

    res["dom_A"] = _eff_domain(cA.id)
    res["dom_B"] = _eff_domain(cB.id)
    res["dom_AB"] = _eff_domain(cA.id, allowed=[cA.id, cB.id])
    res["cA"], res["cB"] = cA.id, cB.id
except Exception as e:
    res["error"] = "%s: %s" % (type(e).__name__, e)
finally:
    env.cr.rollback()
print("===MC===", json.dumps(res))
'''


def _flatten_ints(obj):
    """Collect every int found anywhere in a (possibly nested) domain list."""
    found = []
    if isinstance(obj, bool):
        return found
    if isinstance(obj, int):
        return [obj]
    if isinstance(obj, (list, tuple)):
        for x in obj:
            found.extend(_flatten_ints(x))
    return found


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


def smoke_security_multicompany():
    print("Layer G — security multi-company (AS_COMPANY effective_domain)")
    secpath = (SCRIPTS / "security_sim.py")
    code = ("SECPATH = %r\n" % str(secpath)) + _MC_SNIPPET
    out = _shell_code(code)
    m = re.search(r"===MC===\s+(\{.*\})", out)
    if not m:
        tail = "\n".join(out.strip().splitlines()[-12:])
        check("security_mc.snippet ran", False, f"no ===MC=== marker. Tail:\n{tail}")
        return
    res = json.loads(m.group(1))
    if res.get("error"):
        check("security_mc.setup ok", False, res["error"])
        return
    dom_a, dom_b = res.get("dom_A"), res.get("dom_B")
    dom_ab = res.get("dom_AB")
    ca, cb = res.get("cA"), res.get("cB")
    ints_a, ints_b = _flatten_ints(dom_a), _flatten_ints(dom_b)
    ints_ab = _flatten_ints(dom_ab)
    # The company-scoped rule resolves `company_ids` to the SIMULATED company,
    # so each effective domain must reference its own company and not the other.
    check("security_mc.AS_COMPANY=A scopes to company A",
          ca in ints_a and cb not in ints_a, f"dom_A={dom_a} (cA={ca}, cB={cb})")
    check("security_mc.AS_COMPANY=B scopes to company B",
          cb in ints_b and ca not in ints_b, f"dom_B={dom_b} (cA={ca}, cB={cb})")
    # The whole point of the v0.4.1 fix: the two companies yield DIFFERENT
    # effective domains (pre-fix both collapsed to the user's default company).
    check("security_mc.domains differ per company", dom_a != dom_b,
          f"dom_A == dom_B == {dom_a}")
    # --allowed-companies widens env.companies, so company_ids resolves to the
    # whole toggled-on set: the effective domain covers BOTH companies.
    check("security_mc.--allowed-companies covers the full set",
          ca in ints_ab and cb in ints_ab, f"dom_AB={dom_ab} (cA={ca}, cB={cb})")


def smoke_capabilities():
    print(f"Layer H — capabilities (model mode) on {MODEL}")
    d = _shell("capabilities.py", {"MODEL": MODEL})
    check("capabilities.model mode", d.get("mode") == "model", str(d.get("mode")))
    check("capabilities.mixins has 3 keys",
          set(d.get("mixins", {})) == {"mail_thread", "activities", "portal"},
          str(d.get("mixins")))
    check("capabilities.functional_fields is a list",
          isinstance(d.get("functional_fields"), list))
    check("capabilities._summary present", isinstance(d.get("_summary"), dict))
    check("capabilities.bound_actions is a list", isinstance(d.get("bound_actions"), list))

    print("Layer H — capabilities (module mode) on base")
    m = _shell("capabilities.py", {"MODULE": "base"})
    check("capabilities.module found+installed",
          m.get("found") is True and m.get("state") == "installed",
          f"found={m.get('found')}, state={m.get('state')}")
    check("capabilities.module models is a list", isinstance(m.get("models"), list))
    check("capabilities.module _summary present", isinstance(m.get("_summary"), dict))
    # xmlid evidence: any enumerated window action carries a module.name xmlid
    wins = [w for w in m.get("window_actions", []) if "_truncated" not in w]
    if wins:
        check("capabilities.window_actions carry xmlid evidence",
              all(w.get("xmlid", "").startswith("base.") for w in wins if w.get("xmlid")),
              str(wins[0]))


def smoke_native_check():
    cards = SCRIPTS.parent.parent / "odoo-capabilities" / "references" / "cards"
    print(f"Layer H — native-check (gate-then-rank; cards: {cards.name}/)")
    d = _shell("native_check.py", {
        "REQUIREMENT": "auto-number our delivery slips", "CARDS_DIR": str(cards)})
    check("native_check.cards_loaded > 0", d.get("cards_loaded", 0) > 0, str(d.get("cards_loaded")))
    check("native_check.confirmed is a list", isinstance(d.get("confirmed_candidates"), list))
    check("native_check.unconfirmed is a list", isinstance(d.get("unconfirmed_candidates"), list))
    conf = d.get("confirmed_candidates", [])
    ids = [c.get("id") for c in conf]
    # ir.sequence exists in `base` on ANY install → always confirmed for "auto-number"
    check("native_check confirms universal.ir_sequence for auto-number",
          "universal.ir_sequence" in ids, str(ids))
    # every confirmed candidate carries cited instance evidence (found probes)
    bad_conf = [c["id"] for c in conf
                if not (isinstance(c.get("evidence"), list) and c["evidence"]
                        and all(e.get("found") for e in c["evidence"]))]
    check("native_check confirmed carry found-evidence", not bad_conf, str(bad_conf[:3]))
    # every unconfirmed candidate explains why it's absent
    bad_unconf = [c["id"] for c in d.get("unconfirmed_candidates", [])
                  if not c.get("why_absent")]
    check("native_check unconfirmed carry why_absent", not bad_unconf, str(bad_unconf[:3]))


def main():
    if not DB:
        print("SKIP integration_smoke: ODOO_DB not set (pure-function CI is unaffected).")
        return 0
    print(f"integration_smoke · db={DB} · model={MODEL} · odoo_bin={ODOO_BIN}\n")
    for fn in (smoke_brief, smoke_entrypoints, smoke_metadata, smoke_refs,
               smoke_security, smoke_security_multicompany, smoke_state,
               smoke_capabilities, smoke_native_check):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(fn.__name__, False, f"{type(e).__name__}: {e}")
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
