"""A small Playwright (Chromium) harness: open a generated page from file:// with every request recorded, console errors captured, optional JS-disabled mode."""
from __future__ import annotations

import contextlib
from pathlib import Path

from physicore_collector.investigation_view.html_static import render_html

from .fx import view_of


@contextlib.contextmanager
def open_page(tmp_path: Path, d: dict | None = None, *, js: bool = True, width: int = 1200, height: int = 900, name: str = "page.html", html: str | None = None, view=None):
    from playwright.sync_api import sync_playwright
    p = tmp_path / name
    p.write_text(html if html is not None else render_html(view if view is not None else view_of(d)), encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(java_script_enabled=js, viewport={"width": width, "height": height}, accept_downloads=False)
        ctx.grant_permissions([], origin=None) if False else None
        page = ctx.new_page()
        log = {"requests": [], "console": [], "errors": [], "dialogs": []}
        page.on("request", lambda r: log["requests"].append(r.url))
        page.on("console", lambda m: log["console"].append((m.type, m.text)))
        page.on("pageerror", lambda e: log["errors"].append(str(e)))
        page.on("dialog", lambda dlg: (log["dialogs"].append(dlg.message), dlg.dismiss()))
        page.goto(p.as_uri())
        try:
            yield page, log
        finally:
            browser.close()
