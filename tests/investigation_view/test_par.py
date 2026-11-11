"""PAR: independent parity: the generated outputs agree with the report, and the verifier detects deliberate corruption."""
import pytest

from physicore_collector.investigation_view.service import run_render

from .fx import EXAMPLE_JSON, gen, write_report
from .tools.parity import check_html, check_markdown

CASES = {"example": None, "small": (8, 1, {}), "lanes": (30, 3, {"m2_every": 4, "blockers": 2}), "wide": (40, 2, {"width": 400}), "empty": (0, 1, {}), "capped": (260, 2, {"blockers": 3})}


def report(tmp_path, key):
    if CASES[key] is None:
        return EXAMPLE_JSON
    n, l, kw = CASES[key]
    return write_report(tmp_path, gen(n, l, **kw))


@pytest.mark.parametrize("key", CASES)
def test_PAR_01_markdown_agrees_with_the_report(tmp_path, key):
    p = report(tmp_path, key)
    assert check_markdown(p.read_bytes(), run_render(p, "markdown").text) == []


@pytest.mark.parametrize("key", CASES)
def test_PAR_02_html_agrees_with_the_report(tmp_path, key):
    p = report(tmp_path, key)
    assert check_html(p.read_bytes(), run_render(p, "html").text) == []


def _md(tmp_path):
    p = write_report(tmp_path, gen(12, 2, blockers=1, m2_every=3))
    return p.read_bytes(), run_render(p, "markdown").text, run_render(p, "html").text


@pytest.mark.parametrize("old,new", [("s-00000003", "s-00000099"), ("/t3", "/t999"), ("| m3c_finding |", "| m2_finding |"), ("1700000000003000 ns", "1700000000003001 ns"),
                                     ("consistent_with_propagation", "inconsistent_order"), ("recording_incomplete", "recording_complete"), ("Counts: 12 symptom", "Counts: 13 symptom"),
                                     ("Exit code 0", "Exit code 1")])
def test_PAR_03_the_verifier_detects_markdown_corruption(tmp_path, old, new):
    raw, md, _ = _md(tmp_path)
    if old not in md:
        old = old.replace("1700000000003000 ns", "1700000000000003000 ns").replace("1700000000003001 ns", "1700000000000003001 ns")
    assert old in md, old
    assert check_markdown(raw, md.replace(old, new)) != []


@pytest.mark.parametrize("old,new", [("s-00000003", "s-00000099"), ("/t3", "/t999"), ("consistent_with_propagation", "inconsistent_order"), ("recording_incomplete", "recording_complete"),
                                     ("Exit code 0", "Exit code 1"), ('"lower_ns":"1700000000000003000"', '"lower_ns":"1700000000000003001"')])
def test_PAR_04_the_verifier_detects_html_corruption(tmp_path, old, new):
    raw, _, html = _md(tmp_path)
    assert old in html, old
    assert check_html(raw, html.replace(old, new)) != []


def test_PAR_05_a_dropped_row_is_detected(tmp_path):
    raw, md, html = _md(tmp_path)
    lines = md.splitlines(); i = next(k for k, l in enumerate(lines) if l.startswith("| s-00000005 |") and " m3c_finding " in l)
    del lines[i]
    assert check_markdown(raw, "\n".join(lines)) != []
    j = html.index('<tr id="s5"'); k = html.index("</tr>", j) + 5
    assert check_html(raw, html[:j] + html[k:]) != []


def test_PAR_06_a_wrong_report_digest_is_detected(tmp_path):
    raw, md, html = _md(tmp_path)
    assert check_markdown(raw + b" ", md) != [] and check_html(raw + b" ", html) != []


def test_PAR_07_the_verifier_does_not_import_the_package():
    import ast, pathlib
    src = pathlib.Path(__file__).with_name("tools").joinpath("parity.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            assert not any("physicore" in n or "investigation" in n for n in names)


def test_PAR_08_verified_output_agrees_too(tmp_path):
    from .fx import EXAMPLE_BAG
    raw = EXAMPLE_JSON.read_bytes()
    assert check_markdown(raw, run_render(EXAMPLE_JSON, "markdown", EXAMPLE_BAG).text) == [] and check_html(raw, run_render(EXAMPLE_JSON, "html", EXAMPLE_BAG).text) == []
