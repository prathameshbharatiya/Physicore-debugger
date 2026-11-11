"""M4e: the investigation report as a Markdown document and a single offline HTML page.

Both are rendered from ONE list of blocks (heading, paragraph, list, table), so they cannot disagree. Every sentence is a fixed sentence from the report
(itself drawn from fixed registries) or cleaned data, escaped for the format it is placed in with the escapers of the M3e view package. The page has no script
and no external resource. No sentence generated here states a cause.
"""
from __future__ import annotations

from physicore_collector.investigation_view.safety import clean_text, html_escape, md_escape
from physicore_collector.localization.vocabulary import SCOPE_SENTENCE

from . import causal as K

FIXED = {
    "title": "Hypothesis report",
    "subtitle": "Candidate mechanisms for the symptoms in a localization report, with the evidence for and against each. Each candidate is a proposal from a closed catalogue; none is called the cause.",
    "ladder_note": "The level printed beside a hypothesis is the highest level whose evidence is fully present. It is not a measure of how likely the mechanism is. Level 5 needs a deliberate test on the system and is never reached here.",
    "no_units": "No symptom was reported, so there is nothing to investigate. This is a statement about the detectors that ran, not about the system.",
    "none": "None.",
}


def _fixed(s):
    return ("f", s)          # a fixed sentence: escaped, never truncated


def _d(s):
    return ("d", s)          # data: cleaned, truncated, escaped


def _t(x):
    return x if isinstance(x, tuple) else _d(x)


def _detail(x: dict) -> str:
    k = x["kind"]
    if k == "observed":
        return f"symptom {x['symptom_id']} ({x.get('category')}) on {x.get('topic')}" + (f"; via link {x['link_id']}" if x.get("link_id") else "")
    if k == "inferred":
        ids = x.get("link_id") or ",".join(x.get("expectation_ids", [])) or x.get("check_id", "")
        return f"rule {x['rule']}: {ids}"
    if k == "assumption":
        return f"{x['slot']} bound from {x['source']}: {', '.join(x['topics'])}"
    if "token" in x:
        return f"measurement not available: {x['token']}"
    return f"check {x['check_id']}: {x.get('outcome') or 'no outcome'}" + (f" ({x['reason']})" if x.get("reason") else "")


def build_blocks(r: dict) -> list[tuple]:
    B: list[tuple] = []
    s, inp = r["summary"], r["inputs"]
    B.append(("h", 1, _fixed(FIXED["title"])))
    B.append(("p", _fixed(FIXED["subtitle"])))
    B.append(("p", _fixed("Scope: " + SCOPE_SENTENCE)))
    B.append(("p", _fixed(FIXED["ladder_note"])))
    B.append(("p", _fixed(f"Counts: {s['units']} investigation unit(s), {s['instances']} hypothesis instance(s), {s['leaders']} leading. "
                          f"Sets: " + ", ".join(f"{k} {v}" for k, v in s["set_status"].items()) + ". Levels: " + ", ".join(f"{k} {v}" for k, v in s["claim_levels"].items()) + ".")))
    cat = inp["catalogue"]
    B.append(("p", _fixed("Catalogue digest " + cat["digest"] + ". Entries used, by review status: " + ", ".join(f"{k} {v}" for k, v in cat["entries_used_by_review_status"].items()) + ".")))
    if inp["model"]:
        B.append(("p", _fixed("System model digest " + inp["model"]["digest"] + " (declared by its authors; not verified by this tool).")))
    if r["problems"]:
        B.append(("h", 2, _fixed("Notes on the inputs")))
        B.append(("list", [_fixed(p) for p in r["problems"]]))
    if r["verification"] is not None:
        v = r["verification"]["summary"]
        B.append(("p", _fixed(f"Verification against the recording: {v['verified']} verified, {v['mismatch']} mismatch, {v['not_checked']} not checked.")))
    if not r["units"]:
        B.append(("p", _fixed(FIXED["no_units"])))
    for n, u in enumerate(r["units"], 1):
        B.append(("h", 2, _d(f"Unit {n} of {len(r['units'])}: {', '.join(u['topics'])}")))
        B.append(("p", _fixed(f"Set status: {u['set_status']} ({u['reason']}). " + u["set_sentence"])))
        B.append(("p", _d("Symptoms in this unit: " + ", ".join(u["symptom_ids"]) + ". Categories: " + ", ".join(u["categories"]) + ".")))
        rows = [[_d(i["order"]), _d(i["entry_id"]), _d(i["tier"]), _d(f"{i['claim_level']} ({i['claim_name']})"), _d(i["satisfied_share"]), _d(i["review_status"])] for i in u["instances"]]
        if rows:
            B.append(("table", [_fixed(h) for h in ("Order", "Entry", "Tier", "Claim level", "Satisfied share", "Review status")], rows))
        for i in u["instances"]:
            B.append(("h", 3, _d(f"{i['entry_id']} (order {i['order']})")))
            B.append(("p", _fixed(i["mechanism"])))
            B.append(("badge", i["claim_level"], _fixed(f"Level {i['claim_level']}: {i['claim_sentence']}")))
            for c in i["cap_sentences"]:
                B.append(("p", _fixed(c)))
            B.append(("p", _fixed("What would raise it: " + i["would_raise"])))
            B.append(("list", [_fixed(f"{p['prediction_id']}: {p['text']} State: {p['state']}.") for p in i["predictions"]]))
            if i["ledger"]:
                B.append(("table", [_fixed(h) for h in ("Item", "Kind", "Stance", "Detail")], [[_d(x["item_id"]), _d(x["kind"]), _d(x["stance"]), _d(_detail(x))] for x in i["ledger"]]))
            else:
                B.append(("p", _fixed(FIXED["none"])))
            B.append(("p", _d("Bindings: " + ("; ".join(f"{k} = {', '.join(v) or 'not bound'} ({i['binding_sources'][k]})" for k, v in sorted(i["bindings"].items())) or "none") + ".")))
        if u["missing_evidence"]:
            B.append(("h", 3, _fixed("Evidence that would help")))
            B.append(("list", [_d(t) for t in u["missing_evidence"]]))
        if u["diagnostic_asks"]:
            B.append(("h", 3, _fixed("Diagnostic asks (nothing here runs a test)")))
            B.append(("table", [_fixed(h) for h in ("Entry", "Measurement", "Separates it from", "Open predictions", "Meaning")],
                      [[_d(a["entry_id"]), _d(a["measurement"] + ": " + a["description"]), _d(", ".join(a["would_discriminate_from"]) or "none"),
                        _d("; ".join(f"{p['prediction_id']} ({p['state']}): {p['text']}" for p in a["open_predictions"]) or "none"),
                        _fixed(a["meaning"])] for a in u["diagnostic_asks"]]))
        if u["propagation_facts"]:
            B.append(("h", 3, _fixed("Declared edges at this unit (from the localization step's coverage)")))
            B.append(("list", [_fixed(f"Edge {f['edge_id']}: {f['from_node']} to {f['to_node']}. {f['sentence']}") for f in u["propagation_facts"]]))
        if u["assumptions"]:
            B.append(("h", 3, _fixed("Assumptions (declared, not verified)")))
            B.append(("list", [_d(f"{a['entry_id']}: {a['slot']} from {a['source']}: {', '.join(a['topics'])}") for a in u["assumptions"]]))
    if r["sensitivity"]:
        B.append(("h", 2, _fixed("Sensitivity to the declared inputs")))
        B.append(("list", [_fixed(f"Without the {x['without']}: " + ("the verdicts did not change." if x["robust"] else "the verdicts changed for " + ", ".join(x["changed_units"]) + ".")) for x in r["sensitivity"]]))
    B.append(("h", 2, _fixed("The claim ladder")))
    B.append(("table", [_fixed(h) for h in ("Level", "Name", "Wording", "Reachable here")],
              [[_d(x["level"]), _d(x["name"]), _fixed(x["wording"] or "(none: not reachable)"), _d("yes" if x["reachable"] else "no")] for x in r["ladder"]]))
    B.append(("h", 2, _fixed("Limits")))
    B.append(("list", [_fixed(x) for x in r["limitations"]]))
    return B


# ------------------------------------------------------------------------------------------------ markdown

def _md(x):
    kind, v = _t(x)
    return md_escape(v, None) if kind == "f" else md_escape(v)


def render_markdown(r: dict) -> str:
    out: list[str] = []
    for b in build_blocks(r):
        t = b[0]
        if t == "h":
            out.append("#" * b[1] + " " + _md(b[2]) + "\n")
        elif t == "p":
            out.append(_md(b[1]) + "\n")
        elif t == "badge":
            out.append(f"**{_md(b[2])}**\n")
        elif t == "list":
            out.append("\n".join("- " + _md(x) for x in b[1]) + "\n")
        elif t == "table":
            out.append("| " + " | ".join(_md(h) for h in b[1]) + " |\n|" + "---|" * len(b[1]) + "\n" + "\n".join("| " + " | ".join(_md(c) for c in row) + " |" for row in b[2]) + "\n")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------ html

def _h(x):
    kind, v = _t(x)
    return html_escape(v, None) if kind == "f" else html_escape(v)


CSS = ("body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#1b1b1f;background:#fff}"
       "table{border-collapse:collapse;margin:.5rem 0 1rem;width:100%}th,td{border:1px solid #c8c8d0;padding:.25rem .5rem;text-align:left;vertical-align:top;overflow-wrap:anywhere}"
       "th{background:#eef0f4}.badge{display:inline-block;padding:.15rem .6rem;border-radius:.6rem;border:2px solid #44506b;background:#eef0f4;font-weight:600;margin:.25rem 0}"
       ".L0{border-style:dotted}.L1{border-style:dashed}.L2{border-style:solid}.L3{border-style:double}.L4{border-width:4px;border-style:solid}"
       "@media (prefers-color-scheme:dark){body{color:#e8e8ee;background:#16161a}th{background:#262a33}td,th{border-color:#444a58}.badge{background:#262a33;border-color:#9aa6c4}}")


def render_html(r: dict) -> str:
    out = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
           "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
           '<meta name="viewport" content="width=device-width, initial-scale=1">', f"<title>{html_escape(FIXED['title'])}</title>", f"<style>{CSS}</style></head><body>"]
    for b in build_blocks(r):
        t = b[0]
        if t == "h":
            out.append(f"<h{b[1]}>{_h(b[2])}</h{b[1]}>")
        elif t == "p":
            out.append(f"<p>{_h(b[1])}</p>")
        elif t == "badge":
            out.append(f'<p><span class="badge L{int(b[1])}">{_h(b[2])}</span></p>')
        elif t == "list":
            out.append("<ul>" + "".join(f"<li>{_h(x)}</li>" for x in b[1]) + "</ul>")
        elif t == "table":
            out.append("<table><thead><tr>" + "".join(f"<th>{_h(h)}</th>" for h in b[1]) + "</tr></thead><tbody>" +
                       "".join("<tr>" + "".join(f"<td>{_h(c)}</td>" for c in row) + "</tr>" for row in b[2]) + "</tbody></table>")
    out.append("</body></html>")
    return "\n".join(out) + "\n"


__all__ = ["render_markdown", "render_html", "build_blocks", "FIXED"]
