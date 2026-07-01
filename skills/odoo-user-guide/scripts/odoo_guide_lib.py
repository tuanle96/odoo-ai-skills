#!/usr/bin/env python3
"""Pure helpers for the `odoo-user-guide` skill.

Everything that does NOT need a browser, a live Odoo, or YAML serialization
lives here as a side-effect-free function on plain dicts, so the unit tests run
under `python -m unittest` with only the standard library — matching the rest
of this repo's CI. `yaml` is imported lazily inside the (de)serialize helpers so
importing this module never requires a third-party package.

The three CLI scripts (`odoo-guide-init`, `odoo-guide-doctor`, `odoo-guide-run`)
are thin wrappers: they gather ground truth from the `odoo-ai` CLI / browser,
call into these functions, and write files.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Step actions that change instance state. A guide containing any of these may
# only run against a DB explicitly marked as a sandbox (see doctor_verdict).
MUTATING_ACTIONS = {"click_button"}

# Default flow this v1 vertical slice documents: drive a record to a new state
# by clicking a statusbar button (e.g. sale.order action_confirm -> state=sale).
DEFAULT_METHOD = "action_confirm"


def slugify(text: str) -> str:
    """`"Xác nhận đơn"` -> `"xac_nhan_don"` (ASCII folder id; strips Vietnamese
    diacritics so the slug stays readable instead of collapsing to `x_c_nh_n`)."""
    t = (text or "").replace("đ", "d").replace("Đ", "D")
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")
    return s or "guide"


def buttons_from_arch(arch: str) -> list[dict]:
    """Parse an Odoo form-view arch (XML string) into ordered, de-duplicated
    object-button entries: [{"name": "action_confirm", "string": "Confirm"}].

    This is the portable grounding primitive — the caller gets the arch over the
    standard external API (`get_view`/`fields_view_get`), which works on ANY
    instance (local, Docker, remote, Odoo.sh/Online) with no shell access. Keeps
    only buttons whose `name` is a method (drops `%(action_xmlid)d`, numeric ids,
    and unnamed buttons); pure + unit-tested.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for tag in re.findall(r"<button\b[^>]*>", arch or ""):
        nm = re.search(r'name="([^"]+)"', tag)
        if not nm:
            continue
        name = nm.group(1)
        if name in seen or name.startswith("%") or not re.match(r"[a-zA-Z_]", name):
            continue
        st = re.search(r'string="([^"]+)"', tag)
        seen.add(name)
        out.append({"name": name, "string": st.group(1) if st else None})
    return out


def surface_from_buttons(model: str, buttons: list[dict]) -> dict:
    """Shape parsed buttons into the same payload `odoo-ai surface` emits, so the
    RPC grounding path and the odoo-bin path feed `pick_entrypoint` identically."""
    return {
        "mode": "surface",
        "entrypoints": [
            {"type": "object_button", "model": model, "method": b["name"],
             "label": b.get("string") or b["name"], "rank": 0.9}
            for b in buttons if b["name"].startswith("action_")
        ],
    }


def pick_entrypoint(surface: dict, method: str = DEFAULT_METHOD) -> dict | None:
    """Choose the ranked object-button entrypoint for `method` from an
    `odoo-ai surface` payload. Returns the entrypoint dict or None.

    Falls back to the highest-ranked object_button when `method` isn't found,
    so the caller can surface a clear "I picked X, confirm?" message instead of
    silently guessing the wrong button.
    """
    buttons = [e for e in surface.get("entrypoints", []) if e.get("type") == "object_button"]
    for e in buttons:
        if e.get("method") == method:
            return e
    return buttons[0] if buttons else None


def button_label_from_views(entrypoints_json: dict, method: str) -> str | None:
    """Find the human-visible label of a button (by method name) in the
    form/list views captured by `odoo-ai all` (the `entrypoints` layer)."""
    views = entrypoints_json.get("views", {}) or {}
    for view in views.values():
        for btn in (view or {}).get("buttons", []) or []:
            if btn.get("name") == method:
                return btn.get("string") or btn.get("label")
    return None


def build_manifest(
    *,
    intent: str,
    model: str,
    method: str,
    button_label: str | None,
    expected_field: str,
    expected_value: str,
    menu_path: str | None,
    role: str | None,
    company: str | None,
) -> dict:
    """Assemble the durable guide manifest (a plain dict; the CLI serializes it
    to `guide.yaml`). This is the v1 "click a statusbar button to transition a
    record's state" shape — grounded in metadata the caller read from the
    instance, never hard-coded UI paths."""
    steps: list[dict] = []
    if menu_path:
        steps.append({
            "action": "open_menu",
            "label": menu_path,
            "selector_strategy": "odoo_menu_text",
            "screenshot": True,
            "narration": f"Mở menu {menu_path}.",
        })
    steps.append({
        "action": "open_record",
        "record_ref": "guide_test_record",
        "selector_strategy": "odoo_record_row",
        "screenshot": True,
        "narration": "Mở bản ghi cần thao tác.",
    })
    steps.append({
        "action": "click_button",
        "model": model,
        "button_name": method,
        "visible_text": button_label,
        "mutates": True,
        "selector_strategy": "odoo_button_method",
        "screenshot": True,
        "narration": f"Nhấn nút {button_label or method}.",
        "expected": {"field": expected_field, "equals": expected_value},
    })
    return {
        "guide": {
            "id": slugify(intent),
            "title": intent,
            "model": model,
            "role": role,
            "company": company,
            "preconditions": {
                "demo_db_only": True,
                "create_test_record": True,
            },
            "steps": steps,
        }
    }


def inset_box(bbox: dict, scale: float, pad: int, img_w: int, img_h: int) -> tuple[int, int, int, int]:
    """Pixel crop box (left, top, right, bottom) for a zoomed close-up of the
    action target. `bbox` is Playwright's CSS-pixel rect; `scale` is the
    screenshot's device pixel ratio; `pad` is breathing room in CSS px. Clamped
    to the image so the crop is always valid. Pure → unit-tested."""
    left = int((bbox["x"] - pad) * scale)
    top = int((bbox["y"] - pad) * scale)
    right = int((bbox["x"] + bbox["width"] + pad) * scale)
    bottom = int((bbox["y"] + bbox["height"] + pad) * scale)
    left = max(0, min(left, img_w))
    top = max(0, min(top, img_h))
    right = max(left + 1, min(right, img_w))
    bottom = max(top + 1, min(bottom, img_h))
    return left, top, right, bottom


SHOT_KINDS = ("full", "zoom", "after")


def register_shot(evidence: dict, n: int, kind: str, rel_path: str, *, action=None,
                  label=None, state_before=None, state_after=None) -> dict:
    """Record one captured screenshot into the evidence pack (in place) and return
    it. `kind` is full|zoom|after — the key the renderer reads. Lets the agent feed
    screenshots from an MCP browser tool one at a time without hand-editing JSON,
    so the agent-driven path reaches the renderer without the deterministic driver.
    """
    if kind not in SHOT_KINDS:
        raise ValueError(f"kind must be one of {SHOT_KINDS}, got {kind!r}")
    steps = evidence.setdefault("steps", [])
    step = next((s for s in steps if s.get("n") == n), None)
    if step is None:
        step = {"n": n}
        steps.append(step)
    step[kind] = rel_path
    for key, val in (("action", action), ("label", label),
                     ("state_before", state_before), ("state_after", state_after)):
        if val is not None:
            step[key] = val
    steps.sort(key=lambda s: s.get("n", 0))  # keep render order stable regardless of ingest order
    return evidence


def merge_copy(evidence: dict, copy: dict) -> dict:
    """Join agent-authored plain-language copy onto the captured evidence by step
    number, for the renderer. Steps the agent didn't write keep the captured
    label as a fallback heading so the guide never has an empty step."""
    by_n = {s.get("n"): s for s in copy.get("steps", [])}
    steps = []
    for ev in evidence.get("steps", []):
        c = by_n.get(ev.get("n"), {})
        steps.append({**ev,
                      "heading": c.get("heading") or ev.get("label") or ev.get("action"),
                      "body": c.get("body", "")})
    return {
        "title": copy.get("title") or evidence.get("guide", {}).get("title", "Hướng dẫn"),
        "intro": copy.get("intro", ""),
        "guide": evidence.get("guide", {}),
        "proof": evidence.get("proof"),
        "steps": steps,
    }


def resolve_selectors(step: dict) -> list[str]:
    """Return Playwright selector candidates for a step, most-stable first.

    Bind to Odoo *metadata* (button method name, menu/record text) — NOT brittle
    CSS — so the same manifest keeps resolving after a theme/Studio change. The
    runner tries candidates in order and uses the first that resolves.
    """
    action = step.get("action")
    if action == "click_button":
        name = step.get("button_name", "")
        cands = [
            f'button[name="{name}"]',
            f'.o_statusbar_buttons button[name="{name}"]',
            f'.o_cp_buttons button[name="{name}"]',
        ]
        if step.get("visible_text"):
            cands.append(f'button:has-text("{step["visible_text"]}")')
        return cands
    if action == "open_menu":
        label = step.get("label", "")
        leaf = label.split(">")[-1].split("/")[-1].strip()
        return [f'.o_menu_sections a:has-text("{leaf}")', f'a.dropdown-item:has-text("{leaf}")']
    if action == "open_record":
        return [".o_data_row:first-child .o_data_cell", ".o_kanban_record:first-child"]
    return []


def doctor_verdict(manifest: dict, security: dict | None, sandbox: bool) -> dict:
    """Safety gate. Returns {"verdict": "approve"|"block", "reasons": [...]}.

    Hard-fails (block) when a guide would mutate state outside a sandbox, or when
    the effective role lacks the access the flow needs. This is the skill's fit
    with the suite's "enforcement gate" philosophy: prove it's safe before the
    browser touches anything.
    """
    reasons: list[str] = []
    steps = manifest.get("guide", {}).get("steps", [])
    mutating = [s for s in steps if s.get("action") in MUTATING_ACTIONS or s.get("mutates")]

    if mutating and not sandbox:
        reasons.append(
            f"{len(mutating)} bước thay đổi state nhưng DB chưa được đánh dấu sandbox "
            "(đặt ODOO_GUIDE_SANDBOX=1 hoặc --sandbox)."
        )

    if security is not None:
        rights = security.get("access_rights", {}) or {}
        if mutating and not rights.get("write", False):
            reasons.append("Role hiệu lực KHÔNG có quyền write trên model — không thể thực hiện luồng.")
        if not rights.get("read", True):
            reasons.append("Role hiệu lực KHÔNG có quyền read trên model.")

    return {"verdict": "block" if reasons else "approve", "reasons": reasons}


_SHOT_CAPTIONS = (("full", "Toàn màn hình"), ("zoom", "Cận cảnh vị trí thao tác"),
                  ("after", "Kết quả sau khi thao tác"))


def guide_to_markdown(merged: dict) -> str:
    """Render a merged guide (evidence + agent copy) to Markdown. Images are
    relative links to `screenshots/…` (GitHub/KB-friendly), so the document is
    `guide.md` + the `screenshots/` folder. Pure → unit-tested."""
    out: list[str] = [f"# {merged.get('title', 'Hướng dẫn')}", ""]
    if merged.get("intro"):
        out += [merged["intro"], ""]
    g = merged.get("guide", {})
    meta = " · ".join(x for x in (f"Model: `{g['model']}`" if g.get("model") else "",
                                  f"Vai trò: `{g['role']}`" if g.get("role") else "") if x)
    if meta:
        out += [f"_{meta}_", ""]
    proof = merged.get("proof")
    if proof and proof.get("pass"):
        out += [f"> ✅ **Đã kiểm chứng** trên hệ thống: `{proof['field']}` = `{proof['actual']}`.", ""]
    for s in merged.get("steps", []):
        out += [f"## {s.get('n')}. {s.get('heading', '')}".rstrip(), ""]
        if s.get("body"):
            out += [s["body"], ""]
        for kind, cap in _SHOT_CAPTIONS:
            if s.get(kind):
                out += [f"![{cap}]({s[kind]})", ""]
        b, a = s.get("state_before"), s.get("state_after")
        if b is not None and a is not None and b != a:
            out += [f"**Trạng thái:** `{b}` → `{a}`", ""]
    return "\n".join(out).rstrip() + "\n"


# --- serialization (lazy yaml import so module load stays stdlib-only) --------

def dump_manifest_yaml(manifest: dict) -> str:
    import yaml  # lazy: only needed by the CLI, never by the unit tests
    return yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)


def load_manifest_yaml(text: str) -> dict:
    import yaml
    return yaml.safe_load(text)
