"""ADV: adversarial reports and inputs: every threat of the security model of the plan (section 4.6)."""
import json
import re

import pytest
from markdown_it import MarkdownIt

from physicore_collector.investigation_view.html_static import render_html
from physicore_collector.investigation_view.markdown import render_markdown
from physicore_collector.investigation_view.service import run_render

from .fx import example_dict, gen, hostile_strings, load, to_bytes, view_of, write_report

FIELDS = ["topic", "node", "category", "detector_or_rule", "source_id", "severity_basis"]


def hostile_report(s, *, blockers=1):
    d = gen(5, 2, blockers=blockers, m2_every=2)
    for sy in d["symptoms"]:
        sy["topic"] = sy["node"] = s
        sy["detector_or_rule"] = s
        sy["identities"] = {"logger_name": s}
        for e in sy["evidence"]:
            e["topic"] = s
    d["lanes"][0]["domain"] = d["lanes"][0]["domain"]
    return d


@pytest.mark.parametrize("s", [h for h in hostile_strings() if "\ud800" not in h])
def test_ADV_01_hostile_text_renders_in_both_formats_without_error(s):
    v = view_of(hostile_report(s))
    assert render_markdown(v) and render_html(v)


def test_ADV_02_hostile_values_never_appear_unescaped_in_html():
    for s in ["<script>alert(1)</script>", '"><img src=x onerror=alert(1)>', "</script><script>alert(2)</script>"]:
        html = render_html(view_of(hostile_report(s)))
        assert html.count("<script") == 2 and "<img src=x" not in html and "alert(1)</script>" not in html


def test_ADV_03_hostile_values_never_become_markdown_links_or_html():
    md = MarkdownIt("commonmark").enable("table")
    for s in ["](http://evil.example)", "<http://evil.example>", "![x](http://evil.example/p.png)", "[a](javascript:alert(1))", "<b>x</b>"]:
        toks = md.parse(render_markdown(view_of(hostile_report(s))))
        types = {t.type for t in toks} | {c.type for t in toks for c in (t.children or [])}
        assert not ({"link_open", "image", "html_inline", "html_block"} & types), s


def test_ADV_04_bidirectional_and_invisible_characters_are_made_visible_everywhere():
    s = "‮evil⁦x⁩​﻿"
    v = view_of(hostile_report(s)); md = render_markdown(v); html = render_html(v)
    for out in (md, html):
        assert not re.search("[‪-‮⁦-⁩​-‏﻿  ]", out)
        assert "\u2423U" in out and "202E" in out


def test_ADV_05_very_long_names_are_truncated_everywhere():
    s = "x" * 100_000
    v = view_of(hostile_report(s))
    assert len(render_markdown(v)) < 200_000 and all(len(x["topic"]) <= 120 for x in v["symptoms"])


def test_ADV_06_a_report_cannot_make_the_page_load_anything():
    d = hostile_report("x")
    for sy in d["symptoms"]:
        sy["topic"] = "http://evil.example/a.js"
        sy["identities"] = {"logger_name": "https://evil.example/b.css"}
    html = render_html(view_of(d))
    for tag_attr in re.findall(r'(?:src|href|action)="([^"]*)"', html):
        assert tag_attr.startswith("#")


def test_ADV_07_hostile_artifact_refs_are_display_text_only(tmp_path):
    d = hostile_report("x")
    for sy in d["symptoms"]:
        for e in sy["evidence"]:
            e["artifact_ref"] = "artifact:../../etc/passwd"
    html = run_render(write_report(tmp_path, d), "html", tmp_path).text
    assert "not_checked" in html and "root:x:0" not in html


def test_ADV_08_empty_report_says_what_it_is_not():
    t = run_render_text(gen(0, 1))
    assert "healthy" not in t.lower() and "No symptom was observed" in t


def run_render_text(d, fmt="markdown", tmp=None):
    import tempfile, pathlib
    p = write_report(pathlib.Path(tempfile.mkdtemp()), d)
    return run_render(p, fmt).text


def test_ADV_09_declared_graph_with_a_cycle_renders():
    d = example_dict(); e0 = d["coverage"]["edge_coverage"][0]
    d["coverage"]["edge_coverage"] = [e0, {**e0, "edge_id": "back", "from_node": e0["to_node"], "to_node": e0["from_node"]}]
    assert "<svg" in run_render_text(d, "html")


def test_ADV_10_a_graph_of_five_thousand_nodes_is_capped():
    d = example_dict(); e0 = d["coverage"]["edge_coverage"][0]
    d["coverage"]["edge_coverage"] = [{**e0, "edge_id": f"e{i}", "from_node": f"/n{i}", "to_node": f"/n{i + 1}"} for i in range(5000)]
    html = run_render_text(d, "html")
    assert "The diagram is not drawn" in html and len(html) < 5_000_000


def test_ADV_11_every_blocker_code_renders():
    from physicore_collector.localization.vocabulary import BLOCKER_CODES
    d = gen(2, 1, blockers=1); proto = d["blockers"][0]
    from physicore_collector.localization import blockers as B
    d["blockers"] = []
    for i, code in enumerate(sorted(B.DETAIL)):
        b = dict(proto); b["blocker_id"] = f"b-{i}"; b["code"] = code; b["detail"] = B.DETAIL[code]
        d["blockers"].append(b)
    v = view_of(d)
    assert len(v["blockers"]) == len(B.DETAIL) and render_markdown(v) and render_html(v)


def test_ADV_12_bounded_unknown_chain_total_is_stated():
    d = gen(6, 1)
    d["chains_total"] = None; d["chains_total_status"] = "bounded_unknown"; d["coverage"]["chains_lower_bound"] = 1
    load(d)
    assert "bounded_unknown" in run_render_text(d)


def test_ADV_13_partial_evidence_report_renders_with_exit_1_reasons():
    d = gen(3, 1); d["exit_code"] = 1; d["exit_reasons"] = ["evidence_partial_or_unverified"]
    assert "Exit code 1: evidence_partial_or_unverified." in run_render_text(d)


def test_ADV_14_deeply_nested_json_is_exit_3_not_a_crash(tmp_path):
    p = tmp_path / "deep.json"; p.write_bytes(b'{"a":' * 20000 + b"1" + b"}" * 20000)
    assert run_render(p, "markdown").exit_code == 3


def test_ADV_15_a_binary_file_is_exit_3(tmp_path):
    p = tmp_path / "bin.json"; p.write_bytes(bytes(range(256)) * 50)
    assert run_render(p, "html").exit_code == 3


def test_ADV_16_a_float_in_the_report_is_refused(tmp_path):
    p = tmp_path / "f.json"; p.write_bytes(to_bytes(example_dict()).replace(b'"edge_count":1', b'"edge_count":1.0'))
    assert run_render(p, "markdown").exit_code == 3


def test_ADV_17_ids_that_collide_after_cleaning_do_not_crash():
    d = gen(3, 1)
    d["symptoms"][0]["symptom_id"] = "s-" + "a" * 300 + "x"
    d["symptoms"][1]["symptom_id"] = "s-" + "a" * 300 + "y"
    for ln in d["lanes"]:
        ln["layers"] = [[s["symptom_id"]] for s in d["symptoms"]]
    d["chains"] = []; d["links"] = []; d["components"] = []; d["hypotheses"] = []; d["chains_total"] = 0
    d["localization"]["components"] = []; d["localization"]["topic_layers"] = []
    d["coverage"]["links_total"] = 0; d["coverage"]["chains_reported"] = 0
    d["lanes"][0]["earliest"]["symptom_ids"] = [d["symptoms"][0]["symptom_id"]]
    v = view_of(d)
    assert render_markdown(v) and render_html(v)


def test_ADV_18_control_characters_in_every_text_field_of_the_example():
    raw = to_bytes(example_dict()).decode()
    s = json.loads(raw)
    s["symptoms"][0]["topic"] = "\x00\x01\x1b[2J\x7f"
    v = view_of(s)
    assert not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", render_markdown(v) + render_html(v))


def test_ADV_19_report_claiming_a_huge_exit_code_is_refused(tmp_path):
    d = example_dict(); d["exit_code"] = 99
    assert run_render(write_report(tmp_path, d), "markdown").exit_code == 3


def test_ADV_20_lone_surrogates_in_a_report_do_not_crash_the_output(tmp_path):
    raw = to_bytes(example_dict()).replace(b'"/rosout"', b'"/ros\\ud800out"', 1)
    p = tmp_path / "s.json"; p.write_bytes(raw)
    r = run_render(p, "html")
    assert r.exit_code == 0 and r.text.encode("utf-8")


def test_ADV_21_html_never_contains_the_word_javascript_scheme_in_an_attribute():
    html = render_html(view_of(hostile_report("javascript:alert(1)")))
    assert not re.search(r'(?:href|src|action)="javascript', html, re.I)


def test_ADV_22_markdown_cells_with_pipes_keep_column_counts():
    md = MarkdownIt("commonmark").enable("table")
    toks = md.parse(render_markdown(view_of(hostile_report("a | b | c |"))))
    widths = {}
    cur = None
    for t in toks:
        if t.type == "table_open": cur = {}
        if t.type == "tr_open": n = 0
        if t.type in ("th_open", "td_open"): n += 1
        if t.type == "tr_close": cur.setdefault(n, 0); cur[n] += 1
        if t.type == "table_close": assert len(cur) == 1, cur


def test_ADV_23_nonexistent_artifact_in_blocker_does_not_break_the_view():
    d = gen(2, 1, blockers=1); d["blockers"][0]["artifacts"] = [{"artifact_ref": "artifact:gone.mcap", "sha256": None}]
    assert "gone" in render_markdown(view_of(d))


def test_ADV_24_exit3_problem_messages_are_cleaned():
    d = example_dict(); d.update(exit_code=3, exit_reasons=["anomaly_config_invalid"], problems=[{"source": "anomaly_config", "path": "a‮b", "message": "\x1b[31mred‮"}],
                                 lanes=[], symptoms=[], links=[], components=[], chains=[], hypotheses=[], blockers=[], caveats=[])
    v = view_of(d)
    out = render_markdown(v) + render_html(v)
    assert "\x1b" not in out and "‮" not in out
