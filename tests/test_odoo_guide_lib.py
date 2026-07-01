"""Unit tests for the pure helpers in the `odoo-user-guide` skill.

`odoo_guide_lib` imports only the stdlib at module load (yaml is lazy), so these
run under plain `python -m unittest` with no third-party packages — matching the
rest of this repo's CI (`tests.yml`).
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "odoo-user-guide" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import odoo_guide_lib as lib  # noqa: E402


SURFACE = {
    "mode": "surface",
    "entrypoints": [
        {"type": "object_button", "model": "sale.order", "method": "action_cancel", "rank": 0.4},
        {"type": "object_button", "model": "sale.order", "method": "action_confirm",
         "label": "Action Confirm", "module": "sale", "rank": 0.93},
        {"type": "cron", "model": "sale.order", "method": "_cron_x", "rank": 0.1},
    ],
}

ALL_ENTRYPOINTS = {
    "views": {
        "form": {"buttons": [
            {"name": "action_confirm", "string": "Confirm"},
            {"name": "action_cancel", "string": "Cancel"},
        ]},
    },
}


def _manifest(**over):
    base = dict(
        intent="Confirm a Sale Order", model="sale.order", method="action_confirm",
        button_label="Confirm", expected_field="state", expected_value="sale",
        menu_path="Sales > Orders > Quotations", role="sales_user", company="main",
    )
    base.update(over)
    return lib.build_manifest(**base)


class TestSlugify(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(lib.slugify("Confirm a Sale Order"), "confirm_a_sale_order")

    def test_strips_vietnamese_diacritics(self):
        self.assertEqual(lib.slugify("Xác-nhận!! đơn  bán"), "xac_nhan_don_ban")
        self.assertEqual(lib.slugify("Đơn Bán Hàng"), "don_ban_hang")
        self.assertEqual(lib.slugify(""), "guide")


ARCH = """<form><header>
  <button name="action_confirm" string="Confirm" type="object"/>
  <button name="action_cancel" string="Cancel" type="object"/>
  <button name="%(sale.action_view_x)d" string="X"/>
  <button string="No name"/>
  <button name="action_confirm" string="Dup"/>
</header></form>"""


class TestArchGrounding(unittest.TestCase):
    def test_buttons_from_arch_dedup_and_filter(self):
        btns = lib.buttons_from_arch(ARCH)
        names = [b["name"] for b in btns]
        self.assertEqual(names, ["action_confirm", "action_cancel"])  # %()d, unnamed, dup dropped
        self.assertEqual(btns[0]["string"], "Confirm")

    def test_surface_from_buttons_matches_pick_entrypoint(self):
        surface = lib.surface_from_buttons("sale.order", lib.buttons_from_arch(ARCH))
        ep = lib.pick_entrypoint(surface, method="action_confirm")
        self.assertEqual(ep["type"], "object_button")
        self.assertEqual(ep["model"], "sale.order")
        self.assertEqual(ep["method"], "action_confirm")


class TestPickEntrypoint(unittest.TestCase):
    def test_picks_requested_method(self):
        e = lib.pick_entrypoint(SURFACE, method="action_confirm")
        self.assertEqual(e["method"], "action_confirm")

    def test_falls_back_to_top_button_not_cron(self):
        e = lib.pick_entrypoint(SURFACE, method="does_not_exist")
        self.assertEqual(e["type"], "object_button")
        self.assertEqual(e["method"], "action_cancel")  # first object_button in list

    def test_none_when_no_buttons(self):
        self.assertIsNone(lib.pick_entrypoint({"entrypoints": []}))


class TestButtonLabel(unittest.TestCase):
    def test_found(self):
        self.assertEqual(lib.button_label_from_views(ALL_ENTRYPOINTS, "action_confirm"), "Confirm")

    def test_missing(self):
        self.assertIsNone(lib.button_label_from_views(ALL_ENTRYPOINTS, "action_nope"))


class TestBuildManifest(unittest.TestCase):
    def test_shape_and_expected(self):
        m = _manifest()["guide"]
        self.assertEqual(m["id"], "confirm_a_sale_order")
        self.assertEqual(m["model"], "sale.order")
        self.assertTrue(m["preconditions"]["demo_db_only"])
        click = [s for s in m["steps"] if s["action"] == "click_button"][0]
        self.assertEqual(click["button_name"], "action_confirm")
        self.assertTrue(click["mutates"])
        self.assertEqual(click["expected"], {"field": "state", "equals": "sale"})

    def test_menu_step_optional(self):
        steps = _manifest(menu_path=None)["guide"]["steps"]
        self.assertFalse(any(s["action"] == "open_menu" for s in steps))


class TestResolveSelectors(unittest.TestCase):
    def test_button_prefers_method_name(self):
        cands = lib.resolve_selectors({"action": "click_button", "button_name": "action_confirm",
                                       "visible_text": "Confirm"})
        self.assertEqual(cands[0], 'button[name="action_confirm"]')
        self.assertIn('button:has-text("Confirm")', cands)  # text only as a later fallback
        self.assertLess(cands.index('button[name="action_confirm"]'),
                        cands.index('button:has-text("Confirm")'))

    def test_menu_uses_leaf_label(self):
        cands = lib.resolve_selectors({"action": "open_menu", "label": "Sales > Orders > Quotations"})
        self.assertTrue(all("Quotations" in c for c in cands))

    def test_unknown_action(self):
        self.assertEqual(lib.resolve_selectors({"action": "wat"}), [])


class TestInsetBox(unittest.TestCase):
    def test_scaled_and_padded(self):
        box = lib.inset_box({"x": 100, "y": 50, "width": 80, "height": 30}, scale=2, pad=10,
                            img_w=2880, img_h=1800)
        self.assertEqual(box, (180, 80, 380, 180))  # (100-10)*2,(50-10)*2,(100+80+10)*2,(50+30+10)*2

    def test_clamped_to_image(self):
        box = lib.inset_box({"x": -5, "y": -5, "width": 20, "height": 20}, scale=1, pad=10,
                            img_w=100, img_h=100)
        self.assertEqual(box[0], 0)
        self.assertEqual(box[1], 0)
        self.assertGreater(box[2], box[0])
        self.assertGreater(box[3], box[1])


class TestMergeCopy(unittest.TestCase):
    EV = {"guide": {"title": "T"}, "proof": {"pass": True},
          "steps": [{"n": 1, "action": "click_button", "label": "Xác nhận"},
                    {"n": 2, "action": "open_record", "label": "Đơn"}]}

    def test_joins_agent_copy_by_step(self):
        copy = {"title": "Hướng dẫn xác nhận đơn", "intro": "Giới thiệu",
                "steps": [{"n": 1, "heading": "Bấm Xác nhận", "body": "Mô tả dễ hiểu"}]}
        out = lib.merge_copy(self.EV, copy)
        self.assertEqual(out["title"], "Hướng dẫn xác nhận đơn")
        self.assertEqual(out["steps"][0]["heading"], "Bấm Xác nhận")
        self.assertEqual(out["steps"][0]["body"], "Mô tả dễ hiểu")

    def test_fallback_heading_when_agent_skips_step(self):
        out = lib.merge_copy(self.EV, {"steps": []})
        self.assertEqual(out["steps"][1]["heading"], "Đơn")  # falls back to captured label
        self.assertEqual(out["title"], "T")


class TestRegisterShot(unittest.TestCase):
    def test_creates_step_and_sets_kind(self):
        ev = {"steps": []}
        lib.register_shot(ev, 1, "full", "screenshots/01_full.png", action="open_record", label="Đơn")
        self.assertEqual(ev["steps"][0],
                         {"n": 1, "full": "screenshots/01_full.png", "action": "open_record", "label": "Đơn"})

    def test_updates_existing_step_and_keeps_order(self):
        ev = {"steps": [{"n": 2}, {"n": 1}]}
        lib.register_shot(ev, 1, "zoom", "z.png")
        lib.register_shot(ev, 1, "after", "a.png", state_before="draft", state_after="sale")
        self.assertEqual([s["n"] for s in ev["steps"]], [1, 2])  # re-sorted by n
        s1 = next(s for s in ev["steps"] if s["n"] == 1)
        self.assertEqual(s1["zoom"], "z.png")
        self.assertEqual(s1["after"], "a.png")
        self.assertEqual((s1["state_before"], s1["state_after"]), ("draft", "sale"))

    def test_rejects_bad_kind(self):
        with self.assertRaises(ValueError):
            lib.register_shot({"steps": []}, 1, "thumbnail", "x.png")


class TestGuideToMarkdown(unittest.TestCase):
    MERGED = {
        "title": "Cách xác nhận đơn", "intro": "Giới thiệu ngắn.",
        "guide": {"model": "sale.order", "role": "tuan.le"},
        "proof": {"field": "state", "actual": "sale", "pass": True},
        "steps": [
            {"n": 1, "heading": "Mở đơn", "body": "Mở báo giá.", "full": "screenshots/01_full.png"},
            {"n": 2, "heading": "Xác nhận", "body": "Nhấn nút.", "full": "screenshots/02_full.png",
             "zoom": "screenshots/02_zoom.png", "after": "screenshots/02_after.png",
             "state_before": "draft", "state_after": "sale"},
        ],
    }

    def test_structure(self):
        md = lib.guide_to_markdown(self.MERGED)
        self.assertTrue(md.startswith("# Cách xác nhận đơn\n"))
        self.assertIn("## 1. Mở đơn", md)
        self.assertIn("## 2. Xác nhận", md)
        self.assertIn("![Toàn màn hình](screenshots/01_full.png)", md)
        self.assertIn("![Cận cảnh vị trí thao tác](screenshots/02_zoom.png)", md)
        self.assertIn("![Kết quả sau khi thao tác](screenshots/02_after.png)", md)
        self.assertIn("**Trạng thái:** `draft` → `sale`", md)
        self.assertIn("Đã kiểm chứng", md)
        self.assertIn("`sale.order`", md)

    def test_no_proof_no_badge(self):
        m = dict(self.MERGED, proof=None)
        self.assertNotIn("Đã kiểm chứng", lib.guide_to_markdown(m))


class TestDoctorVerdict(unittest.TestCase):
    def test_block_when_mutating_and_not_sandbox(self):
        v = lib.doctor_verdict(_manifest(), security=None, sandbox=False)
        self.assertEqual(v["verdict"], "block")
        self.assertTrue(v["reasons"])

    def test_approve_when_sandbox_and_write_access(self):
        sec = {"access_rights": {"read": True, "write": True}}
        v = lib.doctor_verdict(_manifest(), security=sec, sandbox=True)
        self.assertEqual(v["verdict"], "approve")
        self.assertEqual(v["reasons"], [])

    def test_block_when_no_write_access_even_in_sandbox(self):
        sec = {"access_rights": {"read": True, "write": False}}
        v = lib.doctor_verdict(_manifest(), security=sec, sandbox=True)
        self.assertEqual(v["verdict"], "block")

    def test_block_when_no_read_access(self):
        sec = {"access_rights": {"read": False, "write": True}}
        v = lib.doctor_verdict(_manifest(), security=sec, sandbox=True)
        self.assertEqual(v["verdict"], "block")


if __name__ == "__main__":
    unittest.main()
