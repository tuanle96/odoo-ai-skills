"""
Unit tests for mcp_server.py — the bounded read-only MCP context server.

Drives dispatch directly with an injected FAKE runner (no Odoo, no real
subprocess): protocol handshake, tools/list schema, tools/call happy path +
provenance, input validation, notifications, unknown method, parse errors,
the warm-cache path (real snapshot_cache + a temp dir), and redaction.
Import-safe outside an Odoo shell.
"""
import io
import json
import sys
import unittest
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import mcp_server as m  # noqa: E402  (import-safe: no serving on import)


def make_server(runner=None, cache=None):
    """A server whose runner records its calls; returns (server, calls)."""
    calls = []

    def default_runner(script, env, preamble=""):
        calls.append({"script": script, "env": dict(env), "preamble": preamble})
        return {"fact_kind": env.get("FACT_KIND"), "model": env.get("MODEL"),
                "fields": {"state": {"type": "selection"}}}

    server = m.ContextServer(m.build_handlers(runner or default_runner), cache=cache)
    return server, calls


def call_tool(server, name, arguments, req_id=1):
    return server.dispatch({"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                            "params": {"name": name, "arguments": arguments}})


# --------------------------------------------------------------------------- #
# Protocol handshake + catalog
# --------------------------------------------------------------------------- #
class HandshakeTests(unittest.TestCase):
    def test_initialize_shape(self):
        server, _ = make_server()
        resp = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"],
                         {"name": "odoo-ai-context", "version": "0.14.0"})
        self.assertEqual(result["capabilities"], {"tools": {}})

    def test_ping_returns_empty(self):
        server, _ = make_server()
        resp = server.dispatch({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_tools_list_has_six_with_schemas(self):
        server, _ = make_server()
        resp = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = resp["result"]["tools"]
        self.assertEqual(len(tools), 6)
        names = {t["name"] for t in tools}
        self.assertEqual(names, {
            "odoo_facts_model", "odoo_facts_security", "odoo_facts_views",
            "odoo_facts_flows", "odoo_dossier_summary", "odoo_native_check"})
        for tool in tools:
            self.assertIn("description", tool)
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIn("properties", schema)
            self.assertFalse(schema["additionalProperties"])

    def test_model_tools_require_model_in_schema(self):
        server, _ = make_server()
        tools = {t["name"]: t for t in
                 server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                 ["result"]["tools"]}
        self.assertEqual(tools["odoo_facts_model"]["inputSchema"]["required"], ["model"])
        self.assertEqual(tools["odoo_native_check"]["inputSchema"]["required"],
                         ["requirement"])


# --------------------------------------------------------------------------- #
# tools/call happy path + provenance + redaction
# --------------------------------------------------------------------------- #
class ToolCallTests(unittest.TestCase):
    def test_facts_model_happy_path_cold_provenance(self):
        server, calls = make_server()
        resp = call_tool(server, "odoo_facts_model", {"model": "sale.order"}, req_id=3)
        result = resp["result"]
        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertIn("fields", payload)
        self.assertEqual(payload["_cache"]["provenance"], "cold")
        self.assertTrue(payload["_cache"]["merge_eligible"])
        # runner was invoked with the correct FACT_KIND/MODEL
        self.assertEqual(calls[0]["env"]["FACT_KIND"], "model")
        self.assertEqual(calls[0]["env"]["MODEL"], "sale.order")

    def test_views_arch_flag_passed_to_runner(self):
        server, calls = make_server()
        call_tool(server, "odoo_facts_views", {"model": "sale.order", "arch": True})
        self.assertEqual(calls[-1]["env"].get("ARCH"), "1")
        # arch omitted → no ARCH env
        call_tool(server, "odoo_facts_views", {"model": "sale.order"})
        self.assertNotIn("ARCH", calls[-1]["env"])

    def test_native_check_ships_card_corpus_via_preamble(self):
        server, calls = make_server()
        resp = call_tool(server, "odoo_native_check",
                         {"requirement": "auto-number delivery slips"})
        self.assertFalse(resp["result"]["isError"])
        last = calls[-1]
        self.assertEqual(last["script"], "native_check.py")
        self.assertEqual(last["env"]["REQUIREMENT"], "auto-number delivery slips")
        self.assertIn("CARDS_JSON", last["preamble"])  # corpus injected on stdin

    def test_pii_is_redacted_in_output(self):
        def runner(script, env, preamble=""):
            return {"note": "email me at alice@example.com please"}
        server, _ = make_server(runner=runner)
        resp = call_tool(server, "odoo_facts_model", {"model": "res.partner"})
        text = resp["result"]["content"][0]["text"]
        self.assertNotIn("alice@example.com", text)
        self.assertIn("<email>", text)


# --------------------------------------------------------------------------- #
# dossier summary reduction
# --------------------------------------------------------------------------- #
class DossierSummaryTests(unittest.TestCase):
    def test_summary_subset_and_footprint_counts(self):
        full = {
            "meta": {"db": "prod", "version": "18.0"},
            "custom_summary": {"custom_models": 3},
            "studio_footprint": {"fields": [1, 2, 3], "views": [1, 1]},
            "upgrade_risk_flags": ["studio_fields"],
            "huge_detail": {"a": list(range(1000))},  # must be dropped
        }
        server, _ = make_server(runner=lambda s, e, p="": full)
        resp = call_tool(server, "odoo_dossier_summary", {})
        payload = json.loads(resp["result"]["content"][0]["text"])
        self.assertEqual(payload["meta"], {"db": "prod", "version": "18.0"})
        self.assertEqual(payload["custom_summary"], {"custom_models": 3})
        self.assertEqual(payload["studio_footprint"], {"fields": 3, "views": 2})
        self.assertEqual(payload["upgrade_risk_flags"], ["studio_fields"])
        self.assertNotIn("huge_detail", payload)

    def test_summarize_dossier_defensive_on_bad_input(self):
        self.assertEqual(
            m.summarize_dossier(None),
            {"meta": {}, "custom_summary": {}, "studio_footprint": {},
             "upgrade_risk_flags": []})
        self.assertEqual(m._footprint_counts([1, 2, 3]), {"total": 3})


# --------------------------------------------------------------------------- #
# Input validation / errors
# --------------------------------------------------------------------------- #
class ValidationTests(unittest.TestCase):
    def test_invalid_model_is_tool_error(self):
        server, calls = make_server()
        resp = call_tool(server, "odoo_facts_model", {"model": "Sale Order!"})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("model", resp["result"]["content"][0]["text"])
        self.assertEqual(calls, [])  # runner never invoked on bad input

    def test_missing_model_is_tool_error(self):
        server, _ = make_server()
        resp = call_tool(server, "odoo_facts_security", {})
        self.assertTrue(resp["result"]["isError"])

    def test_requirement_too_long_is_tool_error(self):
        server, _ = make_server()
        resp = call_tool(server, "odoo_native_check", {"requirement": "x" * 501})
        self.assertTrue(resp["result"]["isError"])

    def test_native_check_bad_optional_model(self):
        server, _ = make_server()
        resp = call_tool(server, "odoo_native_check",
                         {"requirement": "ok", "model": "Bad Model"})
        self.assertTrue(resp["result"]["isError"])

    def test_unknown_tool_is_tool_error(self):
        server, _ = make_server()
        resp = call_tool(server, "odoo_nope", {})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("unknown tool", resp["result"]["content"][0]["text"])

    def test_runner_failure_becomes_tool_error(self):
        def boom(script, env, preamble=""):
            raise RuntimeError("odoo-bin exploded")
        server, _ = make_server(runner=boom)
        resp = call_tool(server, "odoo_facts_model", {"model": "sale.order"})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("odoo-bin exploded", resp["result"]["content"][0]["text"])


# --------------------------------------------------------------------------- #
# JSON-RPC framing: notifications, unknown method, parse errors
# --------------------------------------------------------------------------- #
class FramingTests(unittest.TestCase):
    def test_notification_gets_no_response(self):
        server, _ = make_server()
        self.assertIsNone(
            server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        # any id-less message is a notification — never answered
        self.assertIsNone(server.dispatch({"jsonrpc": "2.0", "method": "tools/list"}))

    def test_unknown_method_is_minus_32601(self):
        server, _ = make_server()
        resp = server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "does/not/exist"})
        self.assertEqual(resp["error"]["code"], -32601)
        self.assertEqual(resp["id"], 5)

    def test_parse_error_is_minus_32700_id_null(self):
        server, _ = make_server()
        line = server.handle_message("{ this is not json ")
        parsed = json.loads(line)
        self.assertIsNone(parsed["id"])
        self.assertEqual(parsed["error"]["code"], -32700)

    def test_blank_line_yields_no_response(self):
        server, _ = make_server()
        self.assertIsNone(server.handle_message("   \n"))

    def test_invalid_request_type_is_minus_32600(self):
        server, _ = make_server()
        self.assertEqual(server.dispatch([1, 2, 3])["error"]["code"], -32600)

    def test_serve_loop_writes_one_line_per_request(self):
        server, _ = make_server()
        lines = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}),
        ]) + "\n"
        out = io.StringIO()
        m.serve(server, stdin=io.StringIO(lines), stdout=out)
        responses = [json.loads(x) for x in out.getvalue().splitlines() if x]
        self.assertEqual([r["id"] for r in responses], [1, 2])  # notification skipped


# --------------------------------------------------------------------------- #
# Caching: cold miss stores, warm hit re-served as context-only
# --------------------------------------------------------------------------- #
@unittest.skipUnless(m.snapshot_cache is not None, "snapshot_cache not importable")
class WarmCacheTests(unittest.TestCase):
    def test_cold_then_warm(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = m.CacheConfig(db="testdb", fingerprint="fp0",
                                  cache_dir=tmp, ttl=900)
            server, calls = make_server(cache=cache)

            first = call_tool(server, "odoo_facts_model", {"model": "sale.order"})
            p1 = json.loads(first["result"]["content"][0]["text"])
            self.assertEqual(p1["_cache"]["provenance"], "cold")
            self.assertTrue(p1["_cache"]["merge_eligible"])
            self.assertEqual(len(calls), 1)

            # Identical call → warm hit: runner NOT re-invoked, context-only stamp.
            second = call_tool(server, "odoo_facts_model", {"model": "sale.order"})
            p2 = json.loads(second["result"]["content"][0]["text"])
            self.assertEqual(p2["_cache"]["provenance"], "warm")
            self.assertFalse(p2["_cache"]["merge_eligible"])
            self.assertEqual(len(calls), 1)  # served from cache, no new subprocess

    def test_distinct_args_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = m.CacheConfig(db="testdb", fingerprint="fp0", cache_dir=tmp)
            server, calls = make_server(cache=cache)
            call_tool(server, "odoo_facts_model", {"model": "sale.order"})
            call_tool(server, "odoo_facts_model", {"model": "stock.picking"})
            self.assertEqual(len(calls), 2)  # different model → separate cold runs

    def test_no_cache_config_always_cold(self):
        server, calls = make_server(cache=None)
        call_tool(server, "odoo_facts_model", {"model": "sale.order"})
        call_tool(server, "odoo_facts_model", {"model": "sale.order"})
        self.assertEqual(len(calls), 2)  # no cache → every call runs cold


class ColdFallbackTests(unittest.TestCase):
    def test_cold_fallback_when_cache_module_absent(self):
        # Simulate snapshot_cache ImportError: server must still stamp provenance.
        saved = m.snapshot_cache
        m.snapshot_cache = None
        try:
            server, _ = make_server(cache=None)
            resp = call_tool(server, "odoo_facts_model", {"model": "sale.order"})
            payload = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual(payload["_cache"]["provenance"], "cold")
        finally:
            m.snapshot_cache = saved


# --------------------------------------------------------------------------- #
# selftest / pure helpers
# --------------------------------------------------------------------------- #
class CliTests(unittest.TestCase):
    def test_selftest_returns_ok_and_tool_names(self):
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = m.main(["--selftest"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["ok"])
        self.assertEqual(out["tools"], m.TOOL_NAMES)

    def test_serve_requires_db(self):
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = m.main([])  # no --db, no ODOO_DB in a clean call
        # rc is 2 unless ODOO_DB is set in the environment; tolerate both.
        if not __import__("os").environ.get("ODOO_DB"):
            self.assertEqual(rc, 2)

    def test_extract_first_start_last_end_keeps_embedded_sentinel(self):
        # first start → LAST end, so a payload that echoes the end sentinel
        # (here inside a string) is not truncated early.
        blob = 'log\n===S===\n{"a": "x===E===y"}\n===E===\ntrailing'
        self.assertEqual(m.extract(blob, "===S===", "===E==="), '{"a": "x===E===y"}')
        self.assertIsNone(m.extract("no markers", "===S===", "===E==="))


if __name__ == "__main__":
    unittest.main()
