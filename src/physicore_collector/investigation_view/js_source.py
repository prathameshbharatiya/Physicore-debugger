"""The JavaScript of the investigation page (FR-JS-*): progressive enhancement of the static page. No library, no network, no storage, no innerHTML,
no eval; all text is inserted with textContent; every nanosecond value is a BigInt made from a string. A failure leaves the static page intact."""

JS = r"""
(function () {
"use strict";
var PAGE = 100, W = 1000, LEFT = 12, TOP = 40, ROWH = 26, BARH = 14, MAXBARS = 300, SVGNS = "http://www.w3.org/2000/svg";
var V;
try { V = JSON.parse(document.getElementById("view-data").textContent); } catch (e) { return; }

function el(tag, attrs, text) {
  var n = document.createElement(tag);
  if (attrs) { for (var k in attrs) { n.setAttribute(k, attrs[k]); } }
  if (text !== undefined && text !== null) { n.textContent = text; }
  return n;
}
function sv(tag, attrs, text) {
  var n = document.createElementNS(SVGNS, tag);
  if (attrs) { for (var k in attrs) { n.setAttribute(k, String(attrs[k])); } }
  if (text !== undefined && text !== null) { n.textContent = text; }
  return n;
}
function clear(n) { while (n.firstChild) { n.removeChild(n.firstChild); } }
function none(s) { return s === "" || s === null || s === undefined ? "none" : s; }
var LIST_CAP = 20;
function jnall(a, sep) { return a.length ? a.join(sep || ", ") : "none"; }
function jn(a, sep) { sep = sep || ", "; if (!a.length) { return "none"; } return a.length <= LIST_CAP ? a.join(sep) : a.slice(0, LIST_CAP).join(sep) + " ... and " + (a.length - LIST_CAP) + " more"; }
function ivtext(lo, hi) { return lo === hi ? lo + " ns" : lo + " to " + hi + " ns"; }
function offtext(b) {
  var ms = b / 1000000n, rem = b % 1000000n;
  return "+" + ms.toString() + "." + rem.toString().padStart(6, "0") + " ms";
}
function xpos(t, origin, span, width) {                       // integer pixel position, identical to fmt.x_pos of the static page
  if (span <= 0n) { return 0; }
  var q = ((t - origin) * BigInt(width) * 2n + span) / (span * 2n);
  return Number(q);                                           // a pixel position (at most W), never a time
}

var symIndex = {}, linkIndex = {}, chainById = {}, symById = {};
V.symptoms.forEach(function (s, i) { symIndex[s.id] = i; symById[s.id] = s; });
V.links.forEach(function (l, i) { linkIndex[l.id] = i; });
V.chains.forEach(function (c) { chainById[c.id] = c; });

/* ------------------------------------------------------------------ selection */
var selected = -1;
function related(s) {
  var syms = {}, links = {};
  s.chains.forEach(function (cid) {
    var c = chainById[cid]; if (!c) { return; }
    c.symptom_ids.forEach(function (x) { syms[x] = true; });
    c.steps.forEach(function (st) { st.link_ids.forEach(function (x) { links[x] = true; }); });
  });
  s.links.forEach(function (x) { links[x] = true; });
  return { syms: syms, links: links };
}
function applyHighlight() {
  var rel = selected >= 0 ? related(V.symptoms[selected]) : { syms: {}, links: {} };
  var symIdx = {}, linkIdx = {};
  Object.keys(rel.syms).forEach(function (id) { symIdx[symIndex[id]] = true; });
  Object.keys(rel.links).forEach(function (id) { linkIdx[linkIndex[id]] = true; });
  Array.prototype.forEach.call(document.querySelectorAll("svg.timeline .bar"), function (r) {
    var i = Number(r.getAttribute("data-sym"));
    r.classList.toggle("sel", i === selected);
    r.classList.toggle("hl", i !== selected && !!symIdx[i]);
  });
  Array.prototype.forEach.call(document.querySelectorAll("svg.timeline .arrow"), function (a) {
    a.classList.toggle("hl", !!linkIdx[Number(a.getAttribute("data-link"))]);
  });
  Array.prototype.forEach.call(document.querySelectorAll("#tbl-symptoms tbody tr"), function (tr) {
    var i = Number(tr.getAttribute("data-i"));
    tr.classList.toggle("sel", i === selected);
    tr.classList.toggle("hl", i !== selected && !!symIdx[i]);
  });
}
function dl(parent, rows) {
  var d = el("dl");
  rows.forEach(function (r) { d.appendChild(el("dt", null, r[0])); var dd = el("dd"); if (typeof r[1] === "string") { dd.textContent = r[1]; } else { dd.appendChild(r[1]); } d.appendChild(dd); });
  parent.appendChild(d);
}
var statusEl = null;
function copyText(text) {
  function done(ok) { if (statusEl) { statusEl.textContent = ok ? "Copied." : "Copy is not available here; select the text instead."; } }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); }); return; }
  } catch (e) { /* fall through */ }
  done(false);
}
function evRow(id) {
  for (var i = 0; i < V.evidence_refs.length; i++) { if (V.evidence_refs[i].id === id) { return V.evidence_refs[i]; } }
  return null;
}
function vtext(r) { return r === null || r === undefined ? "not requested" : (r.detail ? r.status + " (" + r.detail + ")" : r.status); }
function showDetail(i) {
  var box = document.getElementById("detail"); if (!box) { return; }
  clear(box);
  if (i < 0) { box.hidden = true; return; }
  var s = V.symptoms[i];
  box.hidden = false;
  box.appendChild(el("h3", null, "Symptom " + s.id));
  var close = el("button", { type: "button" }, "Close"); close.addEventListener("click", function () { select(-1); }); box.appendChild(close);
  var ons = [];
  V.lanes.forEach(function (ln) {
    ln.items.forEach(function (it) { if (it.id === s.id && it.lower_ns !== null) { ons.push("Lane " + ln.domain + ": " + ivtext(it.lower_ns, it.upper_ns) + ", offset " + offtext(BigInt(it.off_lower_ns)) + ", layer " + it.layer); } });
  });
  var cov = s.coverage;
  var rows = [
    ["Source", s.source + " " + s.rule + " (" + s.source_id + ")"], ["Category", s.category], ["Topic", s.topic], ["Node", s.node],
    ["Identities", s.identities.length ? s.identities.map(function (p) { return p[0] + "=" + p[1]; }).join("; ") : "none"],
    ["Severity as reported", s.severity + " (basis " + s.severity_basis + ")"], ["Onset basis", s.onset_basis],
    ["Onset interval", ivtext(s.onset.lower_ns, s.onset.upper_ns) + " in " + s.onset.domain + ", width " + s.onset.width_ns + " ns"],
    ["Placement per lane", jnall(ons, "; ")],
    ["Source coverage", cov.topic_coverage + "; messages examined " + cov.examined + "; undecodable " + cov.undecodable + "; reasons " + jnall(cov.reasons)],
    ["Uncertainty and notes", jnall(s.uncertainty.concat(s.notes), "; ")], ["Truncated", s.truncated ? "yes" : "no"],
    ["Chains", jnall(s.chains)], ["Links", s.links.length ? s.links.map(function (id) { var l = V.links[linkIndex[id]]; return id + " (" + l.label + ")"; }).join(", ") : "none"]
  ];
  dl(box, rows);
  box.appendChild(el("h4", null, "Evidence references"));
  var ul = el("ul");
  s.evidence.forEach(function (eid) {
    var r = evRow(eid); if (!r) { return; }
    var li = el("li");
    li.appendChild(el("span", { "class": "mono" }, r.id + " " + r.artifact_ref + " message " + r.message_index + " on " + none(r.topic) + ", " + (r.time_ns === null ? "no time" : r.time_ns + " ns") + " (" + none(r.clock_domain) + "), SHA-256 " + none(r.sha256) + "; verification: " + vtext(r.verification) + " "));
    var b = el("button", { type: "button" }, "Copy reference"); b.addEventListener("click", function () { copyText(r.copy); }); li.appendChild(b);
    ul.appendChild(li);
  });
  if (!s.evidence.length) { ul.appendChild(el("li", null, "none")); }
  box.appendChild(ul);
  statusEl = el("p", { "class": "note", role: "status" }); box.appendChild(statusEl);
}
function select(i, noHash) {
  selected = i; showDetail(i); applyHighlight();
  if (!noHash) { try { if (i >= 0) { history.replaceState(null, "", "#s" + i); } else { history.replaceState(null, "", location.pathname + location.search); } } catch (e) { /* file: URLs may refuse */ } }
}
function fromHash() {
  var m = /^#s(\d+)$/.exec(location.hash || "");
  if (m && Number(m[1]) < V.symptoms.length) { select(Number(m[1]), true); }
}
window.addEventListener("hashchange", fromHash);

/* ------------------------------------------------------------------ managed tables */
var CELLS = {
  symptoms: function (s) { return [s.id, s.source, s.rule, s.category, s.topic, s.node, ivtext(s.onset.lower_ns, s.onset.upper_ns), s.onset_basis, s.severity, s.coverage.topic_coverage,
    s.truncated ? "yes" : "no", jn(s.identities.map(function (p) { return p[0] + "=" + p[1]; }), "; "), jn(s.notes.concat(s.uncertainty), "; "), jn(s.evidence)]; },
  links: function (l) { return [l.id, l.lane, l.from_symptom + " on " + l.from_node, l.to_symptom + " on " + l.to_node, jn(l.edge_ids), l.label,
    l.sep_min_ns + " ns to " + l.sep_max_ns + " ns", l.window_ns === null ? "none declared" : l.window_ns + " ns", jn(l.notes)]; },
  chains: function (c) { return [c.id, c.lane, c.head, jn(c.symptom_ids, " then "), jn(c.nodes, " then "), c.defence, jn(c.blocker_ids)]; },
  evidence: function (r) { return [r.id, r.artifact_ref, String(r.message_index), none(r.topic), r.time_ns === null ? "none" : r.time_ns + " ns", none(r.clock_domain), none(r.sha256),
    r.cited_by.map(function (k) { return k.kind + " " + k.id; }).join(", "), vtext(r.verification)]; }
};
var DATA = { symptoms: V.symptoms, links: V.links, chains: V.chains, evidence: V.evidence_refs };
var FILTERS = {
  symptoms: [["lane", "Lane", function (s) { return s.lanes; }], ["rule", "Detector or rule", function (s) { return [s.rule]; }], ["category", "Category", function (s) { return [s.category]; }],
             ["severity", "Severity as reported", function (s) { return [s.severity]; }]],
  links: [["label", "Link label", function (l) { return [l.label]; }], ["lane", "Lane", function (l) { return [l.lane]; }]],
  chains: [["defence", "Defence", function (c) { return [c.defence]; }], ["lane", "Lane", function (c) { return [c.lane]; }]],
  evidence: [["status", "Verification", function (r) { return [r.verification ? r.verification.status : "not requested"]; }]]
};
var tables = {};
function setupTable(name) {
  var tbl = document.getElementById("tbl-" + name); if (!tbl) { return; }
  var controls = document.getElementById("controls-" + name), counter = document.getElementById("count-" + name), pager = document.getElementById("pager-" + name);
  var st = { name: name, tbl: tbl, tbody: tbl.tBodies[0], rows: DATA[name].map(function (d, i) { var cells = CELLS[name](d); return { i: i, cells: cells, text: cells.join(" ").toLowerCase() }; }),
             filters: {}, query: "", page: 0, controls: controls, counter: counter, pager: pager };
  tables[name] = st;
  var specs = FILTERS[name];
  specs.forEach(function (sp) {
    var vals = {}; DATA[name].forEach(function (d) { sp[2](d).forEach(function (x) { vals[x] = true; }); });
    var sel = el("select", { id: "f-" + name + "-" + sp[0] }); sel.appendChild(el("option", { value: "" }, "All"));
    Object.keys(vals).sort().forEach(function (x) { sel.appendChild(el("option", { value: x }, x)); });
    sel.addEventListener("change", function () { st.filters[sp[0]] = sel.value; st.page = 0; render(st); });
    var lab = el("label", null, sp[1]); lab.appendChild(sel); controls.appendChild(lab);
  });
  var q = el("input", { type: "search", id: "q-" + name, autocomplete: "off" });
  q.addEventListener("input", function () { st.query = q.value.toLowerCase(); st.page = 0; render(st); });
  var ql = el("label", null, "Search"); ql.appendChild(q); controls.appendChild(ql);
  var reset = el("button", { type: "button" }, "Clear filters");
  reset.addEventListener("click", function () { st.filters = {}; st.query = ""; q.value = ""; specs.forEach(function (sp) { document.getElementById("f-" + name + "-" + sp[0]).value = ""; }); st.page = 0; render(st); });
  controls.appendChild(reset);
  controls.hidden = false; counter.hidden = false; pager.hidden = false;
  tbl.addEventListener("click", function (ev) {
    var tr = ev.target.closest ? ev.target.closest("tr") : null; if (!tr || name !== "symptoms") { return; }
    var i = tr.getAttribute("data-i"); if (i !== null) { select(Number(i)); }
  });
  render(st);
}
function matches(st, row) {
  var specs = FILTERS[st.name], d = DATA[st.name][row.i];
  for (var k = 0; k < specs.length; k++) {
    var want = st.filters[specs[k][0]]; if (want) { if (specs[k][2](d).indexOf(want) < 0) { return false; } }
  }
  return !st.query || row.text.indexOf(st.query) >= 0;
}
function render(st) {
  var list = st.rows.filter(function (r) { return matches(st, r); });
  var pages = Math.max(1, Math.ceil(list.length / PAGE));
  if (st.page >= pages) { st.page = pages - 1; }
  var from = st.page * PAGE, to = Math.min(list.length, from + PAGE);
  clear(st.tbody);
  var prefix = { symptoms: "s", links: "l", chains: "c", evidence: "E" }[st.name];
  for (var k = from; k < to; k++) {
    var r = list[k], tr = el("tr", { id: prefix + r.i, "data-i": String(r.i) });
    r.cells.forEach(function (c) { tr.appendChild(el("td", null, c === "" ? "none" : c)); });
    st.tbody.appendChild(tr);
  }
  var total = st.rows.length;
  st.counter.textContent = list.length ? "Showing " + (from + 1) + " to " + to + " of " + list.length + " matching (" + total + " in all)." : "Showing 0 of 0 matching (" + total + " in all).";
  clear(st.pager);
  var prev = el("button", { type: "button" }, "Previous page"), next = el("button", { type: "button" }, "Next page");
  prev.disabled = st.page === 0; next.disabled = st.page >= pages - 1;
  prev.addEventListener("click", function () { st.page--; render(st); }); next.addEventListener("click", function () { st.page++; render(st); });
  st.pager.appendChild(prev); st.pager.appendChild(el("span", null, "Page " + (st.page + 1) + " of " + pages)); st.pager.appendChild(next);
  var more = document.querySelector('[data-more="' + (st.name === "symptoms" ? "symptom" : st.name === "evidence" ? "evidence" : st.name === "links" ? "link" : "chain") + '"]');
  if (more) { more.hidden = true; }
  applyHighlight();
}

/* ------------------------------------------------------------------ timeline zoom and pan (BigInt) */
var windows = {};
function drawLane(li) {
  var ln = V.lanes[li], svg = document.getElementById("tl" + li); if (!svg || ln.origin_ns === null) { return; }
  var span = BigInt(ln.span_ns), win = windows[li] || [0n, span], wlo = win[0], whi = win[1], wspan = whi - wlo;
  var items = ln.items.filter(function (it) { return it.lower_ns !== null && BigInt(it.off_upper_ns) >= wlo && BigInt(it.off_lower_ns) <= whi; });
  var total = items.length; items = items.slice(0, MAXBARS);
  var layers = {}; items.forEach(function (it) { layers[it.layer] = true; });
  var order = Object.keys(layers).map(Number).sort(function (a, b) { return a - b; }), rank = {}; order.forEach(function (l, r) { rank[l] = r; });
  var height = TOP + Math.max(1, order.length) * ROWH + 14;
  clear(svg); svg.setAttribute("viewBox", "0 0 " + (W + 2 * LEFT) + " " + height);
  svg.appendChild(sv("title", { id: "tl" + li + "t" }, "Timeline of clock domain " + ln.domain));
  svg.appendChild(sv("line", { "class": "axis", x1: LEFT, y1: TOP - 12, x2: LEFT + W, y2: TOP - 12 }));
  for (var k = 0; k < 5; k++) {
    var off = wlo + wspan * BigInt(k) / 4n, x = LEFT + xpos(off, wlo, wspan, W);
    svg.appendChild(sv("line", { "class": "tick", x1: x, y1: TOP - 16, x2: x, y2: TOP - 8 }));
    svg.appendChild(sv("text", { x: x, y: TOP - 20, "text-anchor": k === 0 ? "start" : (k === 4 ? "end" : "middle") }, offtext(off)));
  }
  var pos = {};
  items.forEach(function (it) {
    var lo = BigInt(it.off_lower_ns), hi = BigInt(it.off_upper_ns);
    var x1 = LEFT + xpos(lo < wlo ? wlo : lo, wlo, wspan, W), x2 = LEFT + xpos(hi > whi ? whi : hi, wlo, wspan, W), w = Math.max(x2 - x1, 4), y = TOP + rank[it.layer] * ROWH;
    pos[it.id] = [x1, x1 + w, y + Math.floor(BARH / 2)];
    var s = symById[it.id], i = symIndex[it.id];
    var rect = sv("rect", { "class": "bar", "data-sym": i === undefined ? -1 : i, x: x1, y: y, width: w, height: BARH });
    rect.appendChild(sv("title", null, it.id + ": " + ivtext(it.lower_ns, it.upper_ns) + (s ? " on " + s.topic : "")));
    if (i !== undefined) { var a = sv("a", { href: "#s" + i }); a.appendChild(rect); svg.appendChild(a); } else { svg.appendChild(rect); }
    if (items.length <= 60 && s) {
      var lab = s.topic.length <= 28 ? s.topic : s.topic.slice(0, 27) + "…", lx = x1 + w + 4 + 7 * lab.length < LEFT + W ? x1 + w + 4 : x1 - 4;
      svg.appendChild(sv("text", { x: lx, y: y + BARH - 3, "text-anchor": lx > x1 ? "start" : "end" }, lab));
    }
  });
  var drawn = 0;
  V.links.forEach(function (l) {
    if (l.lane !== ln.domain || !pos[l.from_symptom] || !pos[l.to_symptom] || drawn >= MAXBARS) { return; }
    var a = pos[l.from_symptom], b = pos[l.to_symptom], cls = ["consistent_with_propagation", "outside_declared_window", "inconsistent_order", "undetermined"].indexOf(l.label) >= 0 ? l.label : "other";
    var p = sv("path", { "class": "arrow l-" + cls, "data-link": linkIndex[l.id], d: "M" + a[1] + "," + a[2] + " L" + Math.max(b[0] - 1, a[1] + 1) + "," + b[2] });
    p.appendChild(sv("title", null, l.label + " along " + l.edge_ids.join(", "))); svg.appendChild(p); drawn++;
  });
  var info = document.getElementById("zoominfo" + li);
  if (info) { info.textContent = "Window " + offtext(wlo) + " to " + offtext(whi) + "; " + Math.min(total, MAXBARS) + " of " + total + " symptoms in the window are drawn."; }
  applyHighlight();
}
function setupZoom(li) {
  var box = document.getElementById("zoom" + li), ln = V.lanes[li]; if (!box || ln.origin_ns === null) { return; }
  var span = BigInt(ln.span_ns);
  function win() { return windows[li] || [0n, span]; }
  function set(lo, hi) { if (lo < 0n) { hi -= lo; lo = 0n; } if (hi > span) { lo -= hi - span; hi = span; } if (lo < 0n) { lo = 0n; } windows[li] = [lo, hi]; drawLane(li); }
  function btn(label, fn) { var b = el("button", { type: "button" }, label); b.addEventListener("click", fn); box.appendChild(b); return b; }
  btn("Zoom in", function () { var w = win(), width = w[1] - w[0], nw = width / 2n < 1n ? 1n : width / 2n, mid = w[0] + width / 2n; set(mid - nw / 2n, mid - nw / 2n + nw); });
  btn("Zoom out", function () { var w = win(), width = w[1] - w[0], mid = w[0] + width / 2n, nw = width * 2n; if (nw > span) { nw = span; } set(mid - nw / 2n, mid - nw / 2n + nw); });
  btn("Pan left", function () { var w = win(), d = (w[1] - w[0]) / 4n; if (d < 1n) { d = 1n; } set(w[0] - d, w[1] - d); });
  btn("Pan right", function () { var w = win(), d = (w[1] - w[0]) / 4n; if (d < 1n) { d = 1n; } set(w[0] + d, w[1] + d); });
  btn("Reset", function () { delete windows[li]; drawLane(li); });
  box.appendChild(el("span", { id: "zoominfo" + li, "class": "note", "aria-live": "polite" }));
  box.hidden = false;
  drawLane(li);
}

try {
  ["symptoms", "links", "chains", "evidence"].forEach(setupTable);
  V.lanes.forEach(function (ln, li) { setupZoom(li); });
  Array.prototype.forEach.call(document.querySelectorAll("table[data-managed]"), function () { /* managed tables are rebuilt above */ });
  fromHash();
  document.documentElement.setAttribute("data-enhanced", "1");
} catch (e) { /* the static page stays usable */ }
})();
"""
