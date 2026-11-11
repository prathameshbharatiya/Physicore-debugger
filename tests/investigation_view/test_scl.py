"""SCL: scale and resource behaviour (NFR-VW-03/04): bounded output, linear growth, the 64 MiB limit. Heavy measurements are in tools/bench.py."""
import time

from physicore_collector.investigation_view import MAX_REPORT_BYTES
from physicore_collector.investigation_view.service import run_render

from .fx import gen, write_report


def timed(fmt, p):
    t = time.time(); r = run_render(p, fmt); return time.time() - t, r


def test_SCL_01_markdown_of_ten_thousand_symptoms(tmp_path):
    p = write_report(tmp_path, gen(10_000, 4))
    dt, r = timed("markdown", p)
    assert r.exit_code == 0 and dt < 60 and "9800 more symptoms not listed here" in r.text


def test_SCL_02_html_of_ten_thousand_symptoms(tmp_path):
    p = write_report(tmp_path, gen(10_000, 4))
    dt, r = timed("html", p)
    assert r.exit_code == 0 and dt < 90 and len(r.text.encode()) < 64 * 1024 * 1024


def test_SCL_03_markdown_size_is_bounded_by_the_row_caps(tmp_path):
    a = run_render(write_report(tmp_path, gen(2_000, 2), "a.json"), "markdown").text
    b = run_render(write_report(tmp_path, gen(8_000, 2), "b.json"), "markdown").text
    assert len(b) < 1.5 * len(a)


def test_SCL_04_runtime_grows_roughly_linearly(tmp_path):
    ts = {}
    for n in (2_500, 5_000, 10_000):
        p = write_report(tmp_path, gen(n, 2), f"{n}.json")
        ts[n] = min(timed("markdown", p)[0] for _ in range(2))
    assert ts[10_000] < 5 * ts[5_000] and ts[5_000] < 5 * ts[2_500]


def test_SCL_05_an_oversized_file_is_refused_quickly(tmp_path):
    p = tmp_path / "big.json"
    with open(p, "wb") as fh:
        fh.truncate(MAX_REPORT_BYTES + 1)
    dt, r = timed("html", p)
    assert r.exit_code == 3 and dt < 5 and "larger than" in r.problems[0]


def test_SCL_06_blockers_are_not_capped_and_still_fast(tmp_path):
    p = write_report(tmp_path, gen(5, 1, blockers=3000))
    dt, r = timed("html", p)
    assert r.exit_code == 0 and dt < 30 and r.text.count('<tr id="b') == 3000


def test_SCL_07_timeline_bars_per_lane_are_capped_in_the_page(tmp_path):
    p = write_report(tmp_path, gen(3_000, 2))
    html = run_render(p, "html").text
    assert html.count('<rect class="bar"') <= 2 * 300


def test_SCL_08_a_report_at_the_limit_is_accepted(tmp_path, monkeypatch):
    import physicore_collector.investigation_view.load as L
    p = write_report(tmp_path, gen(50, 1)); n = p.stat().st_size
    monkeypatch.setattr(L, "MAX_REPORT_BYTES", n)
    assert run_render(p, "markdown").exit_code == 0
    monkeypatch.setattr(L, "MAX_REPORT_BYTES", n - 1)
    assert run_render(p, "markdown").exit_code == 3
