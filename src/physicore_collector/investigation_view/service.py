"""Orchestration of one `evidence render` run (FR-CLI-02): load, build the view, optionally verify the references, render. Never raises for a bad
report; nothing is written anywhere."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .load import read_report
from .safety import clean_text

FORMATS = ("markdown", "html")


@dataclass
class RenderResult:
    text: str = ""
    exit_code: int = 0
    problems: list[str] = field(default_factory=list)


def run_render(report_path: str | Path, fmt: str, verify_path: str | Path | None = None) -> RenderResult:
    if fmt not in FORMATS:
        return RenderResult(exit_code=2, problems=[f"unknown format {clean_text(fmt)!r}; use one of {', '.join(FORMATS)}"])
    loaded = read_report(report_path)
    if loaded.report is None:
        return RenderResult(exit_code=2 if loaded.unreadable else 3, problems=[clean_text(p, 300) for p in loaded.problems])
    from .model import build_view, with_verification
    view = build_view(loaded.report, sha256=loaded.sha256, size=loaded.size)
    code = 0
    if verify_path is not None:
        from .verify import verify_view
        ver = verify_view(view, verify_path)
        view = with_verification(view, ver)
        code = 1 if ver["summary"]["mismatch"] else 0
    if fmt == "markdown":
        from .markdown import render_markdown
        text = render_markdown(view)
    else:
        from .html_static import render_html
        text = render_html(view)
    return RenderResult(text=text, exit_code=code)
