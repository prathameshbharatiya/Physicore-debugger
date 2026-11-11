"""EX: the committed example outputs are exactly what the command produces now."""
from pathlib import Path

from physicore_collector.investigation_view.service import run_render

from .fx import EXAMPLE_BAG, ROOT

D = ROOT / "examples" / "investigation_view"


def test_EX_01_report_is_the_m3d_example():
    assert (D / "report.json").read_bytes() == (ROOT / "examples" / "localization" / "expected_output.json").read_bytes()


def test_EX_02_markdown_is_current():
    assert (D / "report.md").read_bytes().decode() == run_render(D / "report.json", "markdown").text


def test_EX_03_html_is_current():
    assert (D / "report.html").read_bytes().decode() == run_render(D / "report.json", "html").text


def test_EX_04_verified_markdown_is_current(monkeypatch):
    monkeypatch.chdir(ROOT)
    r = run_render("examples/investigation_view/report.json", "markdown", "examples/localization/example_bag")
    assert r.exit_code == 0 and (D / "report_verified.md").read_bytes().decode() == r.text
