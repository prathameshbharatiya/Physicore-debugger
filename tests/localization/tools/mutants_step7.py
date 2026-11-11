from tests.localization.tools.mutate import Mutant

P = "physicore_collector/localization/chains.py"
CH = ("tests/localization/test_chn.py",)
V = "physicore_collector/localization/vocabulary.py"

MUTANTS = [
    # DAG construction
    Mutant("dag: all labels join", P, '        if l.label == "consistent_with_propagation":\n            if l.from_onset', '        if True:\n            if l.from_onset', CH),
    Mutant("dag: outside window joins", P, '        if l.label == "consistent_with_propagation":\n            if l.from_onset', '        if l.label in ("consistent_with_propagation", "outside_declared_window"):\n            if l.from_onset', CH),
    Mutant("dag: cycle guard removed", P, "            if l.from_onset.upper_ns >= l.to_onset.lower_ns:", "            if False:", CH),
    Mutant("dag: cycle guard too weak", P, "            if l.from_onset.upper_ns >= l.to_onset.lower_ns:", "            if l.from_onset.upper_ns > l.to_onset.lower_ns:", CH),
    Mutant("dag: redundant via arc kept", P, "            if l.path_kind == \"via_nodes\" and any(", "            if False and any(", CH),
    Mutant("dag: via arc dropped always", P, "            if l.path_kind == \"via_nodes\" and any((l.from_symptom, z) in all_arcs and (z, l.to_symptom) in all_arcs\n                                                  for n in l.intermediates for z in n.symptom_ids_between):", "            if l.path_kind == \"via_nodes\":", CH),
    Mutant("dag: via arc dropped on one side only", P, "(l.from_symptom, z) in all_arcs and (z, l.to_symptom) in all_arcs", "(l.from_symptom, z) in all_arcs or (z, l.to_symptom) in all_arcs", CH),
    Mutant("dag: parallel links not merged", P, "d.arc_links.setdefault((l.from_symptom, l.to_symptom), []).append(l.link_id)", "d.arc_links[(l.from_symptom, l.to_symptom)] = [l.link_id]", CH),
    Mutant("dag: successor order by id only", P, "            lst.sort(key=lambda v: (d.key[v][1], d.key[v][0], v))\n        d.succ = outs", "            lst.sort()\n        d.succ = outs", CH),
    Mutant("dag: topological key uses lower bound", P, "d.key[sid] = (iv.upper_ns, iv.lower_ns, sid)", "d.key[sid] = (iv.lower_ns, iv.upper_ns, sid)", CH),
    Mutant("dag: sources include non-roots", P, "return [v for v in self.vertices if self.pred_count.get(v, 0) == 0]", "return [v for v in self.vertices]", CH),
    Mutant("dag: vertex order by id", P, "return sorted(self.key, key=lambda v: (self.key[v][1], self.key[v][0], v))", "return sorted(self.key)", CH),
    # counting
    Mutant("dp: sink counts zero", P, "f[v] = sum(f[w] for w in outs) if outs else 1", "f[v] = sum(f[w] for w in outs) if outs else 0", CH),
    Mutant("dp: max instead of sum", P, "f[v] = sum(f[w] for w in outs) if outs else 1", "f[v] = max(f[w] for w in outs) if outs else 1", CH),
    Mutant("dp: counts all vertices not sources", P, "    return sum(f[v] for v in dag.sources)", "    return sum(f[v] for v in dag.key)", CH),
    Mutant("dp: forward order", P, "for v in sorted(dag.key, key=lambda v: dag.key[v], reverse=True):", "for v in sorted(dag.key, key=lambda v: dag.key[v]):", CH),
    Mutant("cnt: size test off by one", P, "if size <= count_limit else None", "if size < count_limit else None", CH),
    Mutant("cnt: edges not counted", P, "size = sum(len(d.key) + d.arc_count for d in dags)", "size = sum(len(d.key) for d in dags)", CH),
    Mutant("cnt: total always computed", P, "if size <= count_limit else None", "if True else None", CH),
    # enumeration
    Mutant("enum: report limit off by one", P, "        if len(kept) >= limit:", "        if len(kept) > limit:", CH),
    Mutant("enum: peek removed (truncation never detected)", P, "        if len(kept) >= limit:\n            truncated_by = \"report_limit\"", "        if False:\n            truncated_by = \"report_limit\"", CH),
    Mutant("enum: work stop not reported", P, "    if truncated_by is None and state.stopped == \"work\":", "    if False:", CH),
    Mutant("enum: DP mismatch check removed", P, "    if total is not None and truncated_by is None and len(kept) != total:", "    if False:", CH),
    Mutant("iter: head visit free", P, "            if not budget.take():\n                state.stopped = \"work\"\n                return\n            path, idx = [head], [0]", "            path, idx = [head], [0]", CH),
    Mutant("iter: successor visit free", P, "                if not budget.take():\n                    state.stopped = \"work\"\n                    return\n                path.append(w)", "                path.append(w)", CH),
    Mutant("iter: head not marked done", P, "            state.done_heads.add((dag.lane, head))", "            pass", CH),
    Mutant("iter: every head marked done", P, "            state.done_heads.add((dag.lane, head))", "            state.done_heads.add((dag.lane, head))\n        state.done_heads.update((dag.lane, h) for h in dag.sources)", CH),
    Mutant("iter: non-sink yields", P, "                if w not in dag.succ:\n                    yield", "                if True:\n                    yield", CH),
    Mutant("iter: stops after first successor", P, "                idx[-1] = i + 1\n                w = outs[i]", "                idx[-1] = len(outs)\n                w = outs[i]", CH),
    # blockers and defence
    Mutant("blk: truncation blocker dropped", P, "    if truncated_by == \"report_limit\":\n        new_blockers.append", "    if False:\n        new_blockers.append", CH),
    Mutant("blk: work blocker dropped", P, "    elif truncated_by == \"work_limit\":", "    elif False:", CH),
    Mutant("blk: suppressed count off by one", P, "suppressed = (total - len(kept)) if total is not None else None", "suppressed = (total - len(kept) - 1) if total is not None else None", CH),
    Mutant("blk: suppressed count estimated when unknown", P, "suppressed = (total - len(kept)) if total is not None else None", "suppressed = (total - len(kept)) if total is not None else 0", CH),
    Mutant("blk: unaffected components downgraded", P, "                if (d.lane, h) not in state.done_heads:\n                    affected.add(comp_of[h])", "                affected.add(comp_of[h])", CH),
    Mutant("blk: affected components not downgraded", P, "    comps = [c.model_copy(update={\"defence\": \"not_defended\"", "    comps = [c if False else c.model_copy(update={\"defence\": \"defended_structure\"", CH),
    Mutant("blk: truncation blocker id not on component", P, "\"blocker_ids\": sorted(set(c.blocker_ids) | set(tb_ids))", "\"blocker_ids\": sorted(set(c.blocker_ids))", CH),
    Mutant("blk: links incomplete ignored", P, "    links_incomplete = consistency.links_status != \"complete\"", "    links_incomplete = False", CH),
    Mutant("blk: chain-level blockers not listed", P, "bids = sorted(set(comp.blocker_ids) | (set(chain_level) if bad else set()))", "bids = sorted(set(comp.blocker_ids))", CH),
    Mutant("blk: chain defence ignores component", P, "        bad = comp.defence == \"not_defended\"", "        bad = False", CH),
    # hypotheses
    Mutant("hyp: link status all consistent", P, '"outside_declared_window": "excluded_by_declared_edge_timing",', '"outside_declared_window": "consistent_with",', CH),
    Mutant("hyp: inconsistent not excluded", P, '"inconsistent_order": "excluded_by_declared_edge_timing", "undetermined": "not_excluded"}', '"inconsistent_order": "not_excluded", "undetermined": "not_excluded"}', CH),
    Mutant("hyp: undetermined excluded", P, '"undetermined": "not_excluded"}', '"undetermined": "excluded_by_declared_edge_timing"}', CH),
    Mutant("hyp: template 2 may be excluded", P, '        if self.status != "not_excluded":\n            raise ValueError("templates 2 and 3 are always not_excluded")', "        if False:\n            raise ValueError('x')", CH),
    Mutant("hyp: free statement accepted", P, '        if self.statement != HYPOTHESIS_SENTENCES[(self.template, self.status)]:', "        if False:", CH),
    Mutant("hyp: template facts unchecked", P, '        if (self.template == T_COMMON) != (self.common_ancestors is not None) or (self.template == T_ARTIFACT) != (self.artifact_facts is not None):', "        if False:", CH),
    Mutant("hyp: evidence vocabulary unchecked", P, '        if bad or self.discriminating_evidence_needed != [e for e in DISCRIMINATING_EVIDENCE if e in self.discriminating_evidence_needed]:', "        if False:", CH),
    Mutant("hyp: chain accepts any template", P, '        if self.template not in (T_COMMON, T_ARTIFACT):', "        if False:", CH),
    Mutant("hyp: hypotheses unordered", P, "hypotheses=sorted([h2, h3], key=lambda h: h.template)", "hypotheses=[h3, h2]", CH),
    Mutant("hyp: order not validated", P, '        if names != sorted(names):', "        if False:", CH),
    Mutant("hyp: chain nodes are ancestors too", P, "if a not in chain_nodes]", "]", CH),
    Mutant("hyp: ancestors of every node not the head", P, "graph.ancestors(head.node) if graph is not None else []", "graph.ancestors(by_id[chain_head_last].node) if graph is not None else []" if False else "[]", CH),
    Mutant("hyp: before-head test <= ", P, "if s.onset_in(lane).precedes(hi)]", "if s.onset_in(lane).lower_ns <= hi.lower_ns]", CH),
    Mutant("hyp: coverage always covered", P, 'c, dets = cov.get(a, ("not_covered", []))', 'c, dets = cov.get(a, ("covered", []))', CH),
    Mutant("hyp: coverage evidence ask dropped", P, "any(a.coverage == \"not_covered\" for a in facts.ancestors)", "False", CH),
    Mutant("hyp: missing-segments ask always", P, '*(["a recording with the missing segments"] if recs else [])', '"a recording with the missing segments"', CH),
    Mutant("hyp: narrower-interval ask always", P, "if any_width else", "if True else", CH),
    Mutant("hyp: narrower-interval never", P, "if any_width else", "if False else", CH),
    Mutant("hyp: ancestor list bound ignored", P, "ancestors=items[:ANCESTORS_LISTED]", "ancestors=items", CH),
    Mutant("hyp: ancestor bound validation removed", P, "        if len(self.ancestors) > min(self.ancestors_total, ANCESTORS_LISTED):", "        if False:", CH),
    Mutant("hyp: caveat codes dropped", P, "artifact_facts=ArtifactFacts(caveat_codes=caveat_codes,", "artifact_facts=ArtifactFacts(caveat_codes=[],", CH),
    Mutant("hyp: link status relation unchecked", P, "        if LINK_STATUS.get(self.label) != self.status:", "        if False:", CH),
    # analysis model
    Mutant("ana: total present although unknown", P, "        if (self.chains_total_status == \"exact\") != (self.chains_total is not None):", "        if False:", CH),
    Mutant("ana: lower bound not validated", P, "or self.chains_lower_bound < self.chains_reported:", "or False:", CH),
    Mutant("ana: total below reported accepted", P, "        if self.chains_total is not None and self.chains_total < self.chains_reported:", "        if False:", CH),
    Mutant("ana: status always exact", P, '    status = "exact" if total is not None else "bounded_unknown"', '    status = "exact"', CH),
    Mutant("ana: lower bound not the enumerated count", P, "chains_lower_bound=len(chains),", "chains_lower_bound=len(chains) + 1,", CH),
    Mutant("chain: head not validated", P, '        if len(self.symptom_ids) < 2 or self.head != self.symptom_ids[0] or len(set(self.symptom_ids)) != len(self.symptom_ids):', "        if False:", CH),
    Mutant("chain: steps not validated", P, '        if [(s.from_symptom, s.to_symptom) for s in self.steps] != list(zip(self.symptom_ids, self.symptom_ids[1:])) or len(self.nodes) != len(self.symptom_ids):', "        if False:", CH),
    Mutant("chain: defended with blockers accepted", P, '        if self.defence == "defended_structure" and self.blocker_ids:', "        if False:", CH),
    # localization
    Mutant("loc: locus is the last layer", P, "locus_nodes=sorted({node[s] for s, k in lm.items() if k == 0})", "locus_nodes=sorted({node[s] for s, k in lm.items() if k == top})", CH),
    Mutant("loc: frontier is the first layer", P, "frontier_nodes=sorted({node[s] for s, k in lm.items() if k == top})", "frontier_nodes=sorted({node[s] for s, k in lm.items() if k == 0})", CH),
    Mutant("loc: unlocalized lists every symptom", P, "if graph is not None and s.node not in in_graph]", "if graph is not None]", CH),
    Mutant("loc: unlocalized without graph", P, "if graph is not None and s.node not in in_graph]", "if s.node not in in_graph]", CH),
    Mutant("loc: topic layers merge topics", P, "groups.setdefault((s.topic, lm[s.symptom_id]), []).append(s.symptom_id)", "groups.setdefault((\"*\", lm[s.symptom_id]), []).append(s.symptom_id)", CH),
    Mutant("loc: components not localized", P, "components=_localize(comps, by_id) if graph is not None else [],", "components=[],", CH),
    # vocabulary: template names stay approved
    Mutant("vocab: template-2 name no longer allowed", V, 'ALLOWED_TOKENS = ("consistent_with_propagation", "propagation_along_declared_edge", "common_or_independent_origin_not_excluded",', 'ALLOWED_TOKENS = ("consistent_with_propagation", "propagation_along_declared_edge",', CH + ("tests/localization/test_det_static.py",)),
]

# Justified equivalent mutant: the line only runs when the generator has already finished every head of the lane (then every head is done anyway);
# an early stop (work limit) returns before it, and a report-limit stop leaves the generator suspended, so the line never runs there either.
EQUIVALENT = ["iter: every head marked done"]
