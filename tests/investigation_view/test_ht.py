"""HT: the static HTML page (FR-HT-*): structure, offline-ness, CSP, SVG geometry, inertness, completeness without JavaScript."""
import base64
import hashlib
import json
import re
from html.parser import HTMLParser

import pytest

from physicore_collector.investigation_view import html_assets as A
from physicore_collector.investigation_view.fmt import x_pos
from physicore_collector.investigation_view.html_static import MAXBARS, render_html

W, LEFT = 1000, 12      # the documented layout of the timeline, kept independent of the module under test
from physicore_collector.investigation_view.templates import COLS, HEADINGS, LABEL_MEANING, T
from physicore_collector.localization.vocabulary import find_forbidden

from .fx import example_dict, gen, hostile_strings, view_of

VOID = {"meta", "link", "br", "hr", "input", "img"}
SVG_EMPTY = {"path", "line", "rect", "circle"}


class Doc(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.tags, self.attrs, self.text, self.ids = [], [], [], [], [], []
        self.scripts = []; self._in_script = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag); self.attrs.append((tag, dict(attrs)))
        if "id" in dict(attrs): self.ids.append(dict(attrs)["id"])
        if tag in ("script", "style"): self._in_script = [tag, dict(attrs), []]
        if tag not in VOID and tag not in SVG_EMPTY: self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in SVG_EMPTY: return
        if tag in ("script", "style") and self._in_script: self.scripts.append((self._in_script[0], self._in_script[1], "".join(self._in_script[2]))); self._in_script = None
        if not self.stack or self.stack[-1] != tag: self.errors.append((tag, self.stack[-3:]))
        else: self.stack.pop()

    def handle_data(self, d):
        if self._in_script: self._in_script[2].append(d)
        else: self.text.append(d)


def parse(html):
    d = Doc(); d.feed(html); d.close(); return d


def page(d=None):
    return render_html(view_of(d))


def test_HT_01_parses_with_balanced_tags():
    for d in (example_dict(), gen(30, 3, m2_every=4, blockers=2), gen(0, 1)):
        p = parse(page(d)); assert not p.errors and not p.stack


def test_HT_02_one_h1_and_ordered_headings():
    p = parse(page(gen(8, 2)))
    hs = [t for t in p.tags if re.fullmatch(r"h[1-6]", t)]
    assert hs.count("h1") == 1
    levels = [int(t[1]) for t in hs]
    assert all(b <= a + 1 for a, b in zip(levels, levels[1:])) and levels[0] == 1
    assert hs.count("h2") == len(HEADINGS)


def test_HT_03_no_external_reference_of_any_kind():
    html = page(gen(10, 2, blockers=1))
    p = parse(html)
    for tag, a in p.attrs:
        for k, v in a.items():
            if k in ("href", "src", "action", "poster", "srcset", "data", "formaction", "xlink:href"):
                assert v.startswith("#"), (tag, k, v)
    for kind, _a, body in p.scripts:
        if kind == "style":
            assert "@import" not in body and not re.search(r"url\((?!#)", body), "css url"
    assert "http://" not in html.replace("http://www.w3.org/2000/svg", "") and "https://" not in html


def test_HT_04_no_network_primitive_in_the_script():
    js = A.JS
    for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "import(", "importScripts", "sendBeacon", "EventSource", "navigator.serviceWorker", "window.open", "location.href", "location.assign", "document.write"):
        assert bad not in js, bad
    assert "http://www.w3.org/2000/svg" in js and js.count("http") == 1


def test_HT_05_no_dynamic_code_and_no_html_injection_in_the_script():
    js = A.JS
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "Function(", "setTimeout(\"", "setAttribute(\"on", "srcdoc", "createContextualFragment", "DOMParser"):
        assert bad not in js, bad
    assert "localStorage" not in js and "sessionStorage" not in js and "indexedDB" not in js and "document.cookie" not in js


def test_HT_06_times_are_never_converted_with_number_or_parseint():
    js = A.JS
    assert "parseInt" not in js and "parseFloat" not in js
    for m in re.finditer(r"Number\(([^)]*)\)", js):
        assert not re.search(r"_ns|lower|upper|off|time", m.group(1)), m.group(0)


def test_HT_07_csp_meta_hashes_match_the_inlined_script_and_style():
    html = page()
    p = parse(html)
    meta = [a for t, a in p.attrs if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"]
    assert len(meta) == 1
    csp = meta[0]["content"]
    for kind, _a, body in p.scripts:
        if kind == "style" or _a.get("type") != "application/json":
            h = "sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            assert f"'{h}'" in csp, kind
    assert "default-src 'none'" in csp and "connect-src 'none'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp and "base-uri 'none'" in csp


def test_HT_08_no_inline_event_handler_and_no_style_attribute():
    p = parse(page(gen(12, 3, m2_every=3, blockers=1)))
    for tag, a in p.attrs:
        assert not any(k.startswith("on") for k in a), (tag, a)
        assert "style" not in a and "srcdoc" not in a


def test_HT_09_data_block_is_inert_json_with_string_times():
    p = parse(page())
    blocks = [(a, b) for k, a, b in p.scripts if a.get("type") == "application/json"]
    assert len(blocks) == 1 and blocks[0][0]["id"] == "view-data"
    data = json.loads(blocks[0][1])
    assert data["symptoms"][0]["onset"]["lower_ns"] == str(view_of()["symptoms"][0]["onset"]["lower_ns"])
    assert "<" not in blocks[0][1] and ">" not in blocks[0][1]


def test_HT_10_every_section_is_present_in_the_static_page():
    p = parse(page())
    for i in range(len(HEADINGS)):
        assert f"sec{i}" in p.ids
    text = " ".join(p.text)
    for h in HEADINGS:
        assert h in text


def test_HT_11_semantic_structure():
    p = parse(page(gen(6, 2)))
    html = page(gen(6, 2))
    assert '<html lang="en">' in html and "<main" in html and "<nav" in html and "<header" in html and "<footer" in html and 'class="skip"' in html
    assert p.tags.count("table") == p.tags.count("caption")
    assert all(a.get("scope") == "col" for t, a in p.attrs if t == "th")
    assert "prefers-color-scheme:dark" in A.CSS and "@media print" in A.CSS


def test_HT_12_every_table_header_is_a_fixed_column_name():
    p = parse(page(gen(6, 2, blockers=1)))
    allowed = {c for cols in COLS.values() for c in cols}
    ths = []
    cap = False
    for _t in p.text: pass
    html = page(gen(6, 2, blockers=1))
    for m in re.finditer(r'<th scope="col">([^<]*)</th>', html):
        assert m.group(1) in allowed


def test_HT_13_the_output_passes_the_wording_check():
    for d in (example_dict(), gen(12, 3, m2_every=3, blockers=2), gen(0, 1)):
        text = " ".join(parse(page(d)).text)
        assert find_forbidden(text) == []


def test_HT_14_counts_in_the_page_equal_the_view():
    v = view_of(gen(15, 3, blockers=2)); html = render_html(v)
    assert T["counts"].format(symptoms=15, links=len(v["links"]), links_total=v["counts"]["links_total"], components=3, chains=3, blockers=2, caveats=v["counts"]["caveats"], refs=15) in html


def _bars(html, lane):
    svg = re.search(rf'<svg class="timeline" id="tl{lane}".*?</svg>', html, re.S).group(0)
    return [tuple(int(x) for x in m) for m in re.findall(r'<rect class="bar" data-sym="-?\d+" x="(\d+)" y="(\d+)" width="(\d+)"', svg)]


def test_HT_15_bar_geometry_equals_an_independent_integer_computation():
    import random
    rnd = random.Random(3)
    for _ in range(10):
        d = gen(rnd.randint(2, 25), rnd.randint(1, 3), width=rnd.choice([0, 200, 800]), step=rnd.choice([1000, 7919]))
        v = view_of(d); html = render_html(v)
        for li, ln in enumerate(v["lanes"]):
            items = ln["items"][:MAXBARS]
            got = _bars(html, li)
            assert len(got) == len(items) and len(got) <= 300
            for (x, _y, w), it in zip(got, items):
                x1 = LEFT + ((it["off_lower_ns"]) * W * 2 + ln["span_ns"]) // (ln["span_ns"] * 2) if ln["span_ns"] else LEFT
                x2 = LEFT + ((it["off_upper_ns"]) * W * 2 + ln["span_ns"]) // (ln["span_ns"] * 2) if ln["span_ns"] else LEFT
                assert x == x1 and w == max(x2 - x1, 4)


def test_HT_16_each_lane_has_its_own_svg_and_header_text():
    html = page(gen(9, 3))
    assert html.count('<svg class="timeline"') == 3 and html.count(T["lane_axis"]) == 3


def test_HT_17_ticks_are_integer_offsets_of_the_lane():
    v = view_of(gen(5, 1, step=1_000_000)); html = render_html(v)
    svg = re.search(r'<svg class="timeline".*?</svg>', html, re.S).group(0)
    assert "+0.000000 ms" in svg and f"+{v['lanes'][0]['span_ns'] // 1_000_000}.000000 ms" in svg


def test_HT_18_arrows_are_distinguished_by_class_and_text_not_colour_alone():
    html = page(gen(6, 1))
    assert 'class="arrow l-consistent_with_propagation"' in html
    for lab in ("outside_declared_window", "inconsistent_order", "undetermined"):
        assert f".l-{lab}{{stroke-dasharray" in A.CSS.replace("svg ", "")
    for lab, meaning in LABEL_MEANING.items():
        assert lab in html


def test_HT_19_legend_states_what_an_arrow_means():
    html = page()
    assert "receipt order and the declared window, nothing more" in html


def test_HT_20_static_page_is_complete_without_javascript():
    html = page(gen(12, 2, blockers=1))
    p = parse(html)
    text = " ".join(p.text)
    for s in view_of(gen(12, 2, blockers=1))["symptoms"]:
        assert s["id"] in text
    assert "hidden" in html and 'id="controls-symptoms" hidden' in html


def test_HT_21_static_tables_are_capped_with_an_exact_omitted_count():
    html = page(gen(260, 1))
    assert T["more"].format(n=60, what="symptoms") in html
    assert html.count(' id="s') >= 200


def test_HT_22_timeline_bars_are_capped_and_the_cap_is_stated():
    html = page(gen(400, 1))
    assert "first 300 of 400 symptoms" in html and len(_bars(html, 0)) == 300


def test_HT_23_anchors_link_to_rows_that_exist():
    p = parse(page(gen(30, 2, blockers=1)))
    ids = set(p.ids)
    for t, a in p.attrs:
        if t == "a" and a.get("href", "").startswith("#"):
            assert a["href"][1:] in ids, a


def test_HT_24_ids_are_unique():
    p = parse(page(gen(30, 2, blockers=1)))
    assert len(p.ids) == len(set(p.ids))


def test_HT_25_hostile_data_is_inert():
    for s in hostile_strings():
        d = gen(4, 1, blockers=1)
        sy = d["symptoms"][0]; sy["topic"] = sy["node"] = sy["detector_or_rule"] = s; sy["identities"] = {"logger_name": s}
        html = page(d)
        p = parse(html)
        assert not p.errors, s
        assert not any(k.startswith("on") for t, a in p.attrs for k in a)
        assert len([t for t in p.tags if t == "script"]) == 2 and "<img" not in html.replace("&lt;img", "") and "<svg onload" not in html


def test_HT_26_hostile_text_stays_in_text_nodes():
    d = gen(3, 1)
    d["symptoms"][0]["topic"] = '"><script>window.__pwn=1</script>'
    html = page(d)
    assert html.count("<script") == 2 and "&lt;script&gt;" in html


def test_HT_27_diagram_is_drawn_from_declared_edges_with_caps():
    html = page()
    assert '<svg class="diagram"' in html and "Declared edges between nodes" in html


def test_HT_28_diagram_is_deterministic_and_cycles_do_not_loop():
    d = example_dict()
    e0 = d["coverage"]["edge_coverage"][0]
    d["coverage"]["edge_coverage"] = [e0, {**e0, "edge_id": "e-back", "from_node": e0["to_node"], "to_node": e0["from_node"]}]
    a, b = page(d), page(d)
    assert a == b and '<svg class="diagram"' in a


def test_HT_29_diagram_over_the_node_cap_is_replaced_by_a_notice():
    d = example_dict()
    e0 = d["coverage"]["edge_coverage"][0]
    d["coverage"]["edge_coverage"] = [{**e0, "edge_id": f"e{i}", "from_node": f"/n{i}", "to_node": f"/n{i + 1}"} for i in range(250)]
    html = page(d)
    assert "The diagram is not drawn" in html and '<svg class="diagram"' not in html


def test_HT_30_blockers_and_limitations_are_not_collapsible():
    html = page(gen(3, 1, blockers=1))
    for i in (0, 7, 9):
        m = re.search(rf'<section id="sec{i}".*?</section>', html, re.S).group(0)
        assert "<details" not in m


def test_HT_31_other_sections_are_open_details():
    html = page()
    assert html.count("<details open>") == len(HEADINGS) - 3


def test_HT_32_title_and_generator_meta():
    html = page()
    assert "<title>Investigation report</title>" in html and "<meta charset=\"utf-8\">" in html


def test_HT_33_page_is_deterministic():
    d = gen(20, 2, m2_every=3, blockers=1)
    assert page(d) == page(json.loads(json.dumps(d)))


def test_HT_34_page_is_ascii_clean_of_control_characters():
    html = page(gen(4, 1))
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", html)


def test_HT_35_empty_report_page():
    html = page(gen(0, 1))
    assert T["no_symptoms"] in html and not parse(html).errors


def test_HT_36_exit3_page_lists_the_problems():
    d = example_dict(); d.update(exit_code=3, exit_reasons=["anomaly_config_invalid"], problems=[{"source": "anomaly_config", "path": "a.b", "message": "bad value"}],
                                 lanes=[], symptoms=[], links=[], components=[], chains=[], hypotheses=[], blockers=[], caveats=[])
    html = page(d)
    assert "bad value" in html and 'class="warn"' in html
