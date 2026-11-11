"""MD: the Markdown report (FR-MD-*): fixed wording, structure, caps, inertness of data."""
import re

import pytest
from markdown_it import MarkdownIt

from physicore_collector.investigation_view.fmt import LIST_CAP
from physicore_collector.investigation_view.markdown import ROW_CAP, render_markdown
from physicore_collector.investigation_view.templates import COLS, HEADINGS, LABEL_MEANING, LEGEND, T, TABLE_TITLES
from physicore_collector.localization.vocabulary import find_forbidden

from .fx import example_dict, gen, hostile_strings, view_of


def md(d=None) -> str:
    return render_markdown(view_of(d))


def parse(text):
    return MarkdownIt("commonmark").enable("table").parse(text)


def flat(text):
    out = []
    for t in parse(text):
        out.append(t.type)
        out += [c.type for c in (t.children or [])]
    return out


def test_MD_01_every_template_and_fixed_text_passes_the_wording_check():
    texts = list(T.values()) + list(LABEL_MEANING.values()) + list(LEGEND.values()) + list(TABLE_TITLES.values()) + HEADINGS + [c for cols in COLS.values() for c in cols]
    for t in texts:
        assert find_forbidden(t) == [], t


def test_MD_02_the_example_report_passes_the_wording_check():
    assert find_forbidden(md()) == []


def test_MD_03_generated_reports_pass_the_wording_check():
    for d in (gen(12, 3, m2_every=3, blockers=2), gen(0, 1)):
        assert find_forbidden(md(d)) == []


def _regexes():
    out = []
    for v in T.values():
        out.append(re.compile("^" + re.sub(r"\\\{[a-z_0-9]+\\\}", ".*", re.escape(v)) + "$"))
    return out


def test_MD_04_every_prose_line_is_a_fixed_template():
    rx = _regexes()
    for d in (example_dict(), gen(12, 3, m2_every=3, blockers=2), gen(0, 1)):
        for ln in md(d).splitlines():
            if not ln or ln.startswith(("|", "#")):
                continue
            body = ln[2:] if ln.startswith("- ") else ln
            assert any(r.match(body) for r in rx), ln


def test_MD_05_sections_appear_in_the_fixed_order():
    text = md()
    pos = [text.index(f"## {h}") for h in HEADINGS]
    assert pos == sorted(pos)


def test_MD_06_one_h1_and_the_fixed_h2_headings():
    toks = [t for t in parse(md(gen(8, 2))) if t.type == "heading_open"]
    assert [t.tag for t in toks].count("h1") == 1 and [t.tag for t in toks].count("h2") == len(HEADINGS)
    assert [t.tag for t in toks].count("h3") == 2


def test_MD_07_tables_are_well_formed():
    for d in (example_dict(), gen(30, 3, m2_every=4, blockers=3)):
        toks = parse(md(d))
        i = 0
        while i < len(toks):
            if toks[i].type == "table_open":
                j = i
                widths = set()
                while toks[j].type != "table_close":
                    if toks[j].type == "tr_open":
                        k, n = j, 0
                        while toks[k].type != "tr_close":
                            n += toks[k].type in ("th_open", "td_open"); k += 1
                        widths.add(n)
                    j += 1
                assert len(widths) == 1, widths
                i = j
            i += 1


def test_MD_08_cap_and_exact_omitted_count():
    text = md(gen(250, 1))
    rows = [ln for ln in text.splitlines() if ln.startswith("| s-")]
    assert len([r for r in rows]) >= ROW_CAP
    assert T["more"].format(n=50, what="symptoms") in text


def test_MD_09_caps_are_stated_in_the_header():
    assert T["caps"].format(cap=ROW_CAP, items=LIST_CAP) in md()


def test_MD_10_blockers_are_never_capped():
    text = md(gen(2, 1, blockers=210))
    assert len([ln for ln in text.splitlines() if ln.startswith("| b-")]) == 210


def test_MD_11_disclaimer_scope_and_limitations_always_present():
    for d in (example_dict(), gen(0, 1)):
        text = md(d)
        assert "Severity:" in text and "Scope:" in text and text.count("Limitation ") == 7


@pytest.mark.parametrize("s", hostile_strings())
def test_MD_12_hostile_data_cannot_change_the_structure(s):
    d = gen(4, 1)
    sy = d["symptoms"][0]
    sy["topic"] = sy["node"] = s
    sy["detector_or_rule"] = s
    sy["identities"] = {"logger_name": s}
    text = md(d)
    base = flat(md(gen(4, 1)))
    got = flat(text)
    forbidden = {"link_open", "image", "html_inline", "html_block", "blockquote_open", "fence", "code_block", "hr", "bullet_list_open", "em_open", "strong_open", "code_inline", "s_open"}
    assert not (forbidden & set(got)) or (forbidden & set(got)) <= (forbidden & set(base))
    assert got.count("heading_open") == base.count("heading_open")
    assert got.count("table_open") == base.count("table_open")
    assert got.count("tr_open") == base.count("tr_open")


def test_MD_13_hostile_data_in_every_text_field():
    d = gen(4, 1, blockers=1)
    s = "](http://evil.example)<b>|\n# x"
    for sy in d["symptoms"]:
        sy["topic"] = sy["node"] = sy["category"] = sy["detector_or_rule"] = s
    base = flat(md(gen(4, 1, blockers=1)))
    got = flat(md(d))
    assert "link_open" not in got and "html_inline" not in got and got.count("heading_open") == base.count("heading_open")


def test_MD_14_no_raw_html_no_images_no_links_in_the_example():
    text = md()
    assert "<" not in text.replace("\\<", "") and "![" not in text and "](" not in text


def test_MD_15_empty_report_says_what_it_is_not():
    text = md(gen(0, 1))
    assert T["no_symptoms"] in text and "healthy" not in text.lower()


def test_MD_16_no_dependencies_statement():
    d = example_dict(); d["coverage"]["dependencies"] = "none_declared"; d["inputs"]["dependencies"] = None
    d.update(links=[], chains=[], components=[], hypotheses=[], chains_total=0)
    d["coverage"].update(links_total=0, edge_coverage=[], links_status=None)
    text = md(d)
    assert T["no_deps"] in text


def test_MD_17_exit3_report_lists_the_problems():
    d = example_dict(); d.update(exit_code=3, exit_reasons=["anomaly_config_invalid"], problems=[{"source": "anomaly_config", "path": "a.b", "message": "bad value"}],
                                 lanes=[], symptoms=[], links=[], components=[], chains=[], hypotheses=[], blockers=[], caveats=[])
    text = md(d)
    assert "Exit code 3" in text and "bad value" in text and T["no_symptoms"] not in text


def test_MD_18_label_meanings_are_listed_for_all_four_labels():
    text = md()
    for lab in LABEL_MEANING:
        assert f"- {lab}: " in text


def test_MD_19_order_note_present():
    assert T["order_note"] in md()


def test_MD_20_lane_axes_are_stated_per_lane():
    text = md(gen(8, 2))
    assert text.count(T["lane_axis"]) == 2


def test_MD_21_times_are_integer_nanoseconds_in_the_output():
    text = md()
    assert re.search(r"\b17000000\d{11} ns\b", text) and not re.search(r"\d\.\d+e", text)


def test_MD_22_the_output_ends_with_one_newline_and_is_ascii_safe_text():
    text = md(); assert text.endswith("\n") and not text.endswith("\n\n")


def test_MD_23_the_evidence_table_lists_every_reference_once():
    v = view_of(gen(7, 1))
    text = render_markdown(v)
    for r in v["evidence_refs"]:
        assert f"| {r['id']} |" in text


def test_MD_24_verification_column_says_not_requested_without_a_run():
    assert "not requested" in md() and T["ver_none"] in md()


def test_MD_25_known_sentences_contain_no_markdown_syntax():
    from physicore_collector.investigation_view.markdown import KNOWN_SENTENCES
    for x in KNOWN_SENTENCES:
        assert not re.search(r"[\\`*_\[\]<>&|~#]", x) and not x[0].isdigit()


def test_MD_26_a_lookalike_sentence_from_a_hostile_report_is_escaped():
    d = gen(2, 1); d["lanes"][0]["earliest"]["statement"] = "**bold** [x](http://e.example)"
    text = md(d)
    assert "link_open" not in flat(text) and "strong_open" not in flat(text)


def test_MD_27_token_columns_print_unescaped_vocabulary():
    assert "consistent_with_propagation" in md() and "consistent\\_with" not in md()


def test_MD_28_long_list_cells_are_cut_with_an_exact_count():
    text = md(gen(300, 1))
    chain_row = next(ln for ln in text.splitlines() if ln.startswith("| ch\\-") or ln.startswith("| ch-"))
    assert " ... and 280 more" in chain_row and chain_row.count(" then ") == 19 + 19


def test_MD_29_short_lists_are_not_cut():
    assert " ... and " not in md(gen(15, 1)).split("## Symptoms")[0].split("## Chains and components")[-1].split("## Hypotheses")[0]
