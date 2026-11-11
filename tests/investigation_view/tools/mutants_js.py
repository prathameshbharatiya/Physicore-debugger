"""Mutants of the page script (js_source.py), killed through the Chromium tests. Justified equivalent (1): "js: zoom out clamp" - without `if (nw > span) { nw = span; }` the following window clamps (low clamp, high clamp and the final lower bound) produce exactly [0, span] for every input, so the displayed window is identical; the first re-run killed all other survivors with tests JS_37-42, and "js: controls stay hidden" was verified killed by hand on a mutated copy after JS_42 was strengthened."""
from tests.localization.tools.mutate import Mutant

F = "physicore_collector/investigation_view/js_source.py"
T = ("tests/investigation_view/test_js.py",)


def J(name, old, new):
    return Mutant("js: " + name, F, old, new, T)


MUTANTS = [
    J("page size", "var PAGE = 100,", "var PAGE = 50,"),
    J("bar cap constant", "MAXBARS = 300, SVGNS", "MAXBARS = 301, SVGNS"),
    J("bar cap slice", "items = items.slice(0, MAXBARS);", "items = items.slice(0, MAXBARS + 1);"),
    J("x position rounding", "BigInt(width) * 2n + span) / (span * 2n);", "BigInt(width) * 2n) / (span * 2n);"),
    J("x position zero span", "if (span <= 0n) { return 0; }", "if (span < 0n) { return 0; }"),
    J("offset decimals", 'padStart(6, "0")', 'padStart(5, "0")'),
    J("offset divisor", "var ms = b / 1000000n", "var ms = b / 1000001n"),
    J("zoom in factor", "nw = width / 2n < 1n ? 1n : width / 2n, mid", "nw = width / 4n < 1n ? 1n : width / 4n, mid"),
    J("zoom in minimum", "nw = width / 2n < 1n ? 1n : width / 2n, mid", "nw = width / 2n < 1n ? 0n : width / 2n, mid"),
    J("zoom out factor", "nw = width * 2n; if (nw > span)", "nw = width * 3n; if (nw > span)"),
    J("zoom out clamp", "if (nw > span) { nw = span; }", ""),
    J("pan left direction", "set(w[0] - d, w[1] - d)", "set(w[0] + d, w[1] + d)"),
    J("pan right direction", "set(w[0] + d, w[1] + d)", "set(w[0] - d, w[1] - d)"),
    J("pan step", "var w = win(), d = (w[1] - w[0]) / 4n; if (d < 1n) { d = 1n; } set(w[0] - d", "var w = win(), d = (w[1] - w[0]) / 8n; if (d < 1n) { d = 1n; } set(w[0] - d"),
    J("pan minimum step", "if (d < 1n) { d = 1n; } set(w[0] + d", "if (d < 0n) { d = 1n; } set(w[0] + d"),
    J("window low clamp", "if (lo < 0n) { hi -= lo; lo = 0n; }", "if (lo < 0n) { lo = 0n; }"),
    J("window high clamp", "if (hi > span) { lo -= hi - span; hi = span; }", "if (hi > span) { hi = span; }"),
    J("reset keeps window", 'btn("Reset", function () { delete windows[li]; drawLane(li); });', 'btn("Reset", function () { drawLane(li); });'),
    J("previous never disabled", "prev.disabled = st.page === 0;", "prev.disabled = false;"),
    J("next never disabled", "next.disabled = st.page >= pages - 1;", "next.disabled = false;"),
    J("filter keeps page", 'st.filters[sp[0]] = sel.value; st.page = 0; render(st);', 'st.filters[sp[0]] = sel.value; render(st);'),
    J("search skips first column", "row.text.indexOf(st.query) >= 0", "row.text.indexOf(st.query) > 0"),
    J("filter inverted", "if (specs[k][2](d).indexOf(want) < 0) { return false; }", "if (specs[k][2](d).indexOf(want) >= 0) { return false; }"),
    J("filter off by one", "if (specs[k][2](d).indexOf(want) < 0) { return false; }", "if (specs[k][2](d).indexOf(want) < -1) { return false; }"),
    J("list cap", "var LIST_CAP = 20;", "var LIST_CAP = 21;"),
    J("selected bar class", '\n    r.classList.toggle("sel", i === selected);', '\n    r.classList.toggle("sel", i !== selected);'),
    J("highlight includes selected", '\n    r.classList.toggle("hl", i !== selected && !!symIdx[i]);', '\n    r.classList.toggle("hl", !!symIdx[i]);'),
    J("arrows never highlighted", 'a.classList.toggle("hl", !!linkIdx[Number(a.getAttribute("data-link"))]);', 'a.classList.toggle("hl", false);'),
    J("row highlight dropped", 'tr.classList.toggle("hl", i !== selected && !!symIdx[i]);', 'tr.classList.toggle("hl", false);'),
    J("hash not written", "if (!noHash) {", "if (false) {"),
    J("hash bound", "if (m && Number(m[1]) < V.symptoms.length)", "if (m)"),
    J("hash pattern", "/^#s(\\d+)$/", "/^#s(\\d*)$/"),
    J("close does not hide", "if (i < 0) { box.hidden = true; return; }", "if (i < 0) { return; }"),
    J("copy status text", 'ok ? "Copied." :', 'ok ? "Done." :'),
    J("more note stays", "if (more) { more.hidden = true; }", ""),
    J("tick divisor", "var off = wlo + wspan * BigInt(k) / 4n", "var off = wlo + wspan * BigInt(k) / 5n"),
    J("bar left clip", "xpos(lo < wlo ? wlo : lo, wlo, wspan, W)", "xpos(lo, wlo, wspan, W)"),
    J("bar right clip", "xpos(hi > whi ? whi : hi, wlo, wspan, W)", "xpos(hi, wlo, wspan, W)"),
    J("window overlap test", "BigInt(it.off_upper_ns) >= wlo && BigInt(it.off_lower_ns) <= whi", "BigInt(it.off_upper_ns) > wlo && BigInt(it.off_lower_ns) < whi"),
    J("detail clears", "clear(box);\n  if (i < 0)", "if (i < 0)"),
    J("evidence status in detail", 'return r === null || r === undefined ? "not requested" : (r.detail ? r.status + " (" + r.detail + ")" : r.status);', 'return r === null || r === undefined ? "not requested" : r.status;'),
    J("symptom cells identities", 'jn(s.identities.map(function (p) { return p[0] + "=" + p[1]; }), "; ")', 'jn(s.identities.map(function (p) { return p[0]; }), "; ")'),
    J("link cells window", 'l.window_ns === null ? "none declared" : l.window_ns + " ns"', 'l.window_ns === null ? "none declared" : l.window_ns'),
    J("evidence time cell", 'r.time_ns === null ? "none" : r.time_ns + " ns"', 'r.time_ns === null ? "none" : r.time_ns'),
    J("static more selector", '(st.name === "symptoms" ? "symptom" : st.name === "evidence" ? "evidence" : st.name === "links" ? "link" : "chain")', '"symptom"'),
    J("zoom info counts", 'Math.min(total, MAXBARS) + " of " + total', 'total + " of " + total'),
    J("no hashchange listener", 'window.addEventListener("hashchange", fromHash);', ""),
    J("enhanced marker", 'document.documentElement.setAttribute("data-enhanced", "1");', ""),
    J("controls stay hidden", "controls.hidden = false; counter.hidden = false; pager.hidden = false;", "controls.hidden = false; counter.hidden = false;"),
]
