from tests.localization.tools.mutate import Mutant

L = "physicore_collector/localization/"
T = ("tests/localization/test_det.py", "tests/localization/test_det2.py", "tests/localization/test_sym.py", "tests/localization/test_pre.py", "tests/localization/test_dep.py",
     "tests/localization/test_con.py", "tests/localization/test_chn.py", "tests/localization/test_svc.py", "tests/localization/test_blk.py")

MUTANTS = [
    Mutant("det: canonical key without id", L + "symptoms.py", "return (s.onset.domain, s.onset.lower_ns, s.onset.upper_ns, s.symptom_id)", "return (s.onset.domain, s.onset.lower_ns, s.onset.upper_ns)", T),
    Mutant("det: canonical key without upper", L + "symptoms.py", "return (s.onset.domain, s.onset.lower_ns, s.onset.upper_ns, s.symptom_id)", "return (s.onset.domain, s.onset.lower_ns, s.symptom_id)", T),
    Mutant("det: cap keeps input order", L + "symptoms.py", "ordered = sorted(symptoms, key=canonical_key)", "ordered = list(symptoms)", T),
    Mutant("det: placements unsorted in id", L + "symptoms.py", "for p in sorted(placements)]", "for p in placements]", T),
    Mutant("det: layer sort key drops id", L + "precedence.py", "key=lambda t: (t[0], t[1], t[2]))", "key=lambda t: (t[0], t[1]))", T),
    Mutant("det: layer members unsorted", L + "precedence.py", "layers = [sorted(by_layer[k]) for k in sorted(by_layer)]", "layers = [list(by_layer[k]) for k in sorted(by_layer)]", T),
    Mutant("det: layers unordered", L + "precedence.py", "for k in sorted(by_layer)]", "for k in reversed(sorted(by_layer))]", T),
    Mutant("det: lanes unsorted", L + "precedence.py", "for d in sorted(per)]", "for d in per]", T),
    Mutant("det: blocker ids unsorted in lane", L + "precedence.py", "blockers = sorted(set(blocker_ids))", "blockers = list(dict.fromkeys(blocker_ids))", T),
    Mutant("det: json keys unsorted", L + "report.py", "return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + \"\\n\"", "return json.dumps(data, sort_keys=False, indent=2, ensure_ascii=False) + \"\\n\"", T),
    Mutant("det: canonical json keys unsorted", L + "report.py", "return json.dumps(obj, sort_keys=True, separators=(\",\", \":\"), ensure_ascii=False)", "return json.dumps(obj, sort_keys=False, separators=(\",\", \":\"), ensure_ascii=False)", T),
    Mutant("det: params hash keys unsorted", L + "service.py", "json.dumps(obj, sort_keys=True, separators", "json.dumps(obj, sort_keys=False, separators", T),
    Mutant("det: detector ids order kept", L + "service.py", "detector_ids = sorted(set(detector_ids)) if detector_ids else None", "detector_ids = list(detector_ids) if detector_ids else None", T),
    Mutant("det: symptoms listed unsorted", L + "service.py", "symptoms=sorted(syms, key=lambda s: s.symptom_id),", "symptoms=list(syms),", T),
    Mutant("det: out-edges unsorted", L + "dependencies.py", "lst.sort(key=lambda e: (e.dst, e.id))", "lst.sort(key=lambda e: e.id)", T),
    Mutant("det: out-edges not sorted by dst", L + "dependencies.py", "lst.sort(key=lambda e: (e.dst, e.id))", "pass", T),
    Mutant("det: edges not sorted in content", L + "dependencies.py", '"edges": sorted(edges, key=lambda e: e["id"])}', '"edges": list(edges)}', T),
    Mutant("det: nodes not sorted in content", L + "dependencies.py", '"nodes": sorted(node_ids),', '"nodes": list(node_ids),', T),
    Mutant("det: semantic hash keys unsorted", L + "dependencies.py", 'json.dumps(canon, sort_keys=True,', 'json.dumps(canon, sort_keys=False,', T),
    Mutant("det: semantic hash bindings unsorted", L + "dependencies.py", 'key=lambda b: (b["topic"], b["identity"], b["node"])),', 'key=lambda b: 0),', T),
    Mutant("det: endpoints unsorted", L + "dependencies.py", "return sorted(set(self.out) | set(self.inc))", "return list(set(self.out) | set(self.inc))", T),
    Mutant("det: ancestors unsorted", L + "dependencies.py", "        return sorted(seen)", "        return list(seen)", T),
    Mutant("det: blocker artifacts unsorted", L + "blockers.py", "arts = sorted({(a.artifact_ref, a.sha256) for a in artifacts}, key=lambda t: (t[0], t[1] or \"\"))", "arts = list({(a.artifact_ref, a.sha256) for a in artifacts})", T),
    Mutant("det: blocker set order", L + "blockers.py", "blockers=[seen[k] for k in sorted(seen)]", "blockers=[seen[k] for k in seen]", T),
    Mutant("det: caveat set order", L + "blockers.py", "caveats=[cseen[k] for k in sorted(cseen)]", "caveats=[cseen[k] for k in cseen]", T),
    Mutant("det: lanes of blocker unsorted", L + "blockers.py", "return {k: sorted(set(v)) for k, v in sorted(out.items())}", "return {k: list(dict.fromkeys(v)) for k, v in out.items()}", T),
    Mutant("det: readable identities unsorted", L + "output.py", "for k, v in sorted(s.identities.items()):", "for k, v in s.identities.items():", T),
    Mutant("det: link targets unsorted", L + "consistency.py", "targets = sorted(_descendants(graph, u) & bearing_set - {u})", "targets = list(_descendants(graph, u) & bearing_set - {u})", T),
    Mutant("det: bearing nodes unsorted", L + "consistency.py", "bearing = sorted(n for n in by_node if n in in_graph)", "bearing = [n for n in by_node if n in in_graph]", T),
    Mutant("det: lane order of links unsorted", L + "consistency.py", "lanes = sorted({ln for s in by_node[u] for ln in s.lanes} & {ln for s in by_node[v] for ln in s.lanes})", "lanes = list({ln for s in by_node[u] for ln in s.lanes} & {ln for s in by_node[v] for ln in s.lanes})", T),
    Mutant("det: blockers of analysis unsorted", L + "consistency.py", "return res, sorted(blk, key=lambda b: b.blocker_id)", "return res, blk", T),
]

# Justified equivalent mutant:
#  - "det: layer sort key drops id": entries tied on (lower, upper) are identical intervals; neither strictly precedes the other and both see the same settled
#    set, so their layers are equal whatever their processing order, and the result is regrouped through sorted layers and sorted member ids afterwards.
EQUIVALENT = ["det: layer sort key drops id"]
