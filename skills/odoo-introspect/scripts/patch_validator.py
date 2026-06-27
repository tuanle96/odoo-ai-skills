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
import ast
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


def _deco_is_model_create_multi(d):
    return ((isinstance(d, ast.Attribute) and d.attr == "model_create_multi")
            or (isinstance(d, ast.Name) and d.id == "model_create_multi"))


def _is_model_class(cls):
    """True if a ClassDef assigns _name/_inherit/_inherits (i.e. an Odoo model)."""
    for n in cls.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in ("_name", "_inherit", "_inherits"):
                    return True
    return False


def _interp_kind(node):
    """If *node* is a string built by interpolation, name the technique, else None."""
    if isinstance(node, ast.JoinedStr):
        return "an f-string"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return "a %-format"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return "string concatenation"
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        return "a .format() call"
    return None


def _ast_findings(path, source):
    """AST checks where regex is too blunt: SQL interpolation in `.execute()`
    (a constant query with %s placeholders or a LIKE '%x%' is NOT flagged) —
    including a query assembled into a variable first — and a batch-unsafe
    `create()` *inside an actual Odoo model class* (so a plain helper named
    create() is not a false positive). Returns [] if unparseable — the line-based
    rules still ran.
    """
    out = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out

    # Parent pointers so each execute() can be scoped to its enclosing function —
    # taint is per-function (a `q` interpolated in one function must not flag a
    # constant `q` executed in another).
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._pv_parent = parent

    def _scope(node):
        while node is not None:
            node = getattr(node, "_pv_parent", None)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
                return node
        return tree

    _taint_cache = {}

    def _tainted_in(scope):
        key = id(scope)
        if key not in _taint_cache:
            names = set()
            for n in ast.walk(scope):
                if isinstance(n, ast.Assign) and _interp_kind(n.value):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
            _taint_cache[key] = names
        return _taint_cache[key]

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute" and node.args):
            arg = node.args[0]
            kind = _interp_kind(arg)
            if kind is None and isinstance(arg, ast.Name) and arg.id in _tainted_in(_scope(node)):
                kind = f"a variable (`{arg.id}`) built by string interpolation"
            if kind:
                out.append(_f(path, getattr(node, "lineno", 0), "sql_injection", "blocking",
                    f"cr.execute(...) built with {kind} — SQL injection risk "
                    "(a constant query with %s placeholders is fine)",
                    "parameterize: cr.execute(query, (a, b))"))

    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        if not _is_model_class(cls):
            continue
        for fn in cls.body:
            if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and fn.name == "create"
                    and not any(_deco_is_model_create_multi(d) for d in fn.decorator_list)):
                out.append(_f(path, fn.lineno, "create_not_batch", "blocking",
                    "create() in a model without @api.model_create_multi is not batch-safe",
                    "decorate with @api.model_create_multi and take vals_list (a list)"))
    return out


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

        # SQL injection (.execute with interpolation) is checked via AST in
        # _ast_findings — a regex would false-positive on a constant LIKE '%x%'.

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

    # create() without @api.model_create_multi is checked via AST in
    # _ast_findings — and only inside a real Odoo model class, so a plain
    # non-Odoo helper named create() is not a false positive.

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

    return findings + _ast_findings(path, source)


_XML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def check_xml(path, source):
    """Flag Odoo anti-patterns in view/data XML. Returns a list of findings.

    XML comments are stripped first (preserving line numbers) so a commented-out
    `attrs=` / `<tree>` isn't flagged, and `<xpath>` is matched as a whole
    (possibly multi-line) opening tag so a `position=` on the next line counts.
    """
    findings = []
    clean = _XML_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), source)
    for i, raw in enumerate(clean.splitlines()):
        ln = i + 1
        if re.search(r"\b(attrs|states)\s*=", raw):
            findings.append(_f(path, ln, "xml_attrs_states", "warning",
                "attrs=/states= were removed in Odoo 17+ views",
                "use invisible=/readonly=/required= with a Python expression"))
        if re.search(r"<\s*tree\b", raw):
            findings.append(_f(path, ln, "tree_tag", "warning",
                "<tree> is renamed <list> in Odoo 17+",
                "use <list> … </list>"))
    # whole opening tag (DOTALL) so a multi-line <xpath ... position="..."> is OK
    for m in re.finditer(r"<\s*xpath\b[^>]*>", clean, re.DOTALL):
        if "position" not in m.group(0):
            ln = clean.count("\n", 0, m.start()) + 1
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
