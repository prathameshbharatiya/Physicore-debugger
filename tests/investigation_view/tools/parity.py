"""Independent parity verifier (PAR): checks that a generated Markdown or HTML view agrees with the localization report it came from.

It imports nothing from the investigation_view package: it parses the report JSON, the Markdown tables and the HTML tables / embedded data block with the
standard library only, and returns a list of mismatches (empty means agreement). Cap: tables are compared up to ROW_CAP rows, as the output states."""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser

ROW_CAP = 200


def _unesc(s: str) -> str:
    return re.sub(r"\\([!-/:-@\\[-`{-~])", r"\\1", s)


def _escape_md(s: str) -> str:
    """Escape punctuation for the Markdown table cell using the renderer's rule."""
    return re.sub(
        r"([!-/:-@\\[-`{-~])",
        lambda mm: "\\\\" + mm.group(1) if mm.group(1) not in "/,;?'\"%^$.-_" else mm.group(1),
        s,
    )


def md_tables(text: str):
    out, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("|"):
            cells = [c.strip() for c in re.split(r"(?<!\\\\)\\|", ln.strip())[1:-1]]
            if cur is None:
                cur = {"head": cells, "rows": [], "sep": False}
            elif not cur["sep"]:
                cur["sep"] = True
            else:
                cur["rows"].append([_unesc(c) for c in cells])
        elif cur is not None:
            out.append(cur); cur = None
    if cur is not None:
        out.append(cur)
    return out


def capj(items, sep):
    items = list(items)
    return sep.join(items) if len(items) <= 20 else sep.join(items[:20]) + f" ... and {len(items) - 20} more"


def iv(o):
    return f"{o['lower_ns']} ns" if o["lower_ns"] == o["upper_ns"] else f"{o['lower_ns']} to {o['upper_ns']} ns"


def expected_refs(rep):
    keys = set()
    for s in rep["symptoms"]:
        for e in s["evidence"]:
            keys.add((e["artifact_ref"], e["message_index"], e.get("sha256") or "", e.get("topic") or "", -1 if e.get("time_ns") is None else e["time_ns"], e.get("clock_domain") or ""))
    for b in rep["blockers"]:
        for e in b["observations"]:
            keys.add((e["artifact_ref"], e["message_index"], e.get("sha256") or "", e.get("topic") or "", -1 if e.get("time_ns") is None else e["time_ns"], e.get("clock_domain") or ""))
    return sorted(keys)


def _tab(tables, first_col, second_col=None):
    for t in tables:
        if t["head"][0] == first_col and (second_col is None or second_col in t["head"]):
            return t
    return None


def check_rows(problems, what, got_rows, want_rows):
    n = min(len(want_rows), ROW_CAP)
    if len(got_rows) != n:
        problems.append(f"{what}: {len(got_rows)} rows, expected {n}")
        return
    for i, (g, w) in enumerate(zip(got_rows, want_rows)):
        if g != w:
            problems.append(f"{what} row {i}: {g!r} != {w!r}")


def expected_tables(rep):
    syms = sorted(rep["symptoms"], key=lambda s: s["symptom_id"])
    sym_rows = [[s["symptom_id"], s["source"], s["detector_or_rule"], s["category"], s["topic"], s["node"], iv(s["onset"]), s["onset_basis"], s["severity"]] for s in syms]
    links = sorted(rep["links"], key=lambda l: l["link_id"])
    link_rows = [[l["link_id"], l["lane"], l["label"], f"{l['sep_min_ns']} ns to {l['sep_max_ns']} ns"] for l in links]
    chains = sorted(rep["chains"], key=lambda c: c["chain_id"])
    chain_rows = [[c["chain_id"], c["lane"], c["head"], capj(c["symptom_ids"], " then "), capj(c["nodes"], " then "), c["defence"]] for c in chains]
    blockers = sorted(rep["blockers"], key=lambda b: b["blocker_id"])
    blk_rows = [[b["blocker_id"], b["code"]] for b in blockers]
    refs = expected_refs(rep)
    ev_rows = [[f"E{i + 1}", k[0], str(k[1]), k[3] or "none", "none" if k[4] == -1 else f"{k[4]} ns", k[5] or "none", k[2] or "none"] for i, k in enumerate(refs)]
    return sym_rows, link_rows, chain_rows, blk_rows, ev_rows


def check_markdown(report_bytes: bytes, md: str) -> list[str]:
    rep = json.loads(report_bytes)
    problems: list[str] = []
    tabs = md_tables(md)
    sym_rows, link_rows, chain_rows, blk_rows, ev_rows = expected_tables(rep)
    t = _tab(tabs, "Symptom", "Detector or rule")
    check_rows(problems, "symptoms", [r[:9] for r in t["rows"]] if t else [], sym_rows) if (t or sym_rows) else None
    t = _tab(tabs, "Link", "Label")
    check_rows(problems, "links", [[r[0], r[1], r[5], r[6]] for r in t["rows"]] if t else [], link_rows) if (t or link_rows) else None
    t = _tab(tabs, "Chain", "Defence")
    check_rows(problems, "chains", [r[:6] for r in t["rows"]] if t else [], chain_rows) if (t or chain_rows) else None
    t = _tab(tabs, "Blocker", "Code")
    check_rows(problems, "blockers", [r[:2] for r in t["rows"]] if t else [], blk_rows[:ROW_CAP] if False else blk_rows) if (t or blk_rows) else None
    t = _tab(tabs, "Ref", "Artifact")
    check_rows(problems, "evidence", [r[:7] for r in t["rows"]] if t else [], ev_rows) if (t or ev_rows) else None
    m = re.search(r"Counts: (\\d+) symptom\\(s\\), (\\d+) link\\(s\\) reported of (\\d+), (\\d+) component\\(s\\), (\\d+) chain\\(s\\) reported, (\\d+) blocker\\(s\\), (\\d+) caveat\\(s\\), (\\d+) evidence reference\\(s\\)\\.", md)
    if not m:
        problems.append("counts line missing")
    else:
        got = tuple(int(x) for x in m.groups())
        want = (len(rep["symptoms"]), len(rep["links"]), rep["coverage"]["links_total"], len(rep["components"]), len(rep["chains"]), len(rep["blockers"]), len(rep["caveats"]), len(expected_refs(rep)))
        if got != want:
            problems.append(f"counts {got} != {want}")
    for ln in rep["lanes"]:
        escaped_domain = _escape_md(ln["domain"])
        if f"Clock domain: {escaped_domain}." not in md and ln["domain"] not in md.replace("\\\\", ""):
            problems.append(f"lane {ln['domain']} missing")
    if f"SHA-256 {hashlib.sha256(report_bytes).hexdigest()}" not in md.replace("\\\\", ""):
        problems.append("report digest missing")
    if rep["parameters_hash"] not in md:
        problems.append("parameters hash missing")
    for e in rep["evidence"]:
        if e["sha256"] not in md:
            problems.append("evidence digest missing")
    if f"Exit code {rep['exit_code']}:" not in md:
        problems.append("exit code missing")
    for lim in rep["limitations"]:
        if md.count("Limitation ") != len(rep["limitations"]):
            problems.append("limitation count"); break
    return problems


class _H(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.cur_table, self.row, self.cell, self.data_json, self._script, self.text = [], None, None, None, None, None, []
        self.in_caption = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self.cur_table = {"id": a.get("id"), "head": [], "rows": []}
        elif tag == "tr":
            self.row = []
        elif tag in ("th", "td"):
            self.cell = []
        elif tag == "script" and a.get("type") == "application/json":
            self._script = []

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.cell is not None:
            txt = "".join(self.cell)
            if self.row is not None:
                self.row.append((tag, txt))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.cur_table is not None:
            if self.row and self.row[0][0] == "th":
                self.cur_table["head"] = [c[1] for c in self.row]
            else:
                self.cur_table["rows"].append([c[1] for c in self.row])
            self.row = None
        elif tag == "table" and self.cur_table is not None:
            self.tables.append(self.cur_table); self.cur_table = None
        elif tag == "script" and self._script is not None:
            self.data_json = "".join(self._script); self._script = None

    def handle_data(self, d):
        if self._script is not None:
            self._script.append(d)
            return
        self.text.append(d)
        if self.cell is not None:
            self.cell.append(d)


def check_html(report_bytes: bytes, html: str) -> list[str]:
    rep = json.loads(report_bytes)
    problems: list[str] = []
    p = _H(); p.feed(html)
    sym_rows, link_rows, chain_rows, blk_rows, ev_rows = expected_tables(rep)
    by_id = {t["id"]: t for t in p.tables if t["id"]}
    def static(tid, proj, want, what):
        t = by_id.get(tid)
        if t is None:
            if want: problems.append(f"{what}: table missing")
            return
        check_rows(problems, what, [proj(r) for r in t["rows"]], want)
    static("tbl-symptoms", lambda r: r[:9], sym_rows, "html symptoms")
    static("tbl-links", lambda r: [r[0], r[1], r[5], r[6]], link_rows, "html links")
    static("tbl-chains", lambda r: r[:6], chain_rows, "html chains")
    static("tbl-evidence", lambda r: r[:7], ev_rows, "html evidence")
    bt = [t for t in p.tables if t["head"][:2] == ["Blocker", "Code"]]
    if blk_rows:
        check_rows(problems, "html blockers", [r[:2] for r in bt[0]["rows"]] if bt else [], blk_rows)
    if p.data_json is None:
        problems.append("data block missing")
    else:
        data = json.loads(p.data_json)
        if [s["id"] for s in data["symptoms"]] != [s["symptom_id"] for s in sorted(rep["symptoms"], key=lambda s: s["symptom_id"])]:
            problems.append("data block symptom ids")
        for s, r in zip(data["symptoms"], sorted(rep["symptoms"], key=lambda s: s["symptom_id"])):
            if s["onset"]["lower_ns"] != str(r["onset"]["lower_ns"]) or s["onset"]["upper_ns"] != str(r["onset"]["upper_ns"]):
                problems.append(f"data block onset of {r['symptom_id']}")
        if len(data["links"]) != len(rep["links"]) or len(data["chains"]) != len(rep["chains"]) or len(data["blockers"]) != len(rep["blockers"]):
            problems.append("data block counts")
        if [e["id"] for e in data["evidence_refs"]] != [r[0] for r in ev_rows]:
            problems.append("data block evidence ids")
        if data["header"]["report_sha256"] != hashlib.sha256(report_bytes).hexdigest():
            problems.append("data block digest")
    text = "".join(p.text)
    if hashlib.sha256(report_bytes).hexdigest() not in text:
        problems.append("report digest missing")
    if f"Exit code {rep['exit_code']}:" not in text:
        problems.append("exit code missing")
    return problems
