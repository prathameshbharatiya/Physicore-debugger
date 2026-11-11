"""SF: text safety (FR-SF-*): cleaning, truncation, and the three escapers against attack corpora."""
import json
import re
import unicodedata

import pytest

from physicore_collector.investigation_view.safety import TRUNCATE, clean_text, html_escape, json_for_script, md_escape

from .fx import hostile_strings


@pytest.mark.parametrize("ch", ["\x00", "\x07", "\t", "\n", "\r", "\x1b", "\x7f", "\x85", "​", "‌", "‍", "‎", "‏", " ", " ", "‪", "‫", "‬",
                                "‭", "‮", "⁠", "⁦", "⁧", "⁨", "⁩", "﻿", "­", "\ud800", "\udfff", "", "\U000e0001"])
def test_SF_01_every_dangerous_code_point_becomes_a_visible_placeholder(ch):
    out = clean_text("a" + ch + "b")
    assert ch not in out and out.startswith("a␣U+") and out.endswith("b")


@pytest.mark.parametrize("ch", [" ", " ", "　", " "])
def test_SF_02_other_space_separators_become_a_plain_space(ch):
    assert clean_text("a" + ch + "b") == "a b"


def test_SF_03_ordinary_text_is_unchanged():
    s = "/robot/arm_1 é中文 \U0001f600 tierA.ros_log"
    assert clean_text(s) == s


@pytest.mark.parametrize("n,expect_len", [(119, 119), (120, 120), (121, 120), (500, 120)])
def test_SF_04_truncation_boundary(n, expect_len):
    out = clean_text("x" * n)
    assert len(out) == expect_len and (n <= TRUNCATE or out.endswith("…"))


def test_SF_05_no_limit():
    assert len(clean_text("x" * 5000, None)) == 5000


def test_SF_06_a_placeholder_is_never_cut_in_half():
    for lim in range(1, 60):
        for src in ["a\u202eb" * 10, "\u202e" * 30, "x" * 117 + "\u202e" + "y" * 50]:
            t = clean_text(src, lim)
            assert len(t) <= lim
            assert not re.findall(r"\u2423(?!U\+[0-9A-F]{4})", t), (lim, t)


def test_SF_07_cleaning_is_idempotent_on_its_output():
    for s in hostile_strings():
        once = clean_text(s, None)
        assert clean_text(once, None) == once


def test_SF_08_no_dangerous_category_survives_cleaning():
    for s in hostile_strings():
        for ch in clean_text(s, None):
            assert unicodedata.category(ch) not in {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}


@pytest.mark.parametrize("s", hostile_strings())
def test_SF_09_html_escape_leaves_no_markup_character(s):
    out = html_escape(s)
    assert "<" not in out and ">" not in out and '"' not in out and "'" not in out
    assert not re.search(r"&(?!amp;|lt;|gt;|quot;|#39;)", out)


def test_SF_10_html_escape_round_trips_through_an_html_parser():
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True); self.text = []; self.tags = []
        def handle_data(self, d): self.text.append(d)
        def handle_starttag(self, t, a): self.tags.append(t)
    for s in hostile_strings():
        p = P(); p.feed("<p>" + html_escape(s, None) + "</p>")
        assert p.tags == ["p"] and "".join(p.text) == clean_text(s, None)


def _md(text):
    from markdown_it import MarkdownIt
    return MarkdownIt("commonmark").enable("table")


@pytest.mark.parametrize("s", hostile_strings())
def test_SF_11_md_escaped_text_forms_no_markup_in_a_paragraph(s):
    md = _md(None)
    for template in ["Topic {}.", "x {} y", "| {} |"]:
        text = template.format(md_escape(s))
        if template.startswith("|"):
            text = "| A |\n|---|\n" + text
        toks = md.parse(text)
        flat = []
        for t in toks:
            flat.append(t.type)
            for c in (t.children or []):
                flat.append(c.type)
        bad = {"link_open", "image", "html_inline", "html_block", "heading_open", "bullet_list_open", "ordered_list_open", "blockquote_open", "fence", "code_block", "hr",
               "em_open", "strong_open", "s_open", "code_inline"}
        assert not (bad & set(flat)), (s, template, flat)
        if template.startswith("|"):
            rows = [t for t in toks if t.type == "tr_open"]
            assert len(rows) == 2


def test_SF_12_md_escape_visible_text_equals_the_cleaned_text():
    import html as H
    md = _md(None)
    for s in hostile_strings():
        rendered = md.render("x " + md_escape(s))
        txt = H.unescape(re.sub(r"</?p>", "", rendered))
        assert " ".join(txt.split()) == " ".join(("x " + clean_text(s)).split()), s


def test_SF_13_md_escape_keeps_ordinary_identifiers_readable():
    for s in ["/rosout", "tierA.ros_log", "s-4bd2015939fdeda6", "constants_from_builtin_table", "a-b", "x.y"]:
        assert md_escape(s) == s


@pytest.mark.parametrize("s,expect", [("-x", "\\-x"), ("x-", "x\\-"), ("_x_", "\\_x\\_"), ("www.a.b", "www\\.a.b"), ("a@b.c", "a\\@b.c"), ("http://x", "http\\://x"),
                                      ("a|b", "a\\|b"), ("*", "\\*"), ("`", "\\`"), ("<", "\\<"), ("&", "\\&"), ("[x]", "\\[x\\]"), ("#", "\\#"), ("a\\b", "a\\\\b")])
def test_SF_14_md_escape_examples(s, expect):
    assert md_escape(s) == expect


def test_SF_15_md_escape_output_is_one_line():
    for s in hostile_strings():
        assert "\n" not in md_escape(s) and "\r" not in md_escape(s)


def test_SF_16_json_for_script_has_no_markup_and_is_ascii():
    for s in hostile_strings():
        out = json_for_script({"k": s})
        assert "<" not in out and ">" not in out and "&" not in out
        assert out.isascii() and " " not in out and " " not in out
        assert "</script" not in out.lower() and "<!--" not in out and "]]>" not in out


def test_SF_17_json_for_script_round_trips():
    for s in hostile_strings():
        if "\ud800" in s:
            continue
        assert json.loads(json_for_script({"k": s, "l": [s]})) == {"k": s, "l": [s]}


def test_SF_18_times_become_strings_only_for_ns_keys():
    out = json.loads(json_for_script({"lower_ns": 2**63, "count": 5, "x": {"upper_ns": 7}, "none_ns": None, "list": [{"a_ns": 1}], "flag": True, "b_ns": True}))
    assert out["lower_ns"] == str(2**63) and out["count"] == 5 and out["x"]["upper_ns"] == "7" and out["none_ns"] is None and out["list"][0]["a_ns"] == "1" and out["flag"] is True
    assert out["b_ns"] is True


def test_SF_19_json_for_script_is_deterministic_and_sorted():
    a = json_for_script({"b": 1, "a": {"d": 1, "c": 2}})
    assert a == '{"a":{"c":2,"d":1},"b":1}'


def test_SF_20_json_for_script_rejects_nan():
    with pytest.raises(ValueError):
        json_for_script({"x": float("nan")})


def test_SF_21_lone_surrogate_in_json_data_is_escaped_not_crashing():
    out = json_for_script({"x": "a\ud800b"})
    assert out.isascii()


def test_SF_22_escapers_accept_non_strings():
    assert clean_text(5) == "5" and md_escape(None) == "None" and html_escape(3) == "3"
