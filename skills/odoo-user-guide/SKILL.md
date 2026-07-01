---
name: odoo-user-guide
description: >-
  Use when the user wants END-USER usage documentation / how-to guides for an
  Odoo flow — "viết hướng dẫn sử dụng", "tạo tài liệu hướng dẫn cho nhân viên",
  "làm guide thao tác", "document how to <do X> in Odoo", "screenshot/step-by-step
  guide", "user manual cho luồng <model>". The AGENT drives the real Odoo UI live
  through an MCP browser tool (claude-in-chrome / Playwright MCP) — observing each
  page semantically (accessibility tree), clicking by visible label, screenshotting
  and narrating — grounded by Odoo metadata + RPC so it stays correct, and proven
  by reading the resulting state at the backend. Then it renders a Markdown guide
  (guide.md + screenshots/, or self-contained HTML). Deterministic, ground-truth-first, re-runnable —
  NOT a blind hand-written selector script. Default: dev/UAT (sandbox) db. NOT for
  QWeb PDF business documents — that is odoo-reports.
---

# odoo-user-guide — instance-verified end-user guides

Generic recorders (Scribe/Tango/Guidde) document **what one person did once**.
This skill documents **what this Odoo instance lets this role do now** — and proves
it by executing the flow on the running instance.

The fast, accurate way to build one is **the agent drives the live UI once** through
an MCP browser tool. The bottleneck in doc generation is *guessing the click-path in
code before seeing the UI*; don't. The agent observes each page (accessibility tree),
clicks by **visible label**, adapts to wizards / locked records / banners like a
human, and screenshots as it goes. Odoo metadata + RPC keep it correct; reading the
backend state makes it a proof.

> Scripts capture FACTS (grounding, test data, proof, cleanup, rendering). The agent
> supplies the JUDGEMENT (driving the UI, and writing clear plain-language prose).

## The playbook (primary, agent-driven)

```bash
SK=skills/odoo-user-guide/scripts
export ODOO_GUIDE_PASSWORD='***'
U="--url https://odoo1.example.dev --db DEMO --login tuan.le"
```

1. **PREP — ground + create an owned test record** (deterministic):
   `$SK/odoo-guide-prep "xác nhận đơn bán hàng" $U --sandbox`
   Reads the real form-view buttons + effective access over RPC, creates an owned
   test record (mail off), and writes `guides/<id>/flow.json` (the form URL, the
   button to click + its label, the expected end state) + an `evidence.json`
   skeleton + a `screenshots/` dir.

2. **DRIVE — the agent, live, via the MCP browser tool** (claude-in-chrome /
   Playwright MCP). For each step in `flow.json`:
   - Open the record's form URL; wait for the form to render (`.o_form_view`) — do
     **not** use `networkidle` (Odoo holds a long-poll open).
   - Take a screenshot with the MCP tool's save-to-disk option; it returns a file
     PATH. Feed that path straight into the evidence pack — no hand-edited JSON:
     `$SK/odoo-guide-shot guides/<id>/ --n 1 --kind full --src <mcp_path> --action open_record`
   - Locate the target by **visible label / role** (e.g. `find("Xác nhận button")`)
     — never a hand-written CSS selector. Screenshot it (`--kind full`, and a zoomed
     close-up via the MCP zoom → `--kind zoom`), then click it.
   - Wait for the result, screenshot it (`--kind after`). Pass the **on-screen label
     in the user's language** (`--label`) and the visible state change
     (`--state-before` / `--state-after`). `odoo-guide-shot` copies each image into
     `screenshots/NN_kind.png` and records the step in `evidence.json` (ordered).
   - If a wizard/dialog appears (e.g. a cancel confirmation), handle it as a human
     would — it's visible in the page; adapt instead of failing.

3. **VERIFY — prove it + tear down** (deterministic):
   `$SK/odoo-guide-verify guides/<id>/ $U`
   Reads the expected field over RPC (the proof), stamps it into `evidence.json`,
   and cancels/archives the owned test record so nothing lingers.

4. **WRITE THE PROSE — the agent.** Looking at the saved screenshots + `evidence.json`,
   write `guides/<id>/copy.json`: `title`, `intro`, and per-step `heading` + `body` in
   the user's language — what to do, **why**, and the result to expect. Plain words,
   no jargon, describe what is circled. Never invent UI absent from the evidence.

5. **RENDER:** `$SK/odoo-guide-render guides/<id>/` → **`guide.md`** (Markdown, the
   default documentation format: headings, image links to `screenshots/`, the
   before→after state, a verified-proof note). Add `--format html` for a
   self-contained `guide.html` instead (full + zoom + result image per step).

## Deterministic replay (secondary / CI)

`$SK/odoo-guide-run guides/<id>/guide.yaml $U --sandbox` is a headless Playwright
driver that replays a fixed manifest without an agent. Keep it for CI / re-runs of
a *known* flow — but it is **not** the authoring path. Authoring new guides goes
through the agent playbook above (no selector debugging).

## Grounding is portable

All connection is the standard external API (XML-RPC): `get_view` for real buttons,
`check_access_rights` for effective access, model reads for the proof. Works on any
deployment (local, Docker, remote, Odoo.sh/Online) with no shell access.

## Safety

Docs are made on **dev/UAT**, so mutation is fine there — but it must be explicit:
`--sandbox` (or `ODOO_GUIDE_SANDBOX=1`). `odoo-guide-prep` refuses without it and
refuses if the role lacks write access. The test record is created by the run,
mail-disabled, and cleaned up by `odoo-guide-verify` (sale.order: unlock → cancel
wizard). The agent must never drive existing business records.

## Scope (v1)

- Ships the `sale.order` "confirm" recipe (statusbar-button state transition).
- Not yet: a generic test-data recipe for other models, multi-step wizards/forms,
  voice/TTS, MP4. Those are the v2/v3 roadmap.

## Install (this skill only)

```bash
pip install -r skills/odoo-user-guide/scripts/requirements.txt   # PyYAML, Pillow (+ playwright only for the replay path)
```

The agent-driven path needs an MCP browser tool (claude-in-chrome or Playwright MCP),
not a local Playwright install. Odoo-side calls use stdlib `xmlrpc.client`.
