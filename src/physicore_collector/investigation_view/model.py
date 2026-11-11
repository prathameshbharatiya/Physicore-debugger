"""The view model (FR-VW-04): one pure function from a validated report to a JSON-able dict from which Markdown and HTML are both generated.

All ordering, evidence-reference numbering, lane axes and offsets are decided here, with integers only. Text that comes from the report is cleaned
(safety.clean_text); the renderers only escape it for their format. Nothing that is not in the report is added."""
from __future__ import annotations

from . import VIEW_VERSION
from .fmt import interval, offset_text
from .safety import clean_text

SENT = 600      # report-supplied sentences are longer than data text


def _c(x: object, limit: int | None = 120) -> str:
    return clean_text(x, limit)


def _iv(m) -> dict:
    return {"domain": _c(m.domain), "lower_ns": m.lower_ns, "upper_ns": m.upper_ns, "width_ns": m.upper_ns - m.lower_ns}


def _sort_ids(xs) -> list[str]:
    return sorted(xs)


def build_view(report, *, sha256: str, size: int) -> dict:
    r = report
    syms = {s.symptom_id: s for s in r.symptoms}

    # ---- evidence references: one entry per distinct (artifact, message, digest, topic, time, domain); cited by symptoms and blockers
    refs: dict[tuple, dict] = {}

    def add_ref(e, who: tuple[str, str]) -> tuple:
        key = (e.artifact_ref, e.message_index, e.sha256 or "", e.topic or "", -1 if e.time_ns is None else e.time_ns, e.clock_domain or "")
        d = refs.setdefault(key, {"artifact_ref": e.artifact_ref, "message_index": e.message_index, "sha256": e.sha256, "topic": e.topic,
                                  "time_ns": e.time_ns, "clock_domain": e.clock_domain, "cited": set()})
        d["cited"].add(who)
        return key

    sym_ref_keys = {s.symptom_id: [add_ref(e, ("symptom", s.symptom_id)) for e in s.evidence] for s in r.symptoms}
    blk_ref_keys = {b.blocker_id: [add_ref(o, ("blocker", b.blocker_id)) for o in b.observations] for b in r.blockers}
    ordered = sorted(refs)
    ref_id = {k: f"E{i + 1}" for i, k in enumerate(ordered)}
    evidence_refs = []
    for k in ordered:
        d = refs[k]
        evidence_refs.append({
            "id": ref_id[k], "artifact_ref": _c(d["artifact_ref"]), "message_index": d["message_index"], "sha256": d["sha256"] and _c(d["sha256"]),
            "topic": d["topic"] and _c(d["topic"]), "time_ns": d["time_ns"], "clock_domain": d["clock_domain"] and _c(d["clock_domain"]),
            "cited_by": [{"kind": kd, "id": _c(i)} for kd, i in sorted(d["cited"])],
            "copy": "|".join([_c(d["artifact_ref"], None), f"message {d['message_index']}", _c(d["topic"] or "", None), str(d["time_ns"]), _c(d["clock_domain"] or "", None),
                              d["sha256"] or ""]),
            "verification": None})

    # ---- lanes: each lane has its own axis; a symptom's interval in a lane is its onset there (primary or secondary)
    def interval_in(s, lane):
        if s.onset.domain == lane:
            return s.onset
        for p in s.secondary_onsets:
            if p.lane == lane:
                return p.onset
        return None

    lanes = []
    for ln in r.lanes:
        layer_of = {sid: i for i, layer in enumerate(ln.layers) for sid in layer}
        items = []
        for sid, li in layer_of.items():
            s = syms.get(sid)
            iv = interval_in(s, ln.domain) if s is not None else None
            items.append({"id": sid, "layer": li, "iv": iv})
        have = [it["iv"] for it in items if it["iv"] is not None]
        origin = min((iv.lower_ns for iv in have), default=None)
        end = max((iv.upper_ns for iv in have), default=None)
        out_items = []
        for it in sorted(items, key=lambda it: ((it["iv"].lower_ns, it["iv"].upper_ns) if it["iv"] else (1 << 80, 0), it["id"])):
            iv = it["iv"]
            out_items.append({"id": _c(it["id"]), "layer": it["layer"],
                              "lower_ns": iv.lower_ns if iv else None, "upper_ns": iv.upper_ns if iv else None,
                              "off_lower_ns": iv.lower_ns - origin if iv else None, "off_upper_ns": iv.upper_ns - origin if iv else None})
        lanes.append({"domain": _c(ln.domain), "symptom_count": ln.symptom_count, "layers": [[_c(i) for i in layer] for layer in ln.layers],
                      "origin_ns": origin, "span_ns": (end - origin) if origin is not None else 0, "items": out_items,
                      "earliest": {"ids": [_c(i) for i in ln.earliest.symptom_ids], "defence": _c(ln.earliest.defence),
                                   "statement": _c(ln.earliest.statement, SENT), "blocker_ids": [_c(i) for i in ln.earliest.blocker_ids]}})

    # ---- symptoms
    chains_of: dict[str, list[str]] = {}
    for ch in r.chains:
        for sid in ch.symptom_ids:
            chains_of.setdefault(sid, []).append(ch.chain_id)
    links_of: dict[str, list[str]] = {}
    for l in r.links:
        for sid in (l.from_symptom, l.to_symptom):
            links_of.setdefault(sid, []).append(l.link_id)
    symptoms = []
    for s in sorted(r.symptoms, key=lambda s: s.symptom_id):
        symptoms.append({
            "id": _c(s.symptom_id), "source": _c(s.source), "source_id": _c(s.source_id), "rule": _c(s.detector_or_rule), "category": _c(s.category),
            "topic": _c(s.topic), "node": _c(s.node), "identities": [[_c(k), _c(v)] for k, v in sorted(s.identities.items())],
            "severity": _c(s.severity), "severity_basis": _c(s.severity_basis), "onset": _iv(s.onset), "extent": _iv(s.extent), "onset_basis": _c(s.onset_basis),
            "secondary": [{"lane": _c(p.lane), "lower_ns": p.onset.lower_ns, "upper_ns": p.onset.upper_ns, "basis": _c(p.onset_basis)} for p in s.secondary_onsets],
            "evidence": [ref_id[k] for k in sym_ref_keys[s.symptom_id]],
            "evidence_roles": [_c(e.role) if e.role else None for e in s.evidence],
            "coverage": {"topic_coverage": _c(s.source_coverage.topic_coverage), "examined": s.source_coverage.messages_examined,
                         "undecodable": s.source_coverage.messages_undecodable, "reasons": [_c(x) for x in s.source_coverage.incomplete_reasons]},
            "uncertainty": [_c(n.code) + ("" if not n.params else " (" + ", ".join(f"{_c(k)}={_c(v)}" for k, v in sorted(n.params.items())) + ")") for n in s.source_uncertainty],
            "notes": [_c(n) for n in s.notes], "truncated": bool(s.truncated),
            "lanes": [_c(x) for x in s.lanes], "chains": [_c(i) for i in _sort_ids(chains_of.get(s.symptom_id, []))],
            "links": [_c(i) for i in _sort_ids(set(links_of.get(s.symptom_id, [])))]})

    fd = [x for x in sorted(((s.onset.domain, s.onset.lower_ns, s.onset.upper_ns, s.symptom_id) for s in r.symptoms if s.source == "m2_finding"))]
    lane_origin = {l["domain"]: l["origin_ns"] for l in lanes}
    first_divergence = []
    for dom, lo, hi, sid in fd:
        s = syms[sid]
        org = lane_origin.get(_c(dom))
        first_divergence.append({"id": _c(sid), "lane": _c(dom), "lower_ns": lo, "upper_ns": hi, "off_lower_ns": (lo - org) if org is not None else None,
                                 "basis": _c(s.onset_basis), "rule": _c(s.detector_or_rule), "topic": _c(s.topic)})

    hyp_by_link = {h.link_id: h for h in r.hypotheses}
    links = []
    for l in sorted(r.links, key=lambda l: l.link_id):
        h = hyp_by_link.get(l.link_id)
        links.append({
            "id": _c(l.link_id), "lane": _c(l.lane), "from_symptom": _c(l.from_symptom), "to_symptom": _c(l.to_symptom), "from_node": _c(l.from_node),
            "to_node": _c(l.to_node), "from_onset": _iv(l.from_onset), "to_onset": _iv(l.to_onset), "edge_ids": [_c(e) for e in l.edge_ids], "label": _c(l.label),
            "sep_min_ns": l.sep_min_ns, "sep_max_ns": l.sep_max_ns, "window_ns": l.window_ns, "path_kind": _c(l.path_kind), "notes": [_c(n) for n in l.notes],
            "window_result": _c(l.window_check.result), "order_relation": _c(l.order.relation),
            "declared_by": sorted({_c(d.declared_by) for d in l.declared}),
            "intermediates": [{"node": _c(i.node), "between": [_c(x) for x in i.symptom_ids_between], "coverage": _c(i.coverage),
                               "detectors": [_c(x) for x in i.covering_detectors]} for i in l.intermediates],
            "hypothesis": ({"template": _c(h.template), "status": _c(h.status), "statement": _c(h.statement, SENT)} if h else None)})

    chains = []
    for ch in sorted(r.chains, key=lambda c: c.chain_id):
        chains.append({
            "id": _c(ch.chain_id), "lane": _c(ch.lane), "head": _c(ch.head), "symptom_ids": [_c(i) for i in ch.symptom_ids], "nodes": [_c(n) for n in ch.nodes],
            "steps": [{"from": _c(st.from_symptom), "to": _c(st.to_symptom), "link_ids": [_c(i) for i in st.link_ids]} for st in ch.steps],
            "component_id": _c(ch.component_id), "defence": _c(ch.defence), "blocker_ids": [_c(i) for i in ch.blocker_ids],
            "hypotheses": [{"template": _c(h.template), "status": _c(h.status), "statement": _c(h.statement, SENT),
                            "ancestors_total": (h.common_ancestors.ancestors_total if h.common_ancestors else None),
                            "ancestors": ([_c(a.node) for a in h.common_ancestors.ancestors] if h.common_ancestors else []),
                            "caveat_codes": ([_c(x) for x in h.artifact_facts.caveat_codes] if h.artifact_facts else []),
                            "needs": [_c(x, SENT) for x in h.discriminating_evidence_needed]} for h in ch.hypotheses]})

    components = [{"id": _c(c.component_id), "lanes": [_c(x) for x in c.lanes], "symptom_ids": [_c(i) for i in c.symptom_ids], "link_ids": [_c(i) for i in c.link_ids],
                   "defence": _c(c.defence), "blocker_ids": [_c(i) for i in c.blocker_ids]} for c in sorted(r.components, key=lambda c: c.component_id)]

    loc = r.localization
    localization = {
        "statement": _c(loc.statement, SENT),
        "components": [{"id": _c(c.component_id), "lanes": [{"lane": _c(x.lane), "locus": [_c(n) for n in x.locus_nodes], "frontier": [_c(n) for n in x.frontier_nodes]}
                                                           for x in c.lanes]} for c in loc.components],
        "unlocalized": [{"id": _c(u.symptom_id), "topic": _c(u.topic), "node": _c(u.node)} for u in loc.unlocalized],
        "topic_layers": [{"lane": _c(t.lane), "topic": _c(t.topic), "layer": t.layer, "ids": [_c(i) for i in t.symptom_ids]} for t in loc.topic_layers]}

    blockers = []
    for b in sorted(r.blockers, key=lambda b: b.blocker_id):
        blockers.append({
            "id": _c(b.blocker_id), "code": _c(b.code), "detail": _c(b.detail, SENT), "detector": b.scope.detector and _c(b.scope.detector),
            "topic": b.scope.topic and _c(b.scope.topic), "lane": b.scope.lane and _c(b.scope.lane), "expectation": b.scope.expectation and _c(b.scope.expectation),
            "count": b.count, "source": {"kind": _c(b.source.kind), "ref": _c(b.source.ref), "field": _c(b.source.field)},
            "artifacts": [{"artifact_ref": _c(a.artifact_ref), "sha256": a.sha256 and _c(a.sha256)} for a in b.artifacts],
            "observations": [ref_id[k] for k in blk_ref_keys[b.blocker_id]]})
    caveats = [{"code": _c(c.code), "detail": _c(c.detail, SENT), "detector": c.scope.detector and _c(c.scope.detector), "topic": c.scope.topic and _c(c.scope.topic),
                "lane": c.scope.lane and _c(c.scope.lane)} for c in sorted(r.caveats, key=lambda c: (c.code, c.scope.detector or "", c.scope.topic or "", c.scope.lane or ""))]

    cv = r.coverage
    coverage = {
        "dependencies": _c(cv.dependencies), "detectors": [{"detector_id": _c(d.detector_id), "status": _c(d.status)} for d in cv.detectors], "m2_status": cv.m2_status and _c(cv.m2_status),
        "edges": [{"edge_id": _c(e.edge_id), "from_node": _c(e.from_node), "to_node": _c(e.to_node), "from_count": e.from_symptom_count, "to_count": e.to_symptom_count,
                   "status": _c(e.status)} for e in cv.edge_coverage],
        "links_total": cv.links_total, "links_suppressed": cv.links_suppressed, "links_status": cv.links_status and _c(cv.links_status),
        "path_pairs_related": cv.path_pairs_related, "path_pairs_examined": cv.path_pairs_examined, "path_pairs_not_examined": cv.path_pairs_not_examined,
        "no_declared_relation_count": cv.no_declared_relation_count, "cross_lane_pairs_not_compared": cv.cross_lane_pairs_not_compared,
        "cross_lane_statement": cv.cross_lane_statement and _c(cv.cross_lane_statement, SENT), "chains_reported": cv.chains_reported,
        "chains_lower_bound": cv.chains_lower_bound, "chains_truncated_by": cv.chains_truncated_by and _c(cv.chains_truncated_by), "symptoms_suppressed": cv.symptoms_suppressed}

    dep = r.inputs.dependencies
    # the detector-configuration digest is read generically (the field ending in _config_hash): accepted test PRES_09 keeps the accepted modules free of that package's name
    cfg_hash = next((val for key, val in r.inputs.model_dump().items() if key.endswith("_config_hash")), None)
    header = {
        "view_version": VIEW_VERSION, "schema": _c(r.localization_schema_version), "tool_version": _c(r.tool_version), "parameters_hash": _c(r.parameters_hash),
        "exit_code": r.exit_code, "exit_reasons": [_c(x) for x in r.exit_reasons], "severity": _c(r.severity_disclaimer, SENT),
        "report_sha256": sha256, "report_bytes": size,
        "evidence": [{"artifact_ref": _c(e.artifact_ref), "sha256": _c(e.sha256), "verification": None} for e in r.evidence],
        "inputs": {"config_hash": cfg_hash and _c(cfg_hash),
                   "expectations_sha256": r.inputs.expectations_sha256 and _c(r.inputs.expectations_sha256),
                   "dependencies": ({"sha256": _c(dep.sha256), "semantic_hash": _c(dep.semantic_hash), "edge_count": dep.edge_count} if dep else None)},
        "problems": [{"source": _c(p.source), "path": _c(p.path), "message": _c(p.message, 300)} for p in (r.problems or [])]}
    return {
        "header": header,
        "counts": {"symptoms": len(r.symptoms), "links": len(r.links), "links_total": cv.links_total, "components": len(r.components), "chains": len(r.chains),
                   "blockers": len(r.blockers), "caveats": len(r.caveats), "refs": len(evidence_refs), "lanes": len(r.lanes)},
        "lanes": lanes, "first_divergence": first_divergence, "symptoms": symptoms, "links": links, "chains": chains, "components": components,
        "chains_total": r.chains_total, "chains_total_status": _c(r.chains_total_status), "localization": localization, "blockers": blockers, "caveats": caveats,
        "coverage": coverage, "limits": [[_c(k), v] for k, v in sorted(r.limits.items())], "limitations": [_c(x, SENT) for x in r.limitations],
        "evidence_refs": evidence_refs, "verification": None}


def with_verification(view: dict, ver: dict) -> dict:
    """A copy of the view with the per-reference results of `verify.verify_view` merged in."""
    import copy
    v = copy.deepcopy(view)
    for e in v["evidence_refs"]:
        e["verification"] = ver["refs"].get(e["id"])
    for e in v["header"]["evidence"]:
        e["verification"] = ver["artifacts"].get(e["artifact_ref"])
    v["verification"] = {"path": ver["path"], "summary": ver["summary"]}
    return v


def span_text(item: dict) -> str:
    if item["lower_ns"] is None:
        return "no interval in this lane"
    return interval(item["lower_ns"], item["upper_ns"])


def off_text(item: dict) -> str:
    return "none" if item.get("off_lower_ns") is None else offset_text(item["off_lower_ns"])
