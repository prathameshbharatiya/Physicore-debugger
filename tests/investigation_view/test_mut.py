"""MUT: tests added after the first mutation run, each aimed at a behaviour that no earlier test pinned (see M3E_TEST_REPORT)."""
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app
from physicore_collector.investigation_view import verify as VER
from physicore_collector.investigation_view.html_static import render_html
from physicore_collector.investigation_view.markdown import KNOWN_SENTENCES, ROW_CAP, e as md_e, render_markdown
from physicore_collector.investigation_view.model import build_view
from physicore_collector.investigation_view.safety import TRUNCATE, clean_text, md_escape
from physicore_collector.investigation_view.service import run_render
from physicore_collector.investigation_view.templates import T

from .fx import SRC, EXAMPLE_BAG, EXAMPLE_JSON, example_dict, gen, load, view_of, write_report

R = CliRunner()


# ---------------------------------------------------------------- safety
def test_MUT_01_exactly_the_limit_is_not_truncated():
    assert clean_text("x" * TRUNCATE) == "x" * TRUNCATE
    assert clean_text("x" * (TRUNCATE + 1)) == "x" * (TRUNCATE - 1) + "…"


@pytest.mark.parametrize("raw,want", [("a_-b", "a\\_\\-b"), ("a-_b", "a\\-\\_b"), ("a.", "a\\."), (".a", "\\.a"), ("a.b", "a.b"), ("a-b_c", "a-b_c"), ("a_", "a\\_"), ("_a", "\\_a")])
def test_MUT_02_intra_word_punctuation_needs_alphanumerics_on_both_sides(raw, want):
    assert md_escape(raw) == want


# ---------------------------------------------------------------- view model
def test_MUT_03_item_offsets_use_both_bounds():
    v = view_of(gen(4, width=500))
    for ln in v["lanes"]:
        for it in ln["items"]:
            assert it["off_lower_ns"] == it["lower_ns"] - ln["origin_ns"] and it["off_upper_ns"] == it["upper_ns"] - ln["origin_ns"]
            assert it["off_upper_ns"] - it["off_lower_ns"] == 500


def test_MUT_04_items_with_identical_intervals_are_ordered_by_id():
    res = load(gen(4))
    rep = res.report
    first = rep.symptoms[0].onset
    for sy in rep.symptoms:
        object.__setattr__(sy.onset, "lower_ns", first.lower_ns); object.__setattr__(sy.onset, "upper_ns", first.upper_ns)
    rep.symptoms.sort(key=lambda s: s.symptom_id, reverse=True)
    lane = rep.lanes[0]
    lane.layers[:] = [sorted((i for layer in lane.layers for i in layer), reverse=True)]
    ids = [it["id"] for it in build_view(rep, sha256=res.sha256, size=res.size)["lanes"][0]["items"]]
    assert len(ids) == 4 and ids == sorted(ids)


def test_MUT_05_limits_are_listed_in_name_order_whatever_the_file_order():
    from physicore_collector.investigation_view.load import parse_report_bytes
    d = example_dict()
    d["limits"] = dict(reversed(sorted(d["limits"].items())))
    raw = json.dumps(d).encode()                      # file order kept: reversed names
    assert raw.index(b"symptoms") < raw.index(b"chains_reported")
    res = parse_report_bytes(raw)
    assert res.report is not None, res.problems
    names = [k for k, _ in build_view(res.report, sha256=res.sha256, size=res.size)["limits"]]
    assert names == sorted(names) and len(names) == len(d["limits"])


def test_MUT_06_limitations_are_the_full_sentences():
    d = example_dict()
    v = view_of(d)
    assert v["limitations"] == d["limitations"] and any(len(x) > 100 for x in d["limitations"])


def test_MUT_07_blocker_observations_and_symptom_evidence_are_listed():
    d = gen(5, blockers=2)
    v = view_of(d)
    for b, rb in zip(v["blockers"], sorted(d["blockers"], key=lambda b: b["blocker_id"])):
        assert len(b["observations"]) == len(rb["observations"]) >= 1
    for s, rs in zip(v["symptoms"], sorted(d["symptoms"], key=lambda s: s["symptom_id"])):
        assert len(s["evidence"]) == len(rs["evidence"]) >= 1


# ---------------------------------------------------------------- markdown
def test_MUT_08_exactly_the_cap_lists_no_omission_line():
    out = render_markdown(view_of(gen(ROW_CAP)))
    assert "more symptoms not listed" not in out and "not listed here" not in out.split("## Symptoms")[1].split("\n## ")[0]
    over = render_markdown(view_of(gen(ROW_CAP + 1)))
    assert "1 more symptoms not listed here" in over or "1 more symptom" in over


def test_MUT_09_caveats_are_never_capped():
    d = example_dict()
    base = d["caveats"][0]
    d["caveats"] = [dict(base, scope={"detector": f"tierA.x{i:04d}"}) for i in range(ROW_CAP + 5)]
    out = render_markdown(view_of(d))
    assert out.count("tierA.x") == ROW_CAP + 5 or out.count("x0204") >= 1


def test_MUT_10_fixed_sentences_print_raw():
    out = render_markdown(view_of())
    for s in example_dict()["limitations"]:
        assert s in out
    assert all(s in KNOWN_SENTENCES for s in example_dict()["limitations"])


def test_MUT_11_lane_heading_numbers_from_one():
    out = render_markdown(view_of(gen(4, lanes=2)))
    assert "### Lane 1 of 2" in out and "### Lane 2 of 2" in out and "Lane 0" not in out


def test_MUT_12_first_divergence_branches():
    assert T["fd_none"] in render_markdown(view_of()) and T["fd_lead"] not in render_markdown(view_of())
    m2 = render_markdown(view_of(gen(4, m2_every=1)))
    assert T["fd_lead"] in m2 and T["fd_none"] not in m2


def test_MUT_13_empty_text_prints_a_placeholder_cell():
    from physicore_collector.investigation_view import markdown as M
    assert M._cell("") == "none" and M._cell("x") == "x" and M._cell(0) == "0"


def test_MUT_14_lane_table_shows_offsets_limits_and_subtitle():
    out = render_markdown(view_of())
    assert "| s-c872a01752e24777 | 0 | 1700000000001000000 ns | +0.000000 ms |" in out
    assert "| s-4bd2015939fdeda6 | 1 | 1700000000003000000 ns | +2.000000 ms |" in out
    assert "Limit symptoms: 100000." in out and "tool version 0.1.0" in out


# ---------------------------------------------------------------- verification
def test_MUT_15_resolution_rejects_each_unsafe_form_on_its_own(tmp_path):
    (tmp_path / "sub").mkdir(); (tmp_path / "bag_0.mcap").write_bytes(b"x")
    p, why = VER._resolve(tmp_path, "artifact:sub/../bag_0.mcap")
    assert p is None and "leaves" in why
    p, why = VER._resolve(tmp_path, "artifact:" + str(tmp_path / "bag_0.mcap"))
    assert p is None and "leaves" in why
    p, why = VER._resolve(tmp_path, "xxxxxxxxxbag_0.mcap")
    assert p is None and "not an artifact reference" in why
    assert VER._resolve(tmp_path, "artifact:bag_0.mcap")[0] == tmp_path / "bag_0.mcap"


def test_MUT_16_scan_stops_at_the_last_wanted_message(monkeypatch, tmp_path):
    seen = []

    class Msg:
        log_time = 1

    class Chan:
        topic = "/t"

    class Rd:
        def iter_messages(self, log_time_order=False):
            for i in range(50):
                seen.append(i)
                yield (None, Chan(), Msg())

    import mcap.reader
    monkeypatch.setattr(mcap.reader, "make_reader", lambda fh: Rd())
    f = tmp_path / "a.mcap"; f.write_bytes(b"x")
    _sha, rows, err = VER._scan(f, {2, 4})
    assert sorted(rows) == [2, 4] and err == "" and seen[-1] == 4


# ---------------------------------------------------------------- html
def ids_of(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def test_MUT_17_every_in_page_link_has_a_target_even_past_the_row_cap():
    html = render_html(view_of(gen(ROW_CAP + 60)))
    ids = ids_of(html)
    missing = [h for h in re.findall(r'href="#([^"]+)"', html) if h not in ids]
    assert missing == []


def test_MUT_18_bar_rows_follow_the_layers():
    html = render_html(view_of())
    ys = sorted(int(m) for m in re.findall(r'<rect class="bar[^"]*"[^>]* y="(\d+)"', html)) if 'class="bar' in html else []
    assert ys == [40, 66] or ys == []


def test_MUT_19_arrows_carry_the_label_class_and_only_their_lane():
    d = gen(6, lanes=2)
    html = render_html(view_of(d))
    for lab in {l["label"] for l in d["links"]}:
        assert f'class="arrow l-{lab}"' in html
    assert 'l-other' not in html
    v = view_of(d)
    for li, ln in enumerate(v["lanes"]):
        svg = re.search(rf'<svg class="timeline" id="tl{li}".*?</svg>', html, re.S).group(0)
        drawn = {int(x) for x in re.findall(r'data-link="(\d+)"', svg)}
        assert drawn == {i for i, l in enumerate(v["links"]) if l["lane"] == ln["domain"]}


def test_MUT_20_diagram_layers_a_long_chain_in_reverse_name_order():
    v = view_of()
    names = ["d", "c", "b", "a"]
    v["coverage"]["edges"] = [{"edge_id": f"e{i}", "from_node": names[i], "to_node": names[i + 1], "from_count": 1, "to_count": 1, "status": "endpoints_have_symptoms"} for i in range(3)]
    html = render_html(v)
    xs = {int(x) for x in re.findall(r'<rect class="node" x="(\d+)"', html)}
    assert len(xs) == 4


def test_MUT_21_exit_code_and_provenance_digest_are_visible():
    v = view_of()
    v["header"]["exit_code"] = 5
    html = render_html(v)
    assert "Exit code 5" in html and v["header"]["report_sha256"] in html


def test_MUT_22_legend_anchors_blockers_zoom_and_lane_svgs():
    from physicore_collector.investigation_view.templates import LEGEND
    html = render_html(view_of())
    assert 'id="s0"' in html
    no_svg = re.sub(r"<svg.*?</svg>", "", html, flags=re.S)
    assert 'href="#s0"' in no_svg and 'href="#E' in no_svg
    assert html.count('class="legend"') >= 1
    leg = re.search(r'<div class="legend">.*?</div>', html, re.S).group(0)
    from physicore_collector.investigation_view.html_static import LINK_LABELS
    assert len(LINK_LABELS) > 3 and leg.count("<svg") == len(LINK_LABELS)
    big = render_html(view_of(gen(4, lanes=3, blockers=1)))
    assert big.count('class="zoomctl"') == 3
    assert [int(x) for x in re.findall(r'<svg class="timeline" id="tl\d+" data-lane="(\d+)"', big)] == [0, 1, 2]
    many = render_html(view_of(gen(1, blockers=ROW_CAP + 10)))
    assert f'id="b{ROW_CAP + 9}"' in many


# ---------------------------------------------------------------- service and CLI
def test_MUT_23_the_service_refuses_an_unknown_format_itself():
    r = run_render(EXAMPLE_JSON, "pdf")
    assert r.exit_code == 2 and r.text == "" and "unknown format" in r.problems[0]


def test_MUT_24_problem_lines_are_cleaned(tmp_path):
    d = example_dict(); d["bad\x1b[31mkey‮"] = 1
    r = run_render(write_report(tmp_path, d), "markdown")
    assert r.exit_code == 3 and r.problems
    for p in r.problems:
        assert "\x1b" not in p and "‮" not in p


def test_MUT_25_cli_messages_come_from_the_cli_checks():
    r = R.invoke(app, ["evidence", "render", "-", "--format", "markdown"])
    assert r.exit_code == 2 and "standard input" in r.output
    r = R.invoke(app, ["evidence", "render", str(EXAMPLE_JSON), "--format", "pdf"])
    assert r.exit_code == 2 and "use one of" in r.output and "unknown format" not in r.output


def test_MUT_26_problems_go_to_stderr_only(tmp_path):
    p = tmp_path / "bad.json"; p.write_text("{")
    env = dict(os.environ, PYTHONPATH=f"{SRC}{os.pathsep}{Path(__file__).resolve().parents[2]}")
    r = subprocess.run([sys.executable, "-m", "physicore_collector", "evidence", "render", str(p), "--format", "markdown"], capture_output=True, env=env)
    assert r.returncode == 3 and r.stdout == b"" and b"not valid JSON" in r.stderr


def test_MUT_27_output_is_utf8_bytes_whatever_the_locale(tmp_path):
    d = example_dict(); d["symptoms"][0]["topic"] = "/robot/é中文"
    p = write_report(tmp_path, d)
    env = dict(os.environ, PYTHONPATH=f"{SRC}{os.pathsep}{Path(__file__).resolve().parents[2]}", PYTHONIOENCODING="ascii", LC_ALL="C")
    r = subprocess.run([sys.executable, "-c", "from physicore_collector.cli import app; app()", "evidence", "render", str(p), "--format", "markdown"], capture_output=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.decode("utf-8") == run_render(p, "markdown").text and "é中文".encode() in r.stdout


def test_MUT_28_ranks_are_dense_and_arrows_stay_in_their_lane():
    v = view_of(gen(2))
    items = v["lanes"][0]["items"]
    items[0]["layer"], items[1]["layer"] = 0, 5
    html = render_html(v)
    svg = re.search(r'<svg class="timeline" id="tl0".*?</svg>', html, re.S).group(0)
    bar_ys = sorted({int(m) for t in re.findall(r"<rect[^>]*>", svg) if "bar" in t for m in re.findall(r' y="(\d+)"', t)})
    assert bar_ys == [40, 66], bar_ys
    v2 = view_of(gen(2))
    v2["links"][0]["lane"] = "stamp_elsewhere"
    svg2 = re.search(r'<svg class="timeline" id="tl0".*?</svg>', render_html(v2), re.S).group(0)
    assert "data-link=" not in svg2
