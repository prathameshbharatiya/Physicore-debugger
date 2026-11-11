"""The Markdown investigation report (FR-MD-*): CommonMark plus pipe tables, no raw HTML, no images, no links. Every sentence is a template of
`templates.T`; every datum is escaped by `safety.md_escape` and is never placed at the start of a line or in a heading."""
from __future__ import annotations

from physicore_collector.localization.vocabulary import (
    CHAIN_DEFENCE, DEFENCE_STATES, HYPOTHESIS_STATUSES, HYPOTHESIS_TEMPLATES, LINK_LABELS, ONSET_BASES, SCOPE_SENTENCE, SYMPTOM_SOURCES)

from .fmt import LIST_CAP, capjoin, interval, ns, offset_text
from .safety import md_escape
from .templates import COLS, HEADINGS, LABEL_MEANING, T

ROW_CAP = 200
_TOKENS = set(LINK_LABELS) | set(HYPOTHESIS_TEMPLATES) | set(HYPOTHESIS_STATUSES) | set(DEFENCE_STATES) | set(CHAIN_DEFENCE) | set(ONSET_BASES) | set(SYMPTOM_SOURCES)


def e(x: object) -> str:
    """A data value or a token of a closed vocabulary, escaped."""
    return str(x) if isinstance(x, str) and x in _TOKENS else md_escape(x)


def _known_sentences() -> frozenset[str]:
    from physicore_collector.localization import blockers as _b
    from physicore_collector.localization.output import LIMITATIONS
    from physicore_collector.localization.vocabulary import (
        DISCRIMINATING_EVIDENCE, HYPOTHESIS_SENTENCES, NONE_OBSERVED_SENTENCE, NOT_COMPARED_ACROSS_LANES, NOT_DEFENDED_SENTENCE, SEVERITY_DISCLAIMER, TIED_SENTENCE)
    return frozenset([SCOPE_SENTENCE, SEVERITY_DISCLAIMER, NOT_DEFENDED_SENTENCE, TIED_SENTENCE, NONE_OBSERVED_SENTENCE, NOT_COMPARED_ACROSS_LANES,
                      *LIMITATIONS, *DISCRIMINATING_EVIDENCE, *HYPOTHESIS_SENTENCES.values(), *_b.DETAIL.values(), *_b.CAVEAT_DETAIL.values()])


KNOWN_SENTENCES = _known_sentences()


def es(x: object) -> str:
    """A report-supplied sentence: the fixed sentences of M3d are used as they are (they contain no Markdown syntax; a test proves it); anything else
    is cleaned, truncated at 600 and escaped like data."""
    return str(x) if x in KNOWN_SENTENCES else md_escape(x, 600)


def join(xs, sep: str = ", ", none: str = "none") -> str:
    xs = list(xs)
    return capjoin([e(x) for x in xs], sep, none)


def ival(lo: int | None, hi: int | None) -> str:
    return "none" if lo is None else interval(lo, hi)


def _cell(x: object) -> str:
    return str(x) if str(x) != "" else "none"


def table(out: list[str], key: str, rows: list[list[str]], what: str, cap: int = ROW_CAP) -> None:
    cols = COLS[key]
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "|".join("---" for _ in cols) + "|")
    for row in rows[:cap]:
        assert len(row) == len(cols)
        out.append("| " + " | ".join(_cell(c) for c in row) + " |")
    out.append("")
    if len(rows) > cap:
        out.append(T["more"].format(n=len(rows) - cap, what=what))
        out.append("")


def line(out: list[str], tname: str, **kw: object) -> None:
    out.append(T[tname].format(**kw))
    out.append("")


def render_markdown(view: dict) -> str:
    v = view
    h = v["header"]
    out: list[str] = []
    out += [f"# {T['title']}", ""]
    line(out, "subtitle", schema=e(h["schema"]), tool=e(h["tool_version"]))
    line(out, "scope", sentence=SCOPE_SENTENCE)
    line(out, "severity", sentence=es(h["severity"]))
    line(out, "order_note")

    # ---- Summary
    out += [f"## {HEADINGS[0]}", ""]
    line(out, "exit", code=h["exit_code"], reasons=join(h["exit_reasons"]))
    for p in h["problems"]:
        line(out, "problem", source=e(p["source"]), path=md_escape(p["path"]), message=md_escape(p["message"], 300))
    c = v["counts"]
    line(out, "counts", symptoms=c["symptoms"], links=c["links"], links_total=c["links_total"], components=c["components"], chains=c["chains"],
         blockers=c["blockers"], caveats=c["caveats"], refs=c["refs"])
    line(out, "caps", cap=ROW_CAP, items=LIST_CAP)
    if v["coverage"]["dependencies"] == "none_declared":
        line(out, "no_deps")
    if c["symptoms"] == 0 and h["exit_code"] != 3:
        line(out, "no_symptoms")

    # ---- First divergence
    out += [f"## {HEADINGS[1]}", ""]
    if v["first_divergence"]:
        line(out, "fd_lead")
        table(out, "fd", [[e(f["id"]), e(f["lane"]), ival(f["lower_ns"], f["upper_ns"]), "none" if f["off_lower_ns"] is None else offset_text(f["off_lower_ns"]),
                           e(f["basis"]), e(f["rule"]), e(f["topic"])] for f in v["first_divergence"]], "first-divergence rows")
    else:
        line(out, "fd_none")

    # ---- Lanes
    out += [f"## {HEADINGS[2]}", ""]
    if not v["lanes"]:
        line(out, "none")
    sym_by_id = {s["id"]: s for s in v["symptoms"]}
    for i, ln in enumerate(v["lanes"], 1):
        out += [f"### {T['lane_head'].format(n=i, total=len(v['lanes']))}", ""]
        line(out, "lane_domain", domain=e(ln["domain"]), count=ln["symptom_count"], layers=len(ln["layers"]),
             start="none" if ln["origin_ns"] is None else ns(ln["origin_ns"]))
        line(out, "lane_axis")
        line(out, "lane_statement", sentence=es(ln["earliest"]["statement"]))
        if ln["earliest"]["ids"]:
            line(out, "lane_earliest", ids=join(ln["earliest"]["ids"]))
        if ln["earliest"]["blocker_ids"]:
            line(out, "lane_blockers", ids=join(ln["earliest"]["blocker_ids"]))
        rows = []
        for it in ln["items"]:
            s = sym_by_id.get(it["id"])
            rows.append([e(it["id"]), it["layer"], ival(it["lower_ns"], it["upper_ns"]), "none" if it["off_lower_ns"] is None else offset_text(it["off_lower_ns"]),
                         e(s["onset_basis"]) if s else "none", e(s["topic"]) if s else "none", e(s["severity"]) if s else "none"])
        table(out, "lane", rows, "lane rows")
    if v["coverage"]["cross_lane_statement"]:
        line(out, "cross_lane", sentence=es(v["coverage"]["cross_lane_statement"]))

    # ---- Symptoms
    out += [f"## {HEADINGS[3]}", ""]
    if v["symptoms"]:
        table(out, "symptom", [[e(s["id"]), e(s["source"]), e(s["rule"]), e(s["category"]), e(s["topic"]), e(s["node"]), ival(s["onset"]["lower_ns"], s["onset"]["upper_ns"]),
                                e(s["onset_basis"]), e(s["severity"]), e(s["coverage"]["topic_coverage"]), "yes" if s["truncated"] else "no",
                                capjoin([f"{e(k)}={e(val)}" for k, val in s["identities"]], "; "),
                                join(s["notes"] + s["uncertainty"], "; "), join(s["evidence"])] for s in v["symptoms"]], "symptoms")
    else:
        line(out, "none")

    # ---- Links
    out += [f"## {HEADINGS[4]}", ""]
    line(out, "label_head")
    for lab in LINK_LABELS:
        out.append("- " + T["label_item"].format(label=lab, meaning=LABEL_MEANING[lab]))
    out.append("")
    line(out, "link_note")
    if v["links"]:
        table(out, "link", [[e(l["id"]), e(l["lane"]), f"{e(l['from_symptom'])} on {e(l['from_node'])}", f"{e(l['to_symptom'])} on {e(l['to_node'])}", join(l["edge_ids"]),
                             e(l["label"]), f"{ns(l['sep_min_ns'])} to {ns(l['sep_max_ns'])}", "none declared" if l["window_ns"] is None else ns(l["window_ns"]),
                             join(l["notes"])] for l in v["links"]], "links")
        via = [[e(l["id"]), e(i["node"]), join(i["between"]), e(i["coverage"]), join(i["detectors"])] for l in v["links"] for i in l["intermediates"]]
        if via:
            table(out, "via", via, "intermediate-node rows")
    else:
        line(out, "none")

    # ---- Chains and components
    out += [f"## {HEADINGS[5]}", ""]
    if v["coverage"]["dependencies"] == "declared":
        if v["chains_total"] is not None:
            line(out, "chains_exact", n=v["chains_total"])
        else:
            line(out, "chains_unknown", n=v["coverage"]["chains_lower_bound"])
        if v["coverage"]["chains_truncated_by"]:
            line(out, "chains_trunc", n=v["coverage"]["chains_reported"], why=e(v["coverage"]["chains_truncated_by"]))
    if v["chains"]:
        table(out, "chain", [[e(c["id"]), e(c["lane"]), e(c["head"]), capjoin([e(i) for i in c["symptom_ids"]], " then "), capjoin([e(n) for n in c["nodes"]], " then "), e(c["defence"]),
                              join(c["blocker_ids"])] for c in v["chains"]], "chains")
    if v["components"]:
        table(out, "component", [[e(c["id"]), join(c["lanes"]), join(c["symptom_ids"]), len(c["link_ids"]), e(c["defence"]), join(c["blocker_ids"])] for c in v["components"]],
              "components")
    line(out, "loc_lead")
    L = v["localization"]
    if L["components"]:
        table(out, "loc", [[e(c["id"]), e(x["lane"]), join(x["locus"]), join(x["frontier"])] for c in L["components"] for x in c["lanes"]], "localization rows")
    if L["unlocalized"]:
        table(out, "unloc", [[e(u["id"]), e(u["topic"]), e(u["node"])] for u in L["unlocalized"]], "unlocalized rows")
    if L["topic_layers"]:
        table(out, "topic", [[e(t["lane"]), e(t["topic"]), t["layer"], join(t["ids"])] for t in L["topic_layers"]], "topic-layer rows")
    if v["coverage"]["dependencies"] == "declared":
        cv = v["coverage"]
        line(out, "undeclared", n=cv["no_declared_relation_count"], m=cv["cross_lane_pairs_not_compared"])

    # ---- Hypotheses
    out += [f"## {HEADINGS[6]}", ""]
    lh = [[e(l["id"]), e(l["hypothesis"]["template"]), e(l["hypothesis"]["status"]), es(l["hypothesis"]["statement"])] for l in v["links"] if l["hypothesis"]]
    ch = [[e(c["id"]), e(x["template"]), e(x["status"]), es(x["statement"]),
           ("none" if x["ancestors_total"] is None else f"{x['ancestors_total']} ({len(x['ancestors'])} listed): {join(x['ancestors'])}"),
           capjoin([es(n) for n in x["needs"]], "; ")] for c in v["chains"] for x in c["hypotheses"]]
    if lh or ch:
        line(out, "hyp_lead")
        if lh:
            table(out, "lhyp", lh, "link hypothesis rows")
        if ch:
            table(out, "chyp", ch, "chain hypothesis rows")
    else:
        line(out, "none")

    # ---- Blockers and caveats
    out += [f"## {HEADINGS[7]}", ""]
    if v["blockers"]:
        table(out, "blocker", [[e(b["id"]), e(b["code"]), es(b["detail"]), e(b["detector"] or "any"), e(b["topic"] or "any"), e(b["lane"] or "every lane"),
                                "unknown" if b["count"] is None else b["count"], f"{e(b['source']['kind'])} {e(b['source']['ref'])} field {e(b['source']['field'])}",
                                capjoin([f"{e(a['artifact_ref'])} {e(a['sha256'] or 'no digest')}" for a in b["artifacts"]], "; "), join(b["observations"])]
                               for b in v["blockers"]], "blockers", cap=10**9)
    else:
        line(out, "none")
    if v["caveats"]:
        table(out, "caveat", [[e(c["code"]), es(c["detail"]), e(c["detector"] or "any")] for c in v["caveats"]], "caveats", cap=10**9)

    # ---- Coverage
    out += [f"## {HEADINGS[8]}", ""]
    cv = v["coverage"]
    table(out, "det", [[e(d["detector_id"]), e(d["status"])] for d in cv["detectors"]], "detector rows")
    if cv["m2_status"]:
        line(out, "m2", status=e(cv["m2_status"]))
    if cv["dependencies"] == "declared":
        line(out, "links_status", n=c["links"], total=cv["links_total"], suppressed=cv["links_suppressed"], status=e(cv["links_status"] or "none"))
        line(out, "pairs", related=cv["path_pairs_related"], examined=cv["path_pairs_examined"], not_examined=cv["path_pairs_not_examined"])
        if cv["edges"]:
            table(out, "edge", [[e(x["edge_id"]), e(x["from_node"]), e(x["to_node"]), x["from_count"], x["to_count"], e(x["status"])] for x in cv["edges"]], "edge rows")

    # ---- Limits and limitations
    out += [f"## {HEADINGS[9]}", ""]
    for k, val in v["limits"]:
        line(out, "limit_item", name=md_escape(k), value=val)
    for i, t in enumerate(v["limitations"], 1):
        line(out, "limitation", n=i, text=es(t))

    # ---- Evidence references
    out += [f"## {HEADINGS[10]}", ""]
    ver = v["verification"]
    if ver:
        sm = ver["summary"]
        line(out, "ver_lead", path=md_escape(ver["path"]), verified=sm["verified"], mismatch=sm["mismatch"], not_checked=sm["not_checked"])
    else:
        line(out, "ver_none")
    if v["evidence_refs"]:
        def vtext(x):
            r = x["verification"]
            return "not requested" if r is None else (r["status"] if not r.get("detail") else f"{r['status']} ({md_escape(r['detail'])})")
        table(out, "evidence", [[x["id"], e(x["artifact_ref"]), x["message_index"], e(x["topic"] or "none"), "none" if x["time_ns"] is None else ns(x["time_ns"]),
                                 e(x["clock_domain"] or "none"), e(x["sha256"] or "none"), capjoin([f"{e(c['kind'])} {e(c['id'])}" for c in x["cited_by"]]), vtext(x)]
                                for x in v["evidence_refs"]], "evidence references")
    else:
        line(out, "none")

    # ---- Provenance
    out += [f"## {HEADINGS[11]}", ""]
    line(out, "prov_report", sha=e(h["report_sha256"]), size=h["report_bytes"])
    line(out, "prov_params", hash=e(h["parameters_hash"]))
    line(out, "prov_inputs", cfg=e(h["inputs"]["config_hash"] or "none"), exp=e(h["inputs"]["expectations_sha256"] or "none"))
    d = h["inputs"]["dependencies"]
    line(out, "deps_declared", edges=d["edge_count"], sha=e(d["sha256"]), sem=e(d["semantic_hash"])) if d else line(out, "deps_none")
    for x in h["evidence"]:
        out.append(T["prov_artifact"].format(ref=e(x["artifact_ref"]), sha=e(x["sha256"])))
    out.append("")
    line(out, "footer")
    return "\n".join(out).rstrip("\n") + "\n"
