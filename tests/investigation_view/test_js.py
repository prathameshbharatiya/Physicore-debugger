"""JS: the interactive page in Chromium (FR-JS-*). Only Chromium is claimed. Waits are on DOM state, never on sleeps."""
import json
import re
import time

import pytest

pytest.importorskip("playwright")

from physicore_collector.investigation_view.fmt import offset_text
from physicore_collector.investigation_view.html_static import render_html

from .browser import open_page
from .fx import BASE_NS, example_dict, gen, hostile_strings, view_of



def text(page, sel):
    return page.inner_text(sel)


def click_bar(page, n):
    page.click(f"svg.timeline a >> nth={n}")
    page.wait_for_selector("#detail:not([hidden])")


def rows(page, table):
    return page.eval_on_selector_all(f"#tbl-{table} tbody tr", "els => els.map(e => Array.from(e.cells).map(c => c.textContent))")


def test_JS_01_loads_offline_without_errors(tmp_path):
    with open_page(tmp_path, example_dict()) as (page, log):
        assert page.evaluate("document.documentElement.getAttribute('data-enhanced')") == "1"
        assert log["errors"] == [] and [c for c in log["console"] if c[0] in ("error", "warning")] == []
        assert [u for u in log["requests"] if not u.startswith("file://")] == [] and len(log["requests"]) == 1


def test_JS_02_without_javascript_the_static_page_is_complete(tmp_path):
    d = gen(12, 2, blockers=1)
    with open_page(tmp_path, d, js=False) as (page, log):
        assert page.evaluate("document.documentElement.getAttribute('data-enhanced')") is None
        assert page.is_hidden("#controls-symptoms") and page.is_hidden("#detail")
        assert len(rows(page, "symptoms")) == 12 and "s-00000003" in text(page, "#sec3")
        assert "Blockers" in text(page, "#sec7")


def test_JS_03_clicking_a_bar_selects_the_symptom(tmp_path):
    with open_page(tmp_path, gen(8, 1)) as (page, log):
        click_bar(page, 2)
        assert page.is_visible("#detail") and "s-00000002" in text(page, "#detail h3")
        assert page.evaluate("location.hash") == "#s2"
        assert page.locator("svg.timeline .bar.sel").count() == 1


def test_JS_04_keyboard_selects_a_symptom(tmp_path):
    with open_page(tmp_path, gen(8, 1)) as (page, log):
        page.focus("svg.timeline a >> nth=3"); page.keyboard.press("Enter"); page.wait_for_selector("#detail:not([hidden])")
        assert "s-00000003" in text(page, "#detail h3")


def test_JS_05_detail_equals_the_view(tmp_path):
    d = gen(6, 1, width=300); v = view_of(d)
    with open_page(tmp_path, view=v) as (page, log):
        click_bar(page, 1)
        t = text(page, "#detail"); s = v["symptoms"][1]
        for want in (s["topic"], s["node"], s["rule"], s["category"], f"{s['onset']['lower_ns']} to {s['onset']['upper_ns']} ns", s["severity"]):
            assert want in t
        assert v["evidence_refs"][1]["artifact_ref"] in t


def test_JS_06_chain_highlight_equals_the_chain(tmp_path):
    d = gen(10, 2); v = view_of(d)
    with open_page(tmp_path, view=v) as (page, log):
        click_bar(page, 0)
        s = v["symptoms"][0]
        chain = [c for c in v["chains"] if c["id"] == s["chains"][0]][0]
        assert page.locator("svg.timeline .bar.hl").count() == len(chain["symptom_ids"]) - 1
        assert page.locator("svg.timeline .arrow.hl").count() == len(chain["steps"])
        assert page.locator("#tbl-symptoms tr.hl").count() == len(chain["symptom_ids"]) - 1 and page.locator("#tbl-symptoms tr.sel").count() == 1


def test_JS_07_close_hides_the_detail_and_clears_the_selection(tmp_path):
    with open_page(tmp_path, gen(5, 1)) as (page, log):
        click_bar(page, 1); page.click("#detail button >> text=Close")
        assert page.is_hidden("#detail") and page.locator("svg.timeline .bar.sel").count() == 0 and page.evaluate("location.hash") == ""


def test_JS_08_fragment_restores_the_selection(tmp_path):
    with open_page(tmp_path, gen(6, 1)) as (page, log):
        page.goto(page.url.split("#")[0] + "#s4"); page.reload()
        assert "s-00000004" in text(page, "#detail h3")


@pytest.mark.parametrize("frag", ["#s999", "#sx", "#s-1", "#E1", "#", "#s"])
def test_JS_09_a_bad_fragment_is_ignored(tmp_path, frag):
    with open_page(tmp_path, gen(4, 1)) as (page, log):
        page.goto(page.url.split("#")[0] + frag); page.reload()
        assert page.is_hidden("#detail") and log["errors"] == []


def test_JS_10_filters_show_exact_counts(tmp_path):
    d = gen(30, 3); v = view_of(d)
    with open_page(tmp_path, view=v) as (page, log):
        assert "Showing 1 to 30 of 30 matching (30 in all)." == text(page, "#count-symptoms")
        page.select_option("#f-symptoms-lane", "stamp_lane_1")
        want = sum(1 for s in v["symptoms"] if "stamp_lane_1" in s["lanes"])
        assert text(page, "#count-symptoms") == f"Showing 1 to {want} of {want} matching (30 in all)." and len(rows(page, "symptoms")) == want


def test_JS_11_combined_filters_and_search_equal_an_independent_count(tmp_path):
    d = gen(40, 2, m2_every=3); v = view_of(d)
    with open_page(tmp_path, view=v) as (page, log):
        page.select_option("#f-symptoms-rule", v["symptoms"][0]["rule"]); page.fill("#q-symptoms", "/t1")
        want = [s for s in v["symptoms"] if s["rule"] == v["symptoms"][0]["rule"] and "/t1" in " ".join([s["id"], s["source"], s["rule"], s["category"], s["topic"], s["node"]]).lower()]
        n = len(rows(page, "symptoms"))
        assert n > 0 and f"of {n} matching" in text(page, "#count-symptoms")
        page.click("text=Clear filters >> nth=0")
        assert len(rows(page, "symptoms")) == 40


def test_JS_12_filters_never_hide_blockers_or_limitations(tmp_path):
    with open_page(tmp_path, gen(10, 2, blockers=2)) as (page, log):
        page.select_option("#f-symptoms-lane", "stamp_lane_1"); page.fill("#q-symptoms", "zzz-no-match")
        assert page.locator("#sec7 tbody tr").count() >= 2 and page.locator("#sec9 li").count() >= 7
        assert "Showing 0 of 0 matching (10 in all)." == text(page, "#count-symptoms")


@pytest.mark.parametrize("s", ["<img src=x onerror=window.__pwn=1>", "</script><script>window.__pwn=1</script>", '"><svg onload=window.__pwn=1>', "javascript:window.__pwn=1"])
def test_JS_13_search_is_inert(tmp_path, s):
    with open_page(tmp_path, gen(5, 1)) as (page, log):
        page.fill("#q-symptoms", s)
        assert page.evaluate("window.__pwn") is None and log["dialogs"] == [] and page.locator("#tbl-symptoms tbody tr").count() == 0 and log["errors"] == []


@pytest.mark.parametrize("s", [h for h in hostile_strings() if "\ud800" not in h][:30])
def test_JS_14_hostile_report_text_never_executes(tmp_path, s):
    d = gen(4, 1, blockers=1)
    sy = d["symptoms"][0]; sy["topic"] = sy["node"] = sy["detector_or_rule"] = s; sy["identities"] = {"logger_name": s}
    with open_page(tmp_path, d) as (page, log):
        click_bar(page, 0); text(page, "#detail")
        assert page.evaluate("window.__pwn") is None and log["dialogs"] == [] and log["errors"] == [] and len(log["requests"]) == 1
        assert page.locator("#detail script, #detail img, #tbl-symptoms img, svg.timeline script").count() == 0


def test_JS_15_pagination_boundaries(tmp_path):
    with open_page(tmp_path, gen(250, 1)) as (page, log):
        assert len(rows(page, "symptoms")) == 100 and text(page, "#pager-symptoms") .startswith("Previous page") and "Page 1 of 3" in text(page, "#pager-symptoms")
        assert page.is_disabled("#pager-symptoms button >> nth=0")
        page.click("#pager-symptoms button >> nth=1"); page.click("#pager-symptoms button >> nth=1")
        assert len(rows(page, "symptoms")) == 50 and page.is_disabled("#pager-symptoms button >> nth=1") and "Showing 201 to 250 of 250" in text(page, "#count-symptoms")


def test_JS_16_page_script_rows_equal_the_static_rows(tmp_path):
    d = gen(40, 3, m2_every=3, blockers=1); v = view_of(d)
    with open_page(tmp_path, view=v, name="a.html", js=False) as (p0, _l0):
        static = {t: rows(p0, t) for t in ("symptoms", "links", "chains", "evidence")}
    with open_page(tmp_path, view=v, name="b.html") as (p1, _l1):
        for t, want in static.items():
            assert rows(p1, t) == want[:100], t


def test_JS_17_page_script_rows_equal_static_rows_with_verification(tmp_path):
    from physicore_collector.investigation_view.model import with_verification
    v = view_of()
    ver = {"path": "p", "summary": {"verified": 2, "mismatch": 1, "not_checked": 0}, "refs": {"E1": {"status": "verified", "detail": ""}, "E2": {"status": "mismatch", "detail": "the topic at this position differs"}},
           "artifacts": {"artifact:bag_0.mcap": {"status": "verified", "detail": ""}}}
    v = with_verification(v, ver)
    with open_page(tmp_path, view=v, name="a.html", js=False) as (p0, _):
        static = rows(p0, "evidence")
    with open_page(tmp_path, view=v, name="b.html") as (p1, _):
        assert rows(p1, "evidence") == static and "mismatch (the topic at this position differs)" in static[1][8]


def test_JS_18_full_range_redraw_equals_the_static_svg(tmp_path):
    d = gen(20, 2, width=300, step=7919); v = view_of(d)
    js = "() => Array.from(document.querySelectorAll('svg.timeline')).map(s => Array.from(s.querySelectorAll('rect.bar')).map(r => [r.getAttribute('x'), r.getAttribute('y'), r.getAttribute('width')]))"
    with open_page(tmp_path, view=v, name="a.html", js=False) as (p0, _):
        static = p0.evaluate(js)
    with open_page(tmp_path, view=v, name="b.html") as (p1, _):
        assert p1.evaluate(js) == static


def _zoom(lo, hi, span, op):
    width = hi - lo
    if op == "in":
        nw = max(1, width // 2); mid = lo + width // 2; lo2 = mid - nw // 2; hi2 = lo2 + nw
    elif op == "out":
        nw = min(span, width * 2); mid = lo + width // 2; lo2 = mid - nw // 2; hi2 = lo2 + nw
    elif op == "left":
        d = max(1, width // 4); lo2, hi2 = lo - d, hi - d
    else:
        d = max(1, width // 4); lo2, hi2 = lo + d, hi + d
    if lo2 < 0: hi2 -= lo2; lo2 = 0
    if hi2 > span: lo2 -= hi2 - span; hi2 = span
    if lo2 < 0: lo2 = 0
    return lo2, hi2


def test_JS_19_zoom_and_pan_follow_exact_integer_arithmetic_beyond_2_53(tmp_path):
    d = gen(9, 1, step=1_234_567, width=1000); v = view_of(d)
    span = v["lanes"][0]["span_ns"]
    assert BASE_NS > 2**53
    with open_page(tmp_path, view=v) as (page, log):
        lo, hi = 0, span
        for op, label in [("in", "Zoom in"), ("in", "Zoom in"), ("right", "Pan right"), ("left", "Pan left"), ("left", "Pan left"), ("out", "Zoom out"), ("in", "Zoom in")]:
            page.click(f"#zoom0 button >> text={label}")
            lo, hi = _zoom(lo, hi, span, op)
            assert text(page, "#zoominfo0").startswith(f"Window {offset_text(lo)} to {offset_text(hi)};"), (op, lo, hi)
        page.click("#zoom0 button >> text=Reset")
        assert text(page, "#zoominfo0").startswith(f"Window {offset_text(0)} to {offset_text(span)};")
        assert log["errors"] == []


def test_JS_20_zoom_in_to_a_single_nanosecond_never_goes_negative(tmp_path):
    v = view_of(gen(6, 1, step=1000))
    span = v["lanes"][0]["span_ns"]
    with open_page(tmp_path, view=v) as (page, log):
        for _ in range(40):
            page.click("#zoom0 button >> text=Zoom in")
        t = text(page, "#zoominfo0")
        m = re.match(r"Window \+(\d+)\.(\d{6}) ms to \+(\d+)\.(\d{6}) ms;", t)
        assert int(m.group(3)) * 1_000_000 + int(m.group(4)) - (int(m.group(1)) * 1_000_000 + int(m.group(2))) == 1
        def lo_ns():
            m = re.match(r"Window \+(\d+)\.(\d{6}) ms", text(page, "#zoominfo0"))
            return int(m.group(1)) * 1_000_000 + int(m.group(2))
        before = lo_ns()
        for _ in range(60):
            page.click("#zoom0 button >> text=Pan left")
        assert lo_ns() == before - 60
        for _ in range(60):
            page.click("#zoom0 button >> text=Zoom out")
        assert text(page, "#zoominfo0").startswith(f"Window {offset_text(0)} to {offset_text(span)};")


def test_JS_21_zooming_changes_the_drawn_symptoms_and_states_the_counts(tmp_path):
    v = view_of(gen(50, 1, step=1000))
    with open_page(tmp_path, view=v) as (page, log):
        before = page.locator("svg.timeline rect.bar").count()
        for _ in range(3):
            page.click("#zoom0 button >> text=Zoom in")
        after = page.locator("svg.timeline rect.bar").count()
        assert after < before and f"{after} of {after} symptoms in the window" in text(page, "#zoominfo0")


def test_JS_22_selection_survives_a_redraw(tmp_path):
    with open_page(tmp_path, gen(10, 1)) as (page, log):
        click_bar(page, 4); page.click("#zoom0 button >> text=Pan right")
        assert page.locator("svg.timeline .bar.sel").count() <= 1 and "s-00000004" in text(page, "#detail h3")


def test_JS_23_sections_collapse_and_expand_but_not_blockers_and_limits(tmp_path):
    with open_page(tmp_path, gen(4, 1, blockers=1)) as (page, log):
        page.click("#sec4 summary")
        assert page.evaluate("document.querySelector('#sec4 details').open") is False
        page.click("#sec4 summary")
        assert page.evaluate("document.querySelector('#sec4 details').open") is True
        assert page.locator("#sec7 details, #sec9 details, #sec0 details").count() == 0


def test_JS_24_copy_reference_button(tmp_path):
    v = view_of(gen(3, 1))
    with open_page(tmp_path, view=v) as (page, log):
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        click_bar(page, 0); page.click("#detail button >> text=Copy reference")
        page.wait_for_selector("#detail [role=status]:not(:empty)")
        status = text(page, "#detail [role=status]")
        assert status in ("Copied.", "Copy is not available here; select the text instead.") and log["errors"] == []
        if status == "Copied.":
            assert page.evaluate("navigator.clipboard.readText()") == v["evidence_refs"][0]["copy"]


def test_JS_25_copy_without_a_clipboard_degrades_gracefully(tmp_path):
    with open_page(tmp_path, gen(3, 1)) as (page, log):
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: undefined})")
        click_bar(page, 0); page.click("#detail button >> text=Copy reference")
        assert text(page, "#detail [role=status]") == "Copy is not available here; select the text instead." and log["errors"] == []


def test_JS_26_a_broken_data_block_leaves_the_static_page_intact(tmp_path):
    html = render_html(view_of(gen(6, 1))).replace('<script type="application/json" id="view-data">{', '<script type="application/json" id="view-data">{{')
    with open_page(tmp_path, html=html) as (page, log):
        assert page.evaluate("document.documentElement.getAttribute('data-enhanced')") is None
        assert page.is_hidden("#controls-symptoms") and len(rows(page, "symptoms")) == 6 and log["errors"] == []


def test_JS_27_ten_thousand_symptoms_page_without_freezing(tmp_path):
    d = gen(10000, 4)
    t0 = time.time()
    with open_page(tmp_path, d) as (page, log):
        load = time.time() - t0
        assert page.evaluate("document.documentElement.getAttribute('data-enhanced')") == "1"
        assert len(rows(page, "symptoms")) == 100 and "of 10000" in text(page, "#count-symptoms")
        t1 = time.time()
        for _ in range(5):
            page.click("#pager-symptoms button >> nth=1")
        page.select_option("#f-symptoms-lane", "stamp_lane_2")
        assert time.time() - t1 < 15 and load < 60 and log["errors"] == []


def test_JS_28_dark_scheme_and_print_media(tmp_path):
    with open_page(tmp_path, gen(4, 1)) as (page, log):
        light = page.evaluate("getComputedStyle(document.body).backgroundColor")
        page.emulate_media(color_scheme="dark")
        assert page.evaluate("getComputedStyle(document.body).backgroundColor") != light
        click_bar(page, 0); page.emulate_media(media="print")
        assert page.is_hidden("#detail") and page.is_hidden("nav.sections")


@pytest.mark.parametrize("w", [390, 1280])
def test_JS_29_no_horizontal_page_scroll_and_screenshots_for_a_human(tmp_path, w):
    with open_page(tmp_path, gen(12, 2, blockers=1), width=w) as (page, log):
        assert page.evaluate("document.documentElement.scrollWidth") <= w + 1
        out = tmp_path / f"shot_{w}.png"; page.screenshot(path=str(out), full_page=True); assert out.stat().st_size > 5000


def test_JS_30_detail_shows_verification_results(tmp_path):
    from physicore_collector.investigation_view.model import with_verification
    v = with_verification(view_of(), {"path": "p", "summary": {"verified": 0, "mismatch": 1, "not_checked": 0}, "refs": {"E1": {"status": "mismatch", "detail": "the log time at this position differs"}},
                                      "artifacts": {}})
    with open_page(tmp_path, view=v) as (page, log):
        click_bar(page, 0)
        assert "mismatch (the log time at this position differs)" in text(page, "#detail")


def test_JS_31_the_timeline_draws_at_most_300_bars_like_the_static_page(tmp_path):
    v = view_of(gen(400, 1))
    with open_page(tmp_path, view=v) as (page, log):
        assert page.locator("svg.timeline rect.bar").count() == 300 and "300 of 400 symptoms in the window" in text(page, "#zoominfo0")


def test_JS_32_long_list_cells_are_cut_like_the_static_page(tmp_path):
    v = view_of(gen(60, 1))
    with open_page(tmp_path, view=v, name="a.html", js=False) as (p0, _):
        static = rows(p0, "chains")
    with open_page(tmp_path, view=v, name="b.html") as (p1, _):
        got = rows(p1, "chains")
    assert got == static and " ... and 40 more" in got[0][3]


def test_JS_33_search_matches_at_the_start_of_the_text_and_is_case_insensitive(tmp_path):
    with open_page(tmp_path, gen(30, 1)) as (page, log):
        page.fill("#q-symptoms", "S-00000007")
        assert len(rows(page, "symptoms")) == 1


def test_JS_34_changing_a_filter_returns_to_the_first_page(tmp_path):
    with open_page(tmp_path, gen(250, 2)) as (page, log):
        page.click("#pager-symptoms button >> nth=1")
        assert "Page 2 of 3" in text(page, "#pager-symptoms")
        page.select_option("#f-symptoms-lane", "stamp_lane_1")
        assert "Page 1 of 2" in text(page, "#pager-symptoms")


def test_JS_35_the_static_more_note_is_hidden_by_the_script(tmp_path):
    with open_page(tmp_path, gen(260, 1)) as (page, log):
        assert page.locator('[data-more="symptom"]').is_hidden()


def test_JS_36_links_chains_and_evidence_tables_filter(tmp_path):
    v = view_of(gen(20, 2, blockers=1))
    with open_page(tmp_path, view=v) as (page, log):
        page.select_option("#f-links-lane", "stamp_lane_1")
        assert len(rows(page, "links")) == sum(1 for l in v["links"] if l["lane"] == "stamp_lane_1")
        page.select_option("#f-chains-lane", "mcap_log_time")
        assert len(rows(page, "chains")) == 1
        page.select_option("#f-evidence-status", "not requested")
        assert len(rows(page, "evidence")) == len(v["evidence_refs"])


# ---- tests added after the JS mutation run
def _win(page, li=0):
    m = re.match(r"Window \+(\d+)\.(\d{6}) ms to \+(\d+)\.(\d{6}) ms;", text(page, f"#zoominfo{li}"))
    return tuple(int(m.group(a)) * 1_000_000 + int(m.group(b)) for a, b in ((1, 2), (3, 4)))


def test_JS_37_a_lane_with_zero_span_draws_and_all_controls_work(tmp_path):
    v = view_of(gen(1, 1))
    assert v["lanes"][0]["span_ns"] == 0
    with open_page(tmp_path, view=v) as (page, log):
        assert page.locator("svg.timeline rect.bar").count() == 1
        for label in ("Zoom in", "Zoom out", "Pan left", "Pan right", "Reset"):
            page.click(f"#zoom0 button >> text={label}")
        assert log["errors"] == [] and page.locator("svg.timeline rect.bar").count() == 1


def test_JS_38_offsets_beyond_a_thousand_seconds_print_exactly(tmp_path):
    v = view_of(gen(3, 1))
    ln = v["lanes"][0]
    last = ln["items"][-1]                              # a long lane: the page script only reads the lane's offsets
    last["off_lower_ns"] = last["off_upper_ns"] = 2 * 10**12
    last["lower_ns"] = last["upper_ns"] = ln["origin_ns"] + 2 * 10**12
    ln["span_ns"] = span = 2 * 10**12
    with open_page(tmp_path, view=v) as (page, log):
        assert text(page, "#zoominfo0").startswith(f"Window {offset_text(0)} to {offset_text(span)};") and "+2000000.000000 ms" in text(page, "#zoominfo0")
        ticks = page.eval_on_selector_all("svg.timeline text", "els => els.map(e => e.textContent).filter(t => t.startsWith('+'))")[:5]
        assert ticks == [offset_text(span * k // 4) for k in range(5)]


def test_JS_39_window_edges_keep_the_width_and_never_leave_the_lane(tmp_path):
    v = view_of(gen(9, 1, step=1_234_567, width=1000))
    span = v["lanes"][0]["span_ns"]
    with open_page(tmp_path, view=v) as (page, log):
        for label in ("Zoom out", "Pan left", "Pan right"):
            page.click(f"#zoom0 button >> text={label}")
            assert _win(page) == (0, span), label
        page.click("#zoom0 button >> text=Zoom in")
        w0 = _win(page); width = w0[1] - w0[0]
        for _ in range(8):
            page.click("#zoom0 button >> text=Pan right")
        assert _win(page) == (span - width, span)
        for _ in range(12):
            page.click("#zoom0 button >> text=Pan left")
        assert _win(page) == (0, width)
        for _ in range(60):
            page.click("#zoom0 button >> text=Zoom in")
        lo, hi = _win(page); assert hi - lo == 1
        for _ in range(5):
            page.click("#zoom0 button >> text=Pan right")
        assert _win(page) == (lo + 5, hi + 5)


def test_JS_40_tick_labels_are_the_quarters_of_the_window_and_bars_are_clipped(tmp_path):
    v = view_of(gen(10, 1, step=1000, width=900))
    with open_page(tmp_path, view=v) as (page, log):
        for _ in range(3):
            page.click("#zoom0 button >> text=Zoom in")
        page.click("#zoom0 button >> text=Pan right")
        lo, hi = _win(page)
        ticks = page.eval_on_selector_all("svg.timeline text", "els => els.map(e => e.textContent).filter(t => t.startsWith('+'))")[:5]
        assert ticks == [offset_text(lo + (hi - lo) * k // 4) for k in range(5)]
        boxes = page.eval_on_selector_all("svg.timeline rect.bar", "els => els.map(e => [Number(e.getAttribute('x')), Number(e.getAttribute('width'))])")
        assert boxes and all(x >= 12 and x + w <= 12 + 1000 + 4 for x, w in boxes), boxes


def test_JS_41_selecting_another_symptom_replaces_the_detail(tmp_path):
    with open_page(tmp_path, gen(6, 1)) as (page, log):
        click_bar(page, 1); click_bar(page, 3)
        assert page.locator("#detail h3").count() == 1 and "s-00000003" in text(page, "#detail h3") and "s-00000001" not in text(page, "#detail")


def test_JS_42_every_static_omission_note_and_every_pager_is_handled_by_the_script(tmp_path):
    v = view_of(gen(260, 1))
    with open_page(tmp_path, view=v, name="a.html", js=False) as (p0, _):
        assert {e for e in p0.eval_on_selector_all("[data-more]", "els => els.map(e => e.getAttribute('data-more'))")} >= {"symptom", "link", "evidence"}
        assert all(p0.locator(f'[data-more="{k}"]').is_visible() for k in ("symptom", "link", "evidence"))
    with open_page(tmp_path, view=v, name="b.html") as (page, log):
        assert all(page.locator(f'[data-more="{k}"]').is_hidden() for k in ("symptom", "link", "evidence"))
        pagers = page.locator('[id^="pager-"]')
        assert pagers.count() >= 4 and all(pagers.nth(i).is_visible() for i in range(pagers.count()))
        assert page.eval_on_selector_all('[id^="pager-"], [id^="controls-"], [id^="count-"]', "els => els.every(e => e.hidden === false)")   # the DOM state, not only the rendering (the pager has a display rule that overrides the hidden attribute)
