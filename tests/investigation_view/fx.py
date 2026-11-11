"""Fixtures of the M3e tests: the shipped M3d example report, a generator of valid reports of any size, hostile-text helpers and a recording builder."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from physicore_collector.investigation_view.load import parse_report_bytes
from physicore_collector.investigation_view.model import build_view

ROOT = Path(__file__).resolve().parents[2]
import physicore_collector as _pc
SRC = Path(_pc.__file__).resolve().parents[1]      # the source tree under test (a mutated copy during mutation runs)
EXAMPLE_JSON = ROOT / "examples" / "localization" / "expected_output.json"
EXAMPLE_BAG = ROOT / "examples" / "localization" / "example_bag"
BASE_NS = 1_700_000_000_000_000_000


def example_dict() -> dict:
    return json.loads(EXAMPLE_JSON.read_text())


def to_bytes(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


def load(d: dict):
    res = parse_report_bytes(to_bytes(d))
    assert res.report is not None, res.problems
    return res


def view_of(d: dict | None = None) -> dict:
    res = load(d if d is not None else example_dict())
    return build_view(res.report, sha256=res.sha256, size=res.size)


def write_report(tmp_path: Path, d: dict, name: str = "report.json") -> Path:
    p = tmp_path / name
    p.write_bytes(to_bytes(d))
    return p


def gen(n: int, lanes: int = 1, *, m2_every: int = 0, width: int = 0, step: int = 1000, blockers: int = 0, base: int = BASE_NS) -> dict:
    """A valid report with n symptoms spread over `lanes` lanes, each lane a chain of consecutive links."""
    d = example_dict()
    s_t, l_t, c_t, comp_t, hyp_t = d["symptoms"][0], d["links"][0], d["chains"][0], d["components"][0], d["hypotheses"][0]
    names = ["mcap_log_time"] + [f"stamp_lane_{k}" for k in range(1, lanes)]
    syms, per_lane = [], {nm: [] for nm in names}
    for i in range(n):
        lane = names[i % lanes]
        lo = base + i * step
        s = copy.deepcopy(s_t)
        sid = f"s-{i:08d}"
        s.update(symptom_id=sid, source_id=f"f-{i:08d}", topic=f"/t{i % 50}", node=f"/t{i % 50}", source=("m2_finding" if m2_every and i % m2_every == 0 else "m3c_finding"),
                 onset={"domain": lane, "lower_ns": lo, "upper_ns": lo + width}, extent={"domain": lane, "lower_ns": lo, "upper_ns": lo + width})
        s["evidence"] = [{"artifact_ref": "artifact:bag_0.mcap", "sha256": d["evidence"][0]["sha256"], "message_index": i, "topic": s["topic"], "time_ns": lo, "clock_domain": lane}]
        s["chains"] = []
        syms.append(s)
        per_lane[lane].append(sid)
    links, chains, comps, hyps, lane_recs, topic_layers = [], [], [], [], [], []
    sym_by = {s["symptom_id"]: s for s in syms}
    for nm in names:
        ids = per_lane[nm]
        lane_links = []
        for a, b in zip(ids, ids[1:]):
            l = copy.deepcopy(l_t)
            sa, sb = sym_by[a], sym_by[b]
            l.update(link_id=f"l-{a[2:]}-{b[2:]}", lane=nm, from_symptom=a, to_symptom=b, from_node=sa["node"], to_node=sb["node"], from_onset=sa["onset"], to_onset=sb["onset"],
                     sep_min_ns=sb["onset"]["lower_ns"] - sa["onset"]["upper_ns"], sep_max_ns=sb["onset"]["upper_ns"] - sa["onset"]["lower_ns"])
            l["window_check"] = {**l["window_check"], "sep_min_ns": l["sep_min_ns"]}
            l["observed"] = [{"kind": "observed", "source_kind": sa["source"], "source_id": a}, {"kind": "observed", "source_kind": sb["source"], "source_id": b}]
            l["order"] = {**l["order"], "earlier": sa["onset"], "later": sb["onset"]}
            lane_links.append(l)
            h = copy.deepcopy(hyp_t)
            h.update(link_id=l["link_id"], from_onset=sa["onset"], to_onset=sb["onset"])
            hyps.append(h)
        links += lane_links
        if len(ids) >= 2:
            cid = f"ch-{nm}"
            ch = copy.deepcopy(c_t)
            ch.update(chain_id=cid, lane=nm, head=ids[0], symptom_ids=ids, nodes=[sym_by[i]["node"] for i in ids], component_id=f"c-{nm}",
                      steps=[{"from_symptom": a, "to_symptom": b, "link_ids": [f"l-{a[2:]}-{b[2:]}"]} for a, b in zip(ids, ids[1:])])
            chains.append(ch)
            for i in ids:
                sym_by[i]["chains"] = [cid]
            comp = copy.deepcopy(comp_t)
            comp.update(component_id=f"c-{nm}", symptom_ids=sorted(ids), lanes=[nm], link_ids=[l["link_id"] for l in lane_links])
            comps.append(comp)
        lane_recs.append({"domain": nm, "symptom_count": len(ids), "layers": [[i] for i in ids],
                          "earliest": {"symptom_ids": ids[:1], "defence": "defended" if ids else "none_observed", "blocker_ids": [],
                                       "statement": d["lanes"][0]["earliest"]["statement"] if ids else "No symptom was observed in this lane by the detectors that ran. This is a statement about those detectors, not about the system."}})
        topic_layers += [{"lane": nm, "topic": sym_by[i]["topic"], "layer": k, "symptom_ids": [i]} for k, i in enumerate(ids)]
    for s in syms:
        s.pop("chains", None)
    d["symptoms"] = syms
    d["links"], d["chains"], d["components"], d["hypotheses"], d["lanes"] = links, chains, comps, hyps, lane_recs
    d["chains_total"] = len(chains)
    d["localization"]["components"] = [{"component_id": c["component_id"], "lanes": [{"lane": c["lanes"][0], "locus_nodes": [sym_by[c["symptom_ids"][0]]["node"]], "frontier_nodes": [sym_by[c["symptom_ids"][-1]]["node"]]}]} for c in comps]
    d["localization"]["topic_layers"] = topic_layers
    d["coverage"]["links_total"] = len(links)
    d["coverage"]["chains_reported"] = len(chains)
    if blockers:
        b_t = {"blocker_id": "b-0", "code": "recording_incomplete", "scope": {}, "source": {"kind": "inventory_record", "ref": "artifact:bag_0.mcap", "field": "integrity"},
               "detail": "The recording or its inventory is incomplete or unverified; earlier symptoms may exist in evidence that was not read.",
               "artifacts": [{"artifact_ref": "artifact:bag_0.mcap", "sha256": d["evidence"][0]["sha256"]}],
               "observations": [{"artifact_ref": "artifact:bag_0.mcap", "message_index": 0, "topic": "/t0", "time_ns": base, "clock_domain": "mcap_log_time", "sha256": d["evidence"][0]["sha256"]}]}
        d["blockers"] = [{**copy.deepcopy(b_t), "blocker_id": f"b-{k}"} for k in range(blockers)]
    return d


def hostile_strings() -> list[str]:
    return ["</script><script>window.__pwn=1</script>", "<img src=x onerror=window.__pwn=1>", '"><svg onload=window.__pwn=1>', "javascript:window.__pwn=1", "<!--", "]]>",
            "](http://evil.example/x)", "[x](javascript:alert(1))", "![img](http://evil.example/p.png)", "<http://evil.example>", "http://evil.example/a", "www.evil.example", "a@evil.example",
            "# heading", "- item", "1. item", "> quote", "| a | b |", "---", "===", "`code`", "```", "**bold**", "_em_", "~~s~~", "&amp;", "&#60;script&#62;", "\\", "line1\nline2", "tab\there",
            "‮evil", "⁦x⁩", "zero​width", "﻿bom", "nul\x00", "esc\x1b[31m", " sep", " sep", "lone\ud800surrogate", "x" * 5000, "'; DROP TABLE x;--", "{{7*7}}", "${7*7}"]
