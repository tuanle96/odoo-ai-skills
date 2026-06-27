"""
Static Odoo patch validator (Layer I) — LOCAL, no Odoo, no shell.

The `odoo-review` checklist, made executable: scan .py/.xml source for the
Odoo-specific anti-patterns an AI confidently ships that lint and "it ran for me
as admin" miss — deprecated v≤16 syntax, batch-unsafe `create()`, N+1 loops,
SQL injection, unjustified `sudo()`, removed ORM aliases, fragile xpath, leftover
debug. Findings are advisory and biased to LOW false positives (better to miss a
fuzzy case than cry wolf); a static scan is a wide net, never proof. Confirm
correctness (MRO, security, data-loss) against the running instance with the
`odoo-ai` introspection layers — this complements that, it never replaces it.

Pure module-level functions (no Odoo) — unit-tested. Runs as a normal script:

    python3 patch_validator.py <path> [<path>...]      # prints JSON to stdout
    odoo-ai validate <path...>                          # via the CLI

A directory path is walked recursively for .py/.xml. Exit code is always 0 — the
blocking count is in the summary; the caller (e.g. deploy-gate) decides.

Output: JSON {findings, summary, _caveat}. Each finding:
    {"file", "line", "rule", "severity": "blocking"|"warning", "message", "fix"}
"""
import os
import re
import sys
import json

# --- Pure helpers (no Odoo needed — unit-testable) ---------------------------
DEF_RE = re.compile(r"^(\s*)def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
FORLOOP_RE = re.compile(r"^(\s*)(for|while)\b")
SEARCH_CALL_RE = re.compile(r"\.(search|browse|search_count|search_read)\s*\(")


def _f(path, line, rule, severity, message, fix):
    return {"file": path, "line": line, "rule": rule, "severity": severity,
            "message": message, "fix": fix}


def _strip_comment(line):
    """Drop a trailing #-comment (heuristic: a '#' inside a string can slip
    through — acceptable for a wide-net linter)."""
    return line.split("#", 1)[0]


def _indent(line):
    return len(line) - len(line.lstrip())


def check_python(path, source):
    """Flag Odoo anti-patterns in Python source. Returns a list of findings."""
    findings = []
    lines = source.splitlines()
    code_lines = [_strip_comment(ln) for ln in lines]

    # --- per-line checks ---
    for i, raw in enumerate(lines):
        code = code_lines[i]
        ln = i + 1

        # deprecated attrs=/states= as a kwarg (removed in v17). Require a
        # preceding ',' or '(' so a plain `states = [...]` assignment is ignored.
        if re.search(r"[,(]\s*(attrs|states)\s*=", code):
            findings.append(_f(path, ln, "deprecated_attrs_states", "warning",
                "attrs=/states= were removed in Odoo 17+",
                "use direct invisible=/readonly=/required= Python expressions"))

        if re.search(r"\bdef\s+name_get\s*\(", code):
            findings.append(_f(path, ln, "name_get", "warning",
                "name_get() is on the removed path",
                "define _compute_display_name instead"))

        if re.search(r"\btype\s*=\s*['\"]json['\"]", code):
            findings.append(_f(path, ln, "route_type_json", "warning",
                "type='json' is renamed type='jsonrpc' in Odoo 19+",
                "use type='jsonrpc' (confirm the target version)"))

        # SQL injection: cr.execute(...) with string interpolation.
        if ".execute(" in code:
            after = code.split(".execute(", 1)[1]
            if (re.match(r"\s*f['\"]", after)                 # f-string
                    or re.search(r"['\"]\s*%\s*[\(\w]", after)  # 'sql' % (...) operator, not %s
                    or ".format(" in after                     # .format()
                    or re.search(r"['\"]\s*\+", after)):       # 'str' + var
                findings.append(_f(path, ln, "sql_injection", "blocking",
                    "cr.execute with string interpolation — SQL injection risk",
                    "parameterize: cr.execute(query, (a, b)); never f-string a domain/SQL"))

        # blanket sudo() with no explanatory comment (check raw line for '#').
        if ".sudo()" in code and "#" not in raw:
            findings.append(_f(path, ln, "unjustified_sudo", "warning",
                ".sudo() with no explanatory comment — privilege-bypass risk",
                "add a one-line reason, or fix the record rule (odoo-security)"))

        if re.search(r"self\._(cr|uid|context)\b", code):
            findings.append(_f(path, ln, "private_env_alias", "warning",
                "self._cr/_uid/_context are legacy aliases",
                "use self.env.cr / self.env.uid / self.env.context"))

        if (re.search(r"\bbreakpoint\s*\(", code) or re.search(r"\bimport\s+pdb\b", code)
                or "pdb.set_trace(" in code):
            findings.append(_f(path, ln, "leftover_debug", "warning",
                "leftover debugger statement", "remove before merge"))
        if re.search(r"\bprint\s*\(", code) and "test" not in path.lower():
            findings.append(_f(path, ln, "leftover_print", "warning",
                "leftover print()", "use logging, or remove"))

    # --- N+1: a search/browse call nested under an open for/while ---
    loop_stack = []  # indents of currently-open loops
    for i, code in enumerate(code_lines):
        if not code.strip():
            continue
        ind = _indent(code)
        while loop_stack and ind <= loop_stack[-1]:
            loop_stack.pop()
        if SEARCH_CALL_RE.search(code) and loop_stack:
            findings.append(_f(path, i + 1, "query_in_loop", "warning",
                "search()/browse() inside a loop — likely N+1",
                "search once with an `in` domain, then filtered()/mapped()"))
        if FORLOOP_RE.match(code):
            loop_stack.append(ind)

    # --- create() without @api.model_create_multi (batch-unsafe) ---
    for i, code in enumerate(code_lines):
        m = DEF_RE.match(code)
        if not (m and m.group(2) == "create"):
            continue
        decos, j = [], i - 1
        while j >= 0:
            prev = code_lines[j].strip()
            if not prev:
                j -= 1
                continue
            if prev.startswith("@"):
                decos.append(prev)
                j -= 1
                continue
            break
        if not any("model_create_multi" in d for d in decos):
            findings.append(_f(path, i + 1, "create_not_batch", "blocking",
                "create() without @api.model_create_multi is not batch-safe",
                "decorate with @api.model_create_multi and take vals_list (a list)"))

    # --- ensure_one() inside a likely-batch method (create/write/action_*) ---
    cur_method, cur_indent = None, -1
    for i, code in enumerate(code_lines):
        if not code.strip():
            continue
        ind = _indent(code)
        if cur_method is not None and ind <= cur_indent:
            cur_method = None
        m = DEF_RE.match(code)
        if m:
            cur_method, cur_indent = m.group(2), ind
            continue
        if cur_method and "ensure_one()" in code and (
                cur_method in ("create", "write") or cur_method.startswith("action_")):
            findings.append(_f(path, i + 1, "ensure_one_in_batch", "warning",
                f"ensure_one() inside {cur_method}() — likely called on a recordset",
                "operate on the full recordset; don't assume a singleton"))

    return findings


def check_xml(path, source):
    """Flag Odoo anti-patterns in view/data XML. Returns a list of findings."""
    findings = []
    for i, raw in enumerate(source.splitlines()):
        ln = i + 1
        if re.search(r"\b(attrs|states)\s*=", raw):
            findings.append(_f(path, ln, "xml_attrs_states", "warning",
                "attrs=/states= were removed in Odoo 17+ views",
                "use invisible=/readonly=/required= with a Python expression"))
        if re.search(r"<\s*tree\b", raw):
            findings.append(_f(path, ln, "tree_tag", "warning",
                "<tree> is renamed <list> in Odoo 17+",
                "use <list> … </list>"))
        if re.search(r"<\s*xpath\b", raw) and "position" not in raw:
            findings.append(_f(path, ln, "xpath_no_position", "warning",
                "<xpath> without position= is fragile/ambiguous",
                'add position="after|before|inside|replace|attributes"'))
    return findings


def _gather(paths):
    """Expand paths to a sorted list of .py/.xml files (dirs walked recursively)."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    if fn.endswith((".py", ".xml")):
                        out.append(os.path.join(root, fn))
        elif os.path.isfile(p):
            out.append(p)
    return sorted(out)


def validate_paths(paths):
    """Validate every .py/.xml under `paths`; return {findings, summary, _caveat}."""
    files = _gather(paths)
    findings = []
    for fp in files:
        try:
            src = open(fp, encoding="utf-8", errors="replace").read()
        except Exception:  # noqa: BLE001 — unreadable file: skip, don't crash the scan
            continue
        if fp.endswith(".py"):
            findings += check_python(fp, src)
        elif fp.endswith(".xml"):
            findings += check_xml(fp, src)
    by_rule, blocking, warning = {}, 0, 0
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
        if f["severity"] == "blocking":
            blocking += 1
        else:
            warning += 1
    return {
        "findings": findings,
        "summary": {"files": len(files), "blocking": blocking,
                    "warning": warning, "by_rule": by_rule},
        "_caveat": ("Static heuristics, biased to LOW false positives — a wide net, "
                    "not proof. Confirm correctness (MRO, security, data-loss, the "
                    "right hook) against the running instance with odoo-ai."),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(json.dumps({"findings": [], "summary": {"files": 0, "blocking": 0,
              "warning": 0, "by_rule": {}}, "_caveat": "no paths given"}, indent=2))
        return
    print(json.dumps(validate_paths(argv), indent=2))


if __name__ == "__main__":
    main()
