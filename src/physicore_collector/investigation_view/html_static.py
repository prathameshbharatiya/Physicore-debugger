"""The static layer of the HTML page (FR-HT-*): the complete report as semantic HTML with inline SVG, readable with JavaScript disabled.

One file, UTF-8, nothing fetched (no src/href to another resource, no url() except the in-document marker, no @import, no fetch), a strict
Content-Security-Policy, no inline event handler and no style attribute. Data is escaped by `safety.html_escape`; the data block for the script
is `safety.json_for_script`. Geometry is computed with integers only."""
from __future__ import annotations

from physicore_collector.localization.vocabulary import (
    CHAIN_DEFENCE, DEFENCE_STATES, LINK_LABELS, SCOPE_SENTENCE)

from .fmt import LIST_CAP, capjoin, interval, ns, offset_text, plural, x_pos
from .html_assets import CSS, JS, csp_hash
from .markdown import KNOWN_SENTENCES, ROW_CAP
from .safety import html_escape, json_for_script
from .templates import COLS, HEADINGS, LABEL_MEANING, LEGEND, T, TABLE_TITLES

W = 1000            # SVG axis width in user units
LEFT = 12
TOP = 40
ROWH = 26
BARH = 14
MAXBARS = 300       # bars drawn per lane in the static page (the page script redraws the visible window)
LABELS_UP_TO = 60
MAX_DIAGRAM_NODES = 200
TOKEN_CLASS = set(LINK_LABELS)


def h(x: object) -> str:
    return html_escape(x)


def fx(x: object) -> str:
    """Fixed text of this package: escaped, never truncated."""
    return html_escape(x, None)


def sent(x: object) -> str:
    return html_escape(x, None) if x in KNOWN_SENTENCES else html_escape(x, 600)


def join(xs, sep: str = ", ", none: str = "none") -> str:
    xs = list(xs)
    return capjoin([h(x) for x in xs], sep, none)


def p(name: str, cls: str = "", **kw: object) -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<p{c}>{T[name].format(**kw)}</p>"


class Ctx:
    def __init__(self, v: dict):
        self.v = v
        self.sym_i = {s["id"]: i for i, s in enumerate(v["symptoms"])}
        self.sym = {s["id"]: s for s in v["symptoms"]}
        self.link_i = {l["id"]: i for i, l in enumerate(v["links"])}
        self.chain_i = {c["id"]: i for i, c in enumerate(v["chains"])}
        self.blk_i = {b["id"]: i for i, b in enumerate(v["blockers"])}
        self.ref_i = {r["id"]: i for i, r in enumerate(v["evidence_refs"])}

    def a(self, kind: str, ident: str, text: str | None = None) -> str:
        table = {"s": self.sym_i, "l": self.link_i, "c": self.chain_i, "b": self.blk_i, "E": self.ref_i}[kind]
        i = table.get(ident)
        label = h(ident) if text is None else text
        if i is None or i >= ROW_CAP:
            return label
        return f'<a href="#{kind}{i}">{label}</a>'

    def alist(self, kind: str, ids, sep: str = ", ") -> str:
        ids = list(ids)
        return capjoin([self.a(kind, i) for i in ids], sep)


def table(key: str, rows: list[tuple[str, list[str]]], what: str, cap: int = ROW_CAP, attrs: str = "") -> str:
    """rows: (row id attribute or '', cells as html)."""
    cols = COLS[key]
    out = [f'<div class="tablewrap"><table{attrs}><caption>{h(TABLE_TITLES[key])}</caption><thead><tr>' + "".join(f'<th scope="col">{h(c)}</th>' for c in cols) + "</tr></thead><tbody>"]
    for rid, cells in rows[:cap]:
        assert len(cells) == len(cols)
        out.append(f'<tr{rid}>' + "".join(f"<td>{c if c != '' else 'none'}</td>" for c in cells) + "</tr>")
    out.append("</tbody></table></div>")
    if len(rows) > cap:
        out.append(f'<p class="note" data-more="{h(key)}">{T["more"].format(n=len(rows) - cap, what=h(what))}</p>')
    return "".join(out)


def rid(kind: str, i: int) -> str:
    return f' id="{kind}{i}" data-i="{i}"'


# ---------------------------------------------------------------- SVG timeline
def lane_svg(ctx: Ctx, li: int) -> str:
    v = ctx.v
    ln = v["lanes"][li]
    items = [it for it in ln["items"] if it["lower_ns"] is not None][:MAXBARS]
    ranks = {layer: r for r, layer in enumerate(sorted({it["layer"] for it in items}))}
    span = ln["span_ns"]
    height = TOP + max(1, len(ranks)) * ROWH + 14
    pos: dict[str, tuple[int, int, int]] = {}
    out = [f'<svg class="timeline" id="tl{li}" data-lane="{li}" viewBox="0 0 {W + 2 * LEFT} {height}" role="img" aria-labelledby="tl{li}t">',
           f'<title id="tl{li}t">{h("Timeline of clock domain " + ln["domain"])}</title>',
           f'<line class="axis" x1="{LEFT}" y1="{TOP - 12}" x2="{LEFT + W}" y2="{TOP - 12}"/>']
    for k in range(5):
        off = span * k // 4
        x = LEFT + x_pos(off, 0, span, W)
        anchor = "start" if k == 0 else ("end" if k == 4 else "middle")
        out.append(f'<line class="tick" x1="{x}" y1="{TOP - 16}" x2="{x}" y2="{TOP - 8}"/><text x="{x}" y="{TOP - 20}" text-anchor="{anchor}">{h(offset_text(off))}</text>')
    for n, it in enumerate(items):
        x1 = LEFT + x_pos(it["off_lower_ns"], 0, span, W)
        x2 = LEFT + x_pos(it["off_upper_ns"], 0, span, W)
        w = max(x2 - x1, 4)
        y = TOP + ranks[it["layer"]] * ROWH
        pos[it["id"]] = (x1, x1 + w, y + BARH // 2)
        s = ctx.sym.get(it["id"])
        tip = f'{it["id"]}: {interval(it["lower_ns"], it["upper_ns"])}' + (f' on {s["topic"]}' if s else "")
        rect = f'<rect class="bar" data-sym="{ctx.sym_i.get(it["id"], -1)}" x="{x1}" y="{y}" width="{w}" height="{BARH}"><title>{h(tip)}</title></rect>'
        si = ctx.sym_i.get(it["id"])
        out.append(f'<a href="#s{si}">{rect}</a>' if si is not None and si < ROW_CAP else rect)
        if len(items) <= LABELS_UP_TO and s:
            lab = s["topic"] if len(s["topic"]) <= 28 else s["topic"][:27] + "…"
            lx = x1 + w + 4 if x1 + w + 4 + 7 * len(lab) < LEFT + W else x1 - 4
            out.append(f'<text x="{lx}" y="{y + BARH - 3}" text-anchor="{"start" if lx > x1 else "end"}">{h(lab)}</text>')
    drawn_links = 0
    for l in v["links"]:
        if l["lane"] != ln["domain"] or l["from_symptom"] not in pos or l["to_symptom"] not in pos or drawn_links >= MAXBARS:
            continue
        a, b = pos[l["from_symptom"]], pos[l["to_symptom"]]
        cls = l["label"] if l["label"] in TOKEN_CLASS else "other"
        out.append(f'<path class="arrow l-{cls}" data-link="{ctx.link_i[l["id"]]}" d="M{a[1]},{a[2]} L{max(b[0] - 1, a[1] + 1)},{b[2]}"><title>{h(l["label"] + " along " + ", ".join(l["edge_ids"]))}</title></path>')
        drawn_links += 1
    out.append("</svg>")
    return "".join(out)


def legend_html() -> str:
    def sample(cls: str) -> str:
        return f'<svg viewBox="0 0 70 12" aria-hidden="true"><path class="arrow l-{cls}" d="M2,6 L66,6" stroke="currentColor" stroke-width="1.5" fill="none"/></svg>'
    items = "".join(f'<li>{sample(lab)} <span class="mono">{h(lab)}</span>: {fx(LABEL_MEANING[lab])}</li>' for lab in LINK_LABELS)
    return (f'<div class="legend"><h3>{h(LEGEND["title"])}</h3><ul class="plain"><li>{fx(LEGEND["bar"])}</li><li>{fx(LEGEND["arrow"])}</li><li>{fx(LEGEND["axis"])}</li></ul>'
            f'<ul class="plain">{items}</ul></div>')


# ---------------------------------------------------------------- component diagram (declared edges that carry symptoms)
def diagram_svg(v: dict) -> str:
    edges = v["coverage"]["edges"]
    if not edges:
        return ""
    nodes: dict[str, int] = {}
    for e in edges:
        nodes[e["from_node"]] = max(nodes.get(e["from_node"], 0), e["from_count"])
        nodes[e["to_node"]] = max(nodes.get(e["to_node"], 0), e["to_count"])
    if len(nodes) > MAX_DIAGRAM_NODES:
        return f'<p class="note">The diagram is not drawn: {len(nodes)} nodes exceed the limit of {MAX_DIAGRAM_NODES}; the table below lists every declared edge.</p>'
    names = sorted(nodes)
    layer = {n: 0 for n in names}
    for _ in range(len(names)):                       # bounded relaxation: a cycle in the declarations cannot loop forever
        changed = False
        for e in sorted(edges, key=lambda e: (e["from_node"], e["to_node"], e["edge_id"])):
            if layer[e["to_node"]] < layer[e["from_node"]] + 1 and layer[e["from_node"]] + 1 < len(names):
                layer[e["to_node"]] = layer[e["from_node"]] + 1
                changed = True
        if not changed:
            break
    cols: dict[int, list[str]] = {}
    for n in names:
        cols.setdefault(layer[n], []).append(n)
    bw, bh, gx, gy = 190, 36, 70, 18
    pos = {n: (10 + c * (bw + gx), 10 + r * (bh + gy)) for c, ns_ in sorted(cols.items()) for r, n in enumerate(ns_)}
    width = 20 + (max(cols) + 1) * (bw + gx) - gx
    height = 20 + max(len(c) for c in cols.values()) * (bh + gy) - gy
    out = [f'<svg class="diagram" viewBox="0 0 {width} {height}" role="img" aria-labelledby="dgt"><title id="dgt">Declared edges between nodes, with the number of symptoms observed on each node</title>',
           '<defs><marker id="head2" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker></defs>']
    for e in sorted(edges, key=lambda e: (e["from_node"], e["to_node"], e["edge_id"])):
        (x1, y1), (x2, y2) = pos[e["from_node"]], pos[e["to_node"]]
        if x2 > x1:
            d = f"M{x1 + bw},{y1 + bh // 2} L{x2},{y2 + bh // 2}"
        else:
            d = f"M{x1 + bw // 2},{y1 + bh} C{x1 + bw // 2},{y1 + bh + 30} {x2 + bw // 2},{y2 - 30} {x2 + bw // 2},{y2}"
        out.append(f'<path class="edge" marker-end="url(#head2)" d="{d}"><title>{h(e["edge_id"] + ": " + e["from_node"] + " to " + e["to_node"] + " (" + e["status"] + ")")}</title></path>')
    for n in names:
        x, y = pos[n]
        lab = n if len(n) <= 26 else n[:25] + "…"
        out.append(f'<rect class="node" x="{x}" y="{y}" width="{bw}" height="{bh}" rx="5"><title>{h(n)}</title></rect><text x="{x + 8}" y="{y + 15}">{h(lab)}</text>'
                   f'<text x="{x + 8}" y="{y + 29}">{h(plural(nodes[n], "symptom"))}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- document
def section(idx: int, body: str, *, open_: bool = True, always_open: bool = False) -> str:
    sid = f"sec{idx}"
    title = h(HEADINGS[idx])
    if always_open:
        return f'<section id="{sid}" aria-labelledby="{sid}h"><h2 id="{sid}h">{title}</h2>{body}</section>'
    return f'<section id="{sid}" aria-labelledby="{sid}h"><details{" open" if open_ else ""}><summary><h2 id="{sid}h" class="inl">{title}</h2></summary>{body}</details></section>'


def render_html(view: dict) -> str:
    v = view
    c = Ctx(v)
    hd = v["header"]
    cv = v["coverage"]
    cn = v["counts"]
    parts: list[str] = []

    # header
    top = [f"<h1>{h(T['title'])}</h1>", p("subtitle", schema=h(hd["schema"]), tool=h(hd["tool_version"])), p("scope", sentence=sent(SCOPE_SENTENCE)),
           p("severity", sentence=sent(hd["severity"])), p("order_note", "note")]

    # 0 summary (never collapsible away: it carries the exit status)
    s0 = [p("exit", code=hd["exit_code"], reasons=join(hd["exit_reasons"]))]
    for pr in hd["problems"]:
        s0.append(p("problem", "warn", source=h(pr["source"]), path=h(pr["path"]), message=html_escape(pr["message"], 300)))
    s0.append(p("counts", symptoms=cn["symptoms"], links=cn["links"], links_total=cn["links_total"], components=cn["components"], chains=cn["chains"], blockers=cn["blockers"],
                caveats=cn["caveats"], refs=cn["refs"]))
    s0.append(p("caps", "note", cap=ROW_CAP, items=LIST_CAP))
    if cv["dependencies"] == "none_declared":
        s0.append(p("no_deps", "warn"))
    if cn["symptoms"] == 0 and hd["exit_code"] != 3:
        s0.append(p("no_symptoms"))
    ver = v["verification"]
    s0.append(p("ver_lead", path=h(ver["path"]), verified=ver["summary"]["verified"], mismatch=ver["summary"]["mismatch"], not_checked=ver["summary"]["not_checked"]) if ver
              else p("ver_none", "note"))
    sections = [section(0, "".join(s0), always_open=True)]

    # 1 first divergence
    if v["first_divergence"]:
        body = p("fd_lead") + table("fd", [("", [c.a("s", f["id"]), h(f["lane"]), h(interval(f["lower_ns"], f["upper_ns"])), "none" if f["off_lower_ns"] is None else h(offset_text(f["off_lower_ns"])),
                                                 h(f["basis"]), h(f["rule"]), h(f["topic"])]) for f in v["first_divergence"]], "first-divergence rows")
    else:
        body = p("fd_none")
    sections.append(section(1, body))

    # 2 lanes and timeline
    body = [legend_html()]
    if not v["lanes"]:
        body.append(p("none"))
    for i, ln in enumerate(v["lanes"]):
        e = ln["earliest"]
        body.append(f'<h3>{h(T["lane_head"].format(n=i + 1, total=len(v["lanes"])))}</h3>')
        body.append(p("lane_domain", domain=h(ln["domain"]), count=ln["symptom_count"], layers=len(ln["layers"]), start="none" if ln["origin_ns"] is None else h(ns(ln["origin_ns"]))))
        body.append(p("lane_axis", "note"))
        body.append(p("lane_statement", sentence=sent(e["statement"])))
        if e["ids"]:
            body.append(p("lane_earliest", ids=c.alist("s", e["ids"])))
        if e["blocker_ids"]:
            body.append(p("lane_blockers", ids=c.alist("b", e["blocker_ids"])))
        drawn = min(len([it for it in ln["items"] if it["lower_ns"] is not None]), MAXBARS)
        total = len([it for it in ln["items"] if it["lower_ns"] is not None])
        if total > drawn:
            body.append(f'<p class="note">The timeline shows the first {drawn} of {total} symptoms of this lane; the table below and the page script give the rest.</p>')
        body.append(f'<div class="zoomctl" id="zoom{i}" hidden></div>')
        body.append(lane_svg(c, i))
        rows = []
        for it in ln["items"]:
            s = c.sym.get(it["id"])
            rows.append(("", [c.a("s", it["id"]), str(it["layer"]), h("none" if it["lower_ns"] is None else interval(it["lower_ns"], it["upper_ns"])),
                              "none" if it["off_lower_ns"] is None else h(offset_text(it["off_lower_ns"])), h(s["onset_basis"]) if s else "none",
                              h(s["topic"]) if s else "none", h(s["severity"]) if s else "none"]))
        body.append(table("lane", rows, "lane rows"))
    if cv["cross_lane_statement"]:
        body.append(p("cross_lane", sentence=sent(cv["cross_lane_statement"])))
    sections.append(section(2, "".join(body)))

    # 3 symptoms (page script manages filter, paging)
    body = ['<div class="controls" id="controls-symptoms" hidden></div><p class="note" id="count-symptoms" aria-live="polite" hidden></p>']
    if v["symptoms"]:
        rows = []
        for i, s in enumerate(v["symptoms"]):
            rows.append((rid("s", i), [h(s["id"]), h(s["source"]), h(s["rule"]), h(s["category"]), h(s["topic"]), h(s["node"]), h(interval(s["onset"]["lower_ns"], s["onset"]["upper_ns"])),
                                       h(s["onset_basis"]), h(s["severity"]), h(s["coverage"]["topic_coverage"]), "yes" if s["truncated"] else "no",
                                       capjoin([f"{h(k)}={h(val)}" for k, val in s["identities"]], "; "), join(s["notes"] + s["uncertainty"], "; "),
                                       c.alist("E", s["evidence"])]))
        body.append(table("symptom", rows, "symptoms", attrs=' id="tbl-symptoms" data-managed="symptoms"'))
        body.append('<div class="pager" id="pager-symptoms" hidden></div>')
    else:
        body.append(p("none"))
    sections.append(section(3, "".join(body)))

    # 4 links
    body = [p("label_head")]
    body.append('<ul class="plain">' + "".join(f"<li>{T['label_item'].format(label=h(lab), meaning=fx(LABEL_MEANING[lab]))}</li>" for lab in LINK_LABELS) + "</ul>")
    body.append(p("link_note", "note"))
    body.append('<div class="controls" id="controls-links" hidden></div><p class="note" id="count-links" aria-live="polite" hidden></p>')
    if v["links"]:
        rows = [(rid("l", i), [h(l["id"]), h(l["lane"]), f'{c.a("s", l["from_symptom"])} on {h(l["from_node"])}', f'{c.a("s", l["to_symptom"])} on {h(l["to_node"])}', join(l["edge_ids"]),
                               h(l["label"]), f'{h(ns(l["sep_min_ns"]))} to {h(ns(l["sep_max_ns"]))}', "none declared" if l["window_ns"] is None else h(ns(l["window_ns"])), join(l["notes"])])
                for i, l in enumerate(v["links"])]
        body.append(table("link", rows, "links", attrs=' id="tbl-links" data-managed="links"'))
        body.append('<div class="pager" id="pager-links" hidden></div>')
        via = [("", [c.a("l", l["id"]), h(i["node"]), c.alist("s", i["between"]), h(i["coverage"]), join(i["detectors"])]) for l in v["links"] for i in l["intermediates"]]
        if via:
            body.append(table("via", via, "intermediate-node rows"))
    else:
        body.append(p("none"))
    sections.append(section(4, "".join(body)))

    # 5 chains and components
    body = []
    if cv["dependencies"] == "declared":
        body.append(p("chains_exact", n=v["chains_total"]) if v["chains_total"] is not None else p("chains_unknown", n=cv["chains_lower_bound"]))
        if cv["chains_truncated_by"]:
            body.append(p("chains_trunc", n=cv["chains_reported"], why=h(cv["chains_truncated_by"])))
    body.append(diagram_svg(v))
    if v["chains"]:
        body.append('<div class="controls" id="controls-chains" hidden></div><p class="note" id="count-chains" aria-live="polite" hidden></p>')
        body.append(table("chain", [(rid("c", i), [h(ch["id"]), h(ch["lane"]), c.a("s", ch["head"]), capjoin([c.a("s", s) for s in ch["symptom_ids"]], " then "), capjoin([h(n) for n in ch["nodes"]], " then "),
                                                   h(ch["defence"]), c.alist("b", ch["blocker_ids"])]) for i, ch in enumerate(v["chains"])], "chains", attrs=' id="tbl-chains" data-managed="chains"'))
        body.append('<div class="pager" id="pager-chains" hidden></div>')
    if v["components"]:
        body.append(table("component", [("", [h(k["id"]), join(k["lanes"]), c.alist("s", k["symptom_ids"]), str(len(k["link_ids"])), h(k["defence"]), c.alist("b", k["blocker_ids"])]) for k in v["components"]], "components"))
    body.append(p("loc_lead", "note"))
    L = v["localization"]
    if L["components"]:
        body.append(table("loc", [("", [h(k["id"]), h(x["lane"]), join(x["locus"]), join(x["frontier"])]) for k in L["components"] for x in k["lanes"]], "localization rows"))
    if L["unlocalized"]:
        body.append(table("unloc", [("", [c.a("s", u["id"]), h(u["topic"]), h(u["node"])]) for u in L["unlocalized"]], "unlocalized rows"))
    if L["topic_layers"]:
        body.append(table("topic", [("", [h(t["lane"]), h(t["topic"]), str(t["layer"]), c.alist("s", t["ids"])]) for t in L["topic_layers"]], "topic-layer rows"))
    if cv["dependencies"] == "declared":
        body.append(p("undeclared", n=cv["no_declared_relation_count"], m=cv["cross_lane_pairs_not_compared"]))
    sections.append(section(5, "".join(body)))

    # 6 hypotheses
    lh = [("", [c.a("l", l["id"]), h(l["hypothesis"]["template"]), h(l["hypothesis"]["status"]), sent(l["hypothesis"]["statement"])]) for l in v["links"] if l["hypothesis"]]
    chh = [("", [c.a("c", ch["id"]), h(x["template"]), h(x["status"]), sent(x["statement"]),
                 "none" if x["ancestors_total"] is None else f"{x['ancestors_total']} ({len(x['ancestors'])} listed): {join(x['ancestors'])}",
                 capjoin([sent(n) for n in x["needs"]], "; ")]) for ch in v["chains"] for x in ch["hypotheses"]]
    body = (p("hyp_lead") + (table("lhyp", lh, "link hypothesis rows") if lh else "") + (table("chyp", chh, "chain hypothesis rows") if chh else "")) if (lh or chh) else p("none")
    sections.append(section(6, body))

    # 7 blockers and caveats (never filtered, never collapsed away)
    body = []
    if v["blockers"]:
        body.append(table("blocker", [(rid("b", i), [h(b["id"]), h(b["code"]), sent(b["detail"]), h(b["detector"] or "any"), h(b["topic"] or "any"), h(b["lane"] or "every lane"),
                                                    "unknown" if b["count"] is None else str(b["count"]), f'{h(b["source"]["kind"])} {h(b["source"]["ref"])} field {h(b["source"]["field"])}',
                                                    capjoin([f'{h(a["artifact_ref"])} {h(a["sha256"] or "no digest")}' for a in b["artifacts"]], "; "), c.alist("E", b["observations"])])
                                     for i, b in enumerate(v["blockers"])], "blockers", cap=10 ** 9))
    else:
        body.append(p("none"))
    if v["caveats"]:
        body.append(table("caveat", [("", [h(k["code"]), sent(k["detail"]), h(k["detector"] or "any")]) for k in v["caveats"]], "caveats", cap=10 ** 9))
    sections.append(section(7, "".join(body), always_open=True))

    # 8 coverage
    body = [table("det", [("", [h(d["detector_id"]), h(d["status"])]) for d in cv["detectors"]], "detector rows")]
    if cv["m2_status"]:
        body.append(p("m2", status=h(cv["m2_status"])))
    if cv["dependencies"] == "declared":
        body.append(p("links_status", n=cn["links"], total=cv["links_total"], suppressed=cv["links_suppressed"], status=h(cv["links_status"] or "none")))
        body.append(p("pairs", related=cv["path_pairs_related"], examined=cv["path_pairs_examined"], not_examined=cv["path_pairs_not_examined"]))
        if cv["edges"]:
            body.append(table("edge", [("", [h(x["edge_id"]), h(x["from_node"]), h(x["to_node"]), str(x["from_count"]), str(x["to_count"]), h(x["status"])]) for x in cv["edges"]], "edge rows"))
    sections.append(section(8, "".join(body)))

    # 9 limits and limitations (never collapsed away)
    body = ["<ul class=\"plain\">" + "".join(f"<li>{T['limit_item'].format(name=h(k), value=val)}</li>" for k, val in v["limits"]) + "</ul>",
            "<ol>" + "".join(f"<li>{sent(t)}</li>" for t in v["limitations"]) + "</ol>"]
    sections.append(section(9, "".join(body), always_open=True))

    # 10 evidence references
    body = ['<div class="controls" id="controls-evidence" hidden></div><p class="note" id="count-evidence" aria-live="polite" hidden></p>']

    def vtext(x: dict) -> str:
        r = x["verification"]
        return "not requested" if r is None else (r["status"] if not r.get("detail") else f"{r['status']} ({h(r['detail'])})")
    if v["evidence_refs"]:
        rows = [(rid("E", i), [x["id"], h(x["artifact_ref"]), str(x["message_index"]), h(x["topic"] or "none"), "none" if x["time_ns"] is None else h(ns(x["time_ns"])), h(x["clock_domain"] or "none"),
                               h(x["sha256"] or "none"), capjoin([f'{h(k["kind"])} {c.a("s" if k["kind"] == "symptom" else "b", k["id"])}' for k in x["cited_by"]]), vtext(x)])
                for i, x in enumerate(v["evidence_refs"])]
        body.append(table("evidence", rows, "evidence references", attrs=' id="tbl-evidence" data-managed="evidence"'))
        body.append('<div class="pager" id="pager-evidence" hidden></div>')
    else:
        body.append(p("none"))
    sections.append(section(10, "".join(body)))

    # 11 provenance
    d = hd["inputs"]["dependencies"]
    body = [p("prov_report", sha=h(hd["report_sha256"]), size=hd["report_bytes"]), p("prov_params", hash=h(hd["parameters_hash"])),
            p("prov_inputs", cfg=h(hd["inputs"]["config_hash"] or "none"), exp=h(hd["inputs"]["expectations_sha256"] or "none")),
            p("deps_declared", edges=d["edge_count"], sha=h(d["sha256"]), sem=h(d["semantic_hash"])) if d else p("deps_none"),
            "<ul class=\"plain\">" + "".join(f"<li>Evidence artifact {h(x['artifact_ref'])}: SHA-256 {h(x['sha256'])}" + (f"; verification {h(x['verification']['status'])}" if x["verification"] else "") + ".</li>" for x in hd["evidence"]) + "</ul>"]
    sections.append(section(11, "".join(body)))

    nav = '<nav class="sections" aria-label="Sections">' + "".join(f'<a href="#sec{i}">{h(t)}</a>' for i, t in enumerate(HEADINGS)) + "</nav>"
    data = json_for_script(v)
    csp = ("default-src 'none'; script-src '" + csp_hash(JS) + "'; style-src '" + csp_hash(CSS) + "'; img-src 'none'; connect-src 'none'; base-uri 'none'; form-action 'none'")
    doc = ['<!doctype html>', '<html lang="en">', '<head><meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f'<meta http-equiv="Content-Security-Policy" content="{csp}">', '<meta name="generator" content="physicore-collector investigation view">',
           f'<title>{h(T["title"])}</title>', f"<style>{CSS}</style></head>", "<body>", '<a class="skip" href="#main">Skip to the report</a>',
           '<svg width="0" height="0" aria-hidden="true" focusable="false"><defs><marker id="head" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker></defs></svg>',
           f'<header class="top">{"".join(top)}{nav}</header>', '<main id="main">', "".join(sections),
           '<aside id="detail" aria-live="polite" aria-label="Details of the selected item" hidden></aside>', "</main>",
           f'<footer>{p("footer", "note")}</footer>', f'<script type="application/json" id="view-data">{data}</script>']
    if JS:
        doc.append(f"<script>{JS}</script>")
    doc += ["</body>", "</html>", ""]
    return "\n".join(doc)
