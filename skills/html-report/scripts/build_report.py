#!/usr/bin/env python3
"""Inline the shared report.css into an HTML report and open it.

The html-report skill ships ONE canonical stylesheet (assets/report.css). Reports
authored from assets/template.html carry a marker stylesheet link:

    <link rel="stylesheet" href="report.css"><!-- @odoo-ai-html-report:css -->

This helper replaces that link with an inline <style>...report.css...</style>
block so the report becomes fully self-contained — no external CSS, viewable
offline, on mobile, or as a PR attachment — then opens it in the default browser.

Pure standard library. Locates report.css next to this script (../assets), so it
works wherever the plugin is installed. Idempotent: re-running on an already
inlined report does nothing.

Usage:
    build_report.py <report.html> [--no-open]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

MARKER = "@odoo-ai-html-report:css"
START = "<!-- @odoo-ai-html-report:css:inlined -->"
END = "<!-- /@odoo-ai-html-report:css:inlined -->"

# A <link> stylesheet whose href points at report.css (with optional ./ prefix,
# optional /assets/ path, and optional ?v= cache-buster), plus an optional
# trailing marker comment. rel= may appear before or after href.
LINK_RE = re.compile(
    r'[ \t]*<link\b[^>]*\bhref=["\'](?:\./)?(?:report\.css|/assets/report\.css)'
    r'(?:\?[^"\']*)?["\'][^>]*>'
    r'(?:[ \t]*<!--\s*' + re.escape(MARKER) + r'\s*-->)?',
    re.IGNORECASE,
)


def css_path() -> Path:
    """Canonical stylesheet shipped alongside this skill."""
    return Path(__file__).resolve().parent.parent / "assets" / "report.css"


def open_file(path: Path) -> None:
    if sys.platform == "darwin":
        cmd = ["open", str(path)]
    elif sys.platform.startswith("win"):
        cmd = ["cmd", "/c", "start", "", str(path)]
    else:
        cmd = ["xdg-open", str(path)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"WARN: could not open browser: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Inline report.css into an HTML report and open it."
    )
    ap.add_argument("report", help="path to the report .html file")
    ap.add_argument(
        "--no-open", action="store_true", help="do not open the report in a browser"
    )
    args = ap.parse_args()

    report = Path(args.report)
    if not report.is_file():
        print(f"ERROR: report not found: {report}", file=sys.stderr)
        return 1

    css_file = css_path()
    if not css_file.is_file():
        print(f"ERROR: stylesheet not found: {css_file}", file=sys.stderr)
        return 1

    html = report.read_text(encoding="utf-8")

    if START in html:
        print(f"==> already inlined: {report}")
    else:
        css = css_file.read_text(encoding="utf-8")
        style_block = f"{START}\n<style>\n{css}\n</style>\n{END}"
        new_html, n = LINK_RE.subn(lambda _m: style_block, html, count=1)
        if n == 0:
            print(
                "ERROR: no stylesheet link found to inline. Expected a line like:\n"
                f'  <link rel="stylesheet" href="report.css"><!-- {MARKER} -->\n'
                "Start your report from assets/template.html.",
                file=sys.stderr,
            )
            return 1
        report.write_text(new_html, encoding="utf-8")
        print(f"==> inlined {css_file.name} ({len(css)} bytes) -> {report}")

    if not args.no_open:
        open_file(report)
        print(f"==> opened {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
