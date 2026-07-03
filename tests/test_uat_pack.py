"""
Unit tests for uat_pack.py — pure-function tests plus tempfile-backed tests for
main()'s file I/O (synthetic surface + scenario artifacts written to a tempdir).
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import uat_pack as up  # noqa: E402


# --- synthetic artifacts ----------------------------------------------------

def _surface(*entrypoints):
    return {"mode": "surface", "entrypoints": list(entrypoints)}


def _ep(model, **kw):
    base = {"type": "object_button", "model": model, "method": "action_confirm",
            "ref": "action_confirm", "label": "Confirm", "module": "sale",
            "n_relations": 8, "active": True, "rank": 5.0}
    base.update(kw)
    return base


def _scenarios(model, keys, tier="high"):
    return {
        "model": model,
        "methods": ["action_post", "write"],
        "risk": {"tier": tier, "reasons": [f"{model} is risky"]},
        "scenarios": [{"key": k, "why": f"why {k}."} for k in keys],
    }


# --- pure helpers -----------------------------------------------------------

class FixtureRecipeTests(unittest.TestCase):
    def test_exact_matches(self):
        self.assertEqual(up.suggest_fixture_recipe("sale.order"), "sale_order_stockable")
        self.assertEqual(up.suggest_fixture_recipe("account.move"), "invoice_posted")
        self.assertEqual(up.suggest_fixture_recipe("purchase.order"), "purchase_to_receipt")
        self.assertEqual(up.suggest_fixture_recipe("stock.picking"), "delivery_with_lot")
        self.assertEqual(up.suggest_fixture_recipe("mrp.production"), "mo_with_bom")

    def test_prefix_match(self):
        # base-model prefix maps a sub-model onto its base recipe
        self.assertEqual(up.suggest_fixture_recipe("account.move.line"), "invoice_posted")

    def test_unknown_and_empty_fall_back(self):
        self.assertEqual(up.suggest_fixture_recipe("res.partner"), "customer_basic")
        self.assertEqual(up.suggest_fixture_recipe(""), "customer_basic")
        self.assertEqual(up.suggest_fixture_recipe(None), "customer_basic")


class RoleDerivationTests(unittest.TestCase):
    def test_todo_when_no_groups(self):
        self.assertEqual(up.derive_role(_ep("sale.order")), "TODO: assign role")

    def test_derived_from_group_xmlids(self):
        role = up.derive_role(_ep("sale.order", groups=["sales_team.group_sale_salesman"]))
        self.assertEqual(role, "Sale Salesman")

    def test_multiple_groups_joined_dedup(self):
        role = up.derive_role(_ep("sale.order", groups=[
            "base.group_user", "sales_team.group_sale_manager", "base.group_user"]))
        self.assertIn("User", role)
        self.assertIn("Sale Manager", role)
        self.assertEqual(role.count("User"), 1)

    def test_empty_groups_list_is_todo(self):
        self.assertEqual(up.derive_role(_ep("sale.order", groups=[])), "TODO: assign role")


class InstalledModulesTests(unittest.TestCase):
    def test_none_for_non_dict(self):
        self.assertIsNone(up.installed_modules(None))
        self.assertIsNone(up.installed_modules("nope"))

    def test_flat_name_list(self):
        self.assertEqual(up.installed_modules({"installed_modules": ["sale", "stock"]}),
                         {"sale", "stock"})

    def test_nested_modules_installed(self):
        self.assertEqual(up.installed_modules({"modules": {"installed": ["sale"]}}), {"sale"})

    def test_list_of_dicts(self):
        got = up.installed_modules({"installed_modules": [{"name": "sale"}, {"module": "stock"}]})
        self.assertEqual(got, {"sale", "stock"})

    def test_missing_returns_none(self):
        self.assertIsNone(up.installed_modules({"meta": {}}))


class PreconditionTests(unittest.TestCase):
    def test_verify_when_no_dossier(self):
        pc = up.build_preconditions(_ep("sale.order", module="sale"), None)
        self.assertIn("verify module 'sale' is installed", pc[0])
        self.assertIn("company", pc[1])

    def test_confirmed_when_installed(self):
        pc = up.build_preconditions(_ep("sale.order", module="sale"), {"sale"})
        self.assertIn("confirmed in dossier", pc[0])

    def test_flagged_when_not_installed(self):
        pc = up.build_preconditions(_ep("sale.order", module="sale"), {"stock"})
        self.assertIn("NOT in dossier", pc[0])

    def test_no_module(self):
        pc = up.build_preconditions(_ep("sale.order", module=None), None)
        self.assertIn("required module", pc[0])


class StepTests(unittest.TestCase):
    def test_object_button_mentions_button(self):
        steps = up.build_steps(_ep("sale.order", type="object_button", label="Confirm",
                                    method="action_confirm"))
        self.assertTrue(any("Confirm" in s and "button" in s for s in steps))

    def test_cron_mentions_run_manually(self):
        steps = up.build_steps({"type": "cron", "model": "sale.order", "label": "Nightly",
                                "trigger": "every 1 days"})
        self.assertTrue(any("Run Manually" in s for s in steps))

    def test_route_mentions_request(self):
        steps = up.build_steps({"type": "route", "model": None, "ref": "/shop",
                                "label": "/shop", "methods": ["GET", "POST"], "auth": "public"})
        self.assertTrue(any("/shop" in s for s in steps))

    def test_menu_path_prepended(self):
        steps = up.build_steps(_ep("sale.order", type="window_action", menu_path="Sales/Orders"))
        self.assertTrue(steps[0].startswith("Navigate: Sales/Orders"))

    def test_unknown_kind_has_generic_steps(self):
        steps = up.build_steps({"type": "mystery", "model": "sale.order", "label": "X"})
        self.assertEqual(len(steps), 2)


# --- build_pack -------------------------------------------------------------

class BuildPackTests(unittest.TestCase):
    def _pack(self, surface, scenarios, dossier=None):
        return up.build_pack(surface, scenarios, dossier, "T", "2026-01-01T00:00:00+00:00",
                             {"surface": "s", "scenarios": "c", "dossier": None})

    def test_case_count_cross_product(self):
        surface = _surface(_ep("sale.order", ref="action_confirm", rank=5),
                           _ep("sale.order", ref="action_cancel", label="Cancel", rank=4),
                           _ep("sale.order", ref="action_done", label="Done", rank=3))
        scen = _scenarios("sale.order", ["non_admin", "multi_company"])
        pack = self._pack(surface, scen)
        # 2 scenarios × 3 entrypoints = 6 cases (< cap)
        self.assertEqual(len(pack["cases"]), 6)
        self.assertEqual(pack["coverage_note"], [])

    def test_case_shape(self):
        surface = _surface(_ep("sale.order"))
        scen = _scenarios("sale.order", ["non_admin"])
        case = self._pack(surface, scen)["cases"][0]
        for key in ("uat_id", "title", "model", "role", "preconditions", "data_setup",
                    "steps", "expected_result", "evidence", "sign_off", "risk"):
            self.assertIn(key, case)
        self.assertEqual(case["uat_id"], "UAT-001")
        self.assertEqual(case["model"], "sale.order")
        self.assertEqual(case["risk"], "high")
        self.assertEqual(case["data_setup"]["suggested_fixture_recipe"], "sale_order_stockable")
        self.assertEqual(case["sign_off"], {"owner": "TODO", "date": None})
        self.assertIn("why non_admin.", case["expected_result"])

    def test_role_falls_back_to_todo(self):
        pack = self._pack(_surface(_ep("sale.order")), _scenarios("sale.order", ["non_admin"]))
        self.assertEqual(pack["cases"][0]["role"], "TODO: assign role")

    def test_role_from_groups(self):
        surface = _surface(_ep("sale.order", groups=["sales_team.group_sale_salesman"]))
        pack = self._pack(surface, _scenarios("sale.order", ["non_admin"]))
        self.assertEqual(pack["cases"][0]["role"], "Sale Salesman")

    def test_scenario_without_matching_entrypoint_in_coverage_note(self):
        # scenario B targets a model with no entrypoint; A matches → no global fallback
        surface = _surface(_ep("sale.order"))
        scen = _scenarios("sale.order", ["non_admin"])
        scen["scenarios"].append({"key": "locked_period", "why": "locked.",
                                  "model": "account.move"})
        pack = self._pack(surface, scen)
        keys = [g["scenario"] for g in pack["coverage_note"]]
        self.assertIn("locked_period", keys)
        # and it is NOT silently in cases
        self.assertNotIn("locked_period", [c["scenario_key"] for c in pack["cases"]])

    def test_cap_at_ten_and_notes_dropped(self):
        # 12 scenarios, 1 matching entrypoint each → 10 cases, 2 dropped-by-cap notes
        surface = _surface(_ep("sale.order"))
        scen = _scenarios("sale.order", [f"s{i}" for i in range(12)])
        pack = self._pack(surface, scen)
        self.assertEqual(len(pack["cases"]), 10)
        self.assertEqual(len(pack["coverage_note"]), 2)
        self.assertTrue(all("cap" in g["reason"] for g in pack["coverage_note"]))

    def test_fallback_to_top_ranked_when_no_model_match(self):
        # surface entrypoints are on a different model than the scenarios
        surface = _surface(_ep("crm.lead", ref="action_x", rank=9),
                           _ep("crm.lead", ref="action_y", label="Y", rank=1))
        scen = _scenarios("sale.order", ["non_admin"])
        pack = self._pack(surface, scen)
        self.assertTrue(pack["_fallback"])
        self.assertEqual(len(pack["cases"]), 2)
        self.assertEqual(pack["coverage_note"], [])
        # top-ranked first
        self.assertEqual(pack["cases"][0]["entrypoint"]["rank"], 9)

    def test_no_entrypoints_at_all(self):
        pack = self._pack(_surface(), _scenarios("sale.order", ["non_admin", "batch"]))
        self.assertEqual(pack["cases"], [])
        self.assertEqual(len(pack["coverage_note"]), 2)

    def test_installed_modules_flow_through(self):
        surface = _surface(_ep("sale.order", module="sale"))
        pack = self._pack(surface, _scenarios("sale.order", ["non_admin"]),
                          dossier={"installed_modules": ["sale"]})
        self.assertIn("confirmed in dossier", pack["cases"][0]["preconditions"][0])


# --- markdown ---------------------------------------------------------------

class MarkdownTests(unittest.TestCase):
    def _pack(self):
        surface = _surface(_ep("sale.order"))
        return up.build_pack(surface, _scenarios("sale.order", ["non_admin"]), None,
                             "My Pack", "2026-01-01T00:00:00+00:00",
                             {"surface": "s.json", "scenarios": "c.json", "dossier": None})

    def test_markdown_contains_uat_ids_and_signoff(self):
        md = up.render_markdown(self._pack())
        self.assertIn("UAT-001", md)
        self.assertIn("- [ ]", md)             # checkbox sign-off lines
        self.assertIn("Tester:", md)
        self.assertIn("| UAT ID |", md)         # summary table

    def test_markdown_lists_coverage_gaps(self):
        surface = _surface(_ep("sale.order"))
        scen = _scenarios("sale.order", ["non_admin"])
        scen["scenarios"].append({"key": "batch", "why": "b.", "model": "x.y"})
        pack = up.build_pack(surface, scen, None, "T", "t",
                             {"surface": "s", "scenarios": "c", "dossier": None})
        md = up.render_markdown(pack)
        self.assertIn("Coverage gaps", md)
        self.assertIn("batch", md)


# --- main() / file I/O ------------------------------------------------------

class MainTests(unittest.TestCase):
    def _write(self, tmp, name, obj):
        p = Path(tmp) / name
        p.write_text(json.dumps(obj))
        return str(p)

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = up.main(argv)
        return rc, buf.getvalue()

    def test_happy_path_writes_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            surf = self._write(tmp, "surface.json", _surface(_ep("sale.order")))
            scen = self._write(tmp, "scen.json", _scenarios("sale.order", ["non_admin"]))
            rc, out = self._run(["--surface", surf, "--scenarios", scen, "--out-dir", tmp])
            self.assertEqual(rc, 0)
            pack = json.loads(out)
            self.assertEqual(len(pack["cases"]), 1)
            self.assertTrue((Path(tmp) / "uat-pack.md").is_file())
            self.assertEqual(pack["outputs"]["markdown"], str(Path(tmp) / "uat-pack.md"))

    def test_missing_dossier_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            surf = self._write(tmp, "surface.json", _surface(_ep("sale.order")))
            scen = self._write(tmp, "scen.json", _scenarios("sale.order", ["non_admin"]))
            rc, out = self._run(["--surface", surf, "--scenarios", scen, "--out-dir", tmp])
            self.assertEqual(rc, 0)
            pack = json.loads(out)
            self.assertIsNone(pack["source_artifacts"]["dossier"])

    def test_required_args_missing(self):
        rc, out = self._run(["--surface", "only-surface.json"])
        self.assertEqual(rc, 0)
        self.assertIn("error", json.loads(out))

    def test_malformed_surface_is_error_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "surface.json"
            bad.write_text("{not valid json")
            scen = self._write(tmp, "scen.json", _scenarios("sale.order", ["non_admin"]))
            rc, out = self._run(["--surface", str(bad), "--scenarios", scen, "--out-dir", tmp])
            self.assertEqual(rc, 0)
            report = json.loads(out)
            self.assertIn("error", report)

    def test_empty_surface_file_is_error_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "surface.json"
            empty.write_text("")
            scen = self._write(tmp, "scen.json", _scenarios("sale.order", ["non_admin"]))
            rc, out = self._run(["--surface", str(empty), "--scenarios", scen, "--out-dir", tmp])
            self.assertEqual(rc, 0)
            self.assertIn("error", json.loads(out))

    def test_valid_but_empty_scenarios_is_graceful(self):
        with tempfile.TemporaryDirectory() as tmp:
            surf = self._write(tmp, "surface.json", _surface(_ep("sale.order")))
            scen = self._write(tmp, "scen.json", {"model": "sale.order", "scenarios": []})
            rc, out = self._run(["--surface", surf, "--scenarios", scen, "--out-dir", tmp])
            self.assertEqual(rc, 0)
            pack = json.loads(out)
            self.assertEqual(pack["cases"], [])
            self.assertEqual(pack["coverage_note"], [])

    def test_html_mode_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            surf = self._write(tmp, "surface.json", _surface(_ep("sale.order")))
            scen = self._write(tmp, "scen.json", _scenarios("sale.order", ["non_admin"]))
            rc, out = self._run(["--surface", surf, "--scenarios", scen,
                                 "--out-dir", tmp, "--html"])
            self.assertEqual(rc, 0)
            pack = json.loads(out)
            html_path = Path(tmp) / "uat-pack.html"
            # viz needs its stylesheet; if present the file is written, else a warning
            if pack["outputs"]["html"]:
                self.assertTrue(html_path.is_file())
                self.assertIn("UAT-001", html_path.read_text())
            else:
                self.assertTrue(pack.get("_warnings"))


if __name__ == "__main__":
    unittest.main()
