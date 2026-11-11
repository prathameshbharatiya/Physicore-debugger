from tests.localization.tools.mutate import Mutant

P = "physicore_collector/localization/consistency.py"
CON = ("tests/localization/test_con.py", "tests/localization/test_blk.py")

MUTANTS = [
    # truth table
    Mutant("cls: window boundary < instead of <=", P, "        if window_ns is None or sep_min <= window_ns:", "        if window_ns is None or sep_min < window_ns:", CON),
    Mutant("cls: window compared with max separation", P, "        if window_ns is None or sep_min <= window_ns:", "        if window_ns is None or sep_max <= window_ns:", CON),
    Mutant("cls: no window never outside", P, "        return \"outside_declared_window\", sep_min, sep_max\n    if y.precedes(x):", "        return \"consistent_with_propagation\", sep_min, sep_max\n    if y.precedes(x):", CON),
    Mutant("cls: reversed order consistent", P, "    if y.precedes(x):\n        return \"inconsistent_order\", sep_min, sep_max", "    if y.precedes(x):\n        return \"consistent_with_propagation\", sep_min, sep_max", CON),
    Mutant("cls: reversed order undetermined", P, "    if y.precedes(x):\n        return \"inconsistent_order\", sep_min, sep_max", "    if y.precedes(x):\n        return \"undetermined\", sep_min, sep_max", CON),
    Mutant("cls: overlap consistent", P, "    return \"undetermined\", sep_min, sep_max\n\n\ndef path_window", "    return \"consistent_with_propagation\", sep_min, sep_max\n\n\ndef path_window", CON),
    Mutant("cls: separations swapped", P, "    sep_min, sep_max = x.min_separation(y), x.max_separation(y)", "    sep_max, sep_min = x.min_separation(y), x.max_separation(y)", CON),
    Mutant("cls: touching counts as ordered", P, "    if x.precedes(y):\n        if window_ns", "    if x.upper_ns <= y.lower_ns:\n        if window_ns", CON),
    Mutant("win: sum becomes max", P, "        total += e.window_ns", "        total = max(total, e.window_ns)", CON),
    Mutant("win: missing window skipped instead of none", P, "        if e.window_ns is None:\n            return None", "        if e.window_ns is None:\n            continue", CON),
    Mutant("win: first edge only", P, "    for e in edges:\n        if e.window_ns is None:", "    for e in list(edges)[:1]:\n        if e.window_ns is None:", CON),
    # model
    Mutant("model: label not validated", P, "        if (label, smin, smax) != (self.label, self.sep_min_ns, self.sep_max_ns):", "        if False:", CON),
    Mutant("model: path kind unchecked", P, "        if not self.edge_ids or (self.path_kind == \"direct\") != (len(self.edge_ids) == 1):", "        if not self.edge_ids:", CON),
    Mutant("model: declared statements unchecked", P, "        if len(self.declared) != len(self.edge_ids) or [d.edge_id for d in self.declared] != self.edge_ids:", "        if False:", CON),
    Mutant("model: observed statements unchecked", P, "        if [o.source_id for o in self.observed] != [self.from_symptom, self.to_symptom]:", "        if False:", CON),
    Mutant("model: window check unchecked", P, "        if (self.window_check.window_ns, self.window_check.sep_min_ns) != (self.window_ns, self.sep_min_ns):", "        if False:", CON),
    Mutant("model: unverified note optional", P, "or \"declared_edge_unverified\" not in self.notes:", "or False:", CON),
    Mutant("model: order statement unchecked", P, "        if (self.order.relation, self.order.earlier, self.order.later, self.order.rule_id) != (want_rel, first, second, \"interval_order.strict_precedence\"):", "        if False:", CON),
    # links
    Mutant("link: inconsistent order statement not swapped", P, "    first, second = (ym, xm) if label == \"inconsistent_order\" else (xm, ym)", "    first, second = (xm, ym)", CON),
    Mutant("link: unobserved note never added", P, '(["path_via_unobserved_nodes"] if', "([] if", CON),
    Mutant("link: intermediate between ignores upper check", P, "        if iv.upper_ns < y.lower_ns:\n            out.append", "        if True:\n            out.append", CON),
    Mutant("link: between starts at lower bound", P, "range(bisect_right(lows, x.upper_ns), len(lst))", "range(bisect_right(lows, x.lower_ns), len(lst))", CON),
    Mutant("link: between not sorted", P, "    return sorted(out)\n\n\ndef _make_link", "    return out[::-1]\n\n\ndef _make_link", CON),
    Mutant("link: intermediate coverage assumed covered", P, "cov, dets = ctx.coverage.get(e.dst, (\"not_covered\", []))", "cov, dets = ctx.coverage.get(e.dst, (\"covered\", []))", CON),
    Mutant("link: intermediates list dropped", P, "        intermediates=inter, notes=notes)", "        intermediates=[], notes=notes)", CON),
    Mutant("link: path kind always via_nodes", P, "path_kind=\"direct\" if len(path) == 1 else \"via_nodes\"", "path_kind=\"via_nodes\"", CON),
    # analysis
    Mutant("ana: cross-lane pairs linked", P, "            lanes = sorted({ln for s in by_node[u] for ln in s.lanes} & {ln for s in by_node[v] for ln in s.lanes})", "            lanes = sorted({ln for s in by_node[u] for ln in s.lanes} | {ln for s in by_node[v] for ln in s.lanes})", CON),
    Mutant("ana: link cap ignored", P, "max(0, cap - len(links))", "10**9", CON),
    Mutant("ana: cap off by one", P, "max(0, cap - len(links))", "max(0, cap - 1 - len(links))", CON),
    Mutant("ana: suppressed count wrong", P, "    suppressed = total - len(links)", "    suppressed = total - len(links) - 1 if total > len(links) else 0", CON),
    Mutant("ana: total counts only first lane", P, "                    total += len(xs) * len(ys)", "                    total += len(xs) * len(ys) if lane == lanes[0] else 0", CON),
    Mutant("ana: undeclared pairs linked via every pair", P, "        targets = sorted(_descendants(graph, u) & bearing_set - {u})", "        targets = sorted(bearing_set - {u})", CON),
    Mutant("ana: ambiguous binding resolved", P, "        node, status = graph.node_for(s.topic, s.identities)", "        node, status = graph.node_for(s.topic, {})", CON),
    Mutant("ana: paths per pair ignored", P, "max_paths=int(lim[\"paths_per_pair\"])", "max_paths=10**9", CON),
    Mutant("ana: depth ignored", P, "max_depth=int(lim[\"path_depth\"])", "max_depth=10**6", CON),
    Mutant("ana: work budget unshared", P, "            pr = enumerate_paths(graph, u, v, max_depth=int(lim[\"path_depth\"]), max_paths=int(lim[\"paths_per_pair\"]), budget=budget)", "            pr = enumerate_paths(graph, u, v, max_depth=int(lim[\"path_depth\"]), max_paths=int(lim[\"paths_per_pair\"]), budget=WorkBudget(int(lim[\"path_work_limit\"])))", CON),
    Mutant("ana: partial pair used", P, "            if pr.work_exhausted:\n                work_hit = True\n                continue", "            if pr.work_exhausted:\n                work_hit = True", CON),
    Mutant("ana: pair cap blocker dropped", P, "    if pair_cap_pairs:", "    if False:", CON),
    Mutant("ana: depth blocker dropped", P, "    if depth_pairs:", "    if False:", CON),
    Mutant("ana: work blocker dropped", P, "    if work_hit:\n        blk.append", "    if False:\n        blk.append", CON),
    Mutant("ana: work blocker count wrong", P, "count=related - examined, artifacts=dep))", "count=related, artifacts=dep))", CON),
    Mutant("ana: link cap blocker dropped", P, "    if suppressed:\n        blk.append", "    if False:\n        blk.append", CON),
    Mutant("ana: status ignores bounded paths", P, "    status = \"bounded_paths\" if (work_hit or pair_cap_pairs or depth_pairs) else", "    status = \"bounded_paths\" if False else", CON),
    Mutant("ana: no-declared counts same-node pairs", P, "        diff_node_pairs = (s1 * s1 - sum(c * c for c in cn.values())) // 2", "        diff_node_pairs = (s1 * s1 - s1) // 2", CON),
    Mutant("ana: no-declared ignores relatedness", P, "        no_decl += diff_node_pairs - rel_pairs", "        no_decl += diff_node_pairs", CON),
    Mutant("ana: cross-lane counter zero", P, "            cross += sum(a * b for ku, a in gu.items() for kv, b in groups[v].items() if not (ku & kv))", "            cross += 0", CON),
    Mutant("ana: group lane test inverted", P, "cross += sum(a * b for ku, a in gu.items() for kv, b in groups[v].items() if not (ku & kv))", "cross += sum(a * b for ku, a in gu.items() for kv, b in groups[v].items() if (ku & kv))", CON),
    Mutant("ana: edge coverage always has symptoms", P, "status=\"endpoints_have_symptoms\" if a and b else \"no_symptom_on_endpoint\"", "status=\"endpoints_have_symptoms\"", CON),
    Mutant("ana: edge coverage either endpoint", P, "status=\"endpoints_have_symptoms\" if a and b else", "status=\"endpoints_have_symptoms\" if a or b else", CON),
    Mutant("ana: blockers lack dependency digest", P, "dep = [ArtifactDigest(artifact_ref=\"dependencies\", sha256=graph.sha256)]", "dep = []", CON),
    # coverage
    Mutant("cov: blind detectors cover", P, "COVERING_STATUSES = (\"findings_reported\", \"no_anomaly_observed\")", "COVERING_STATUSES = (\"findings_reported\", \"no_anomaly_observed\", \"insufficient_evidence\", \"blind_spot\")", CON),
    Mutant("cov: any topic suffices", P, "        ok = bool(topics) and all(t in covering for t in topics)", "        ok = bool(topics) and any(t in covering for t in topics)", CON),
    Mutant("cov: empty node is covered", P, "        ok = bool(topics) and all(t in covering for t in topics)", "        ok = all(t in covering for t in topics)", CON),
    Mutant("cov: bound topics ignored", P, "        node_topics.setdefault(b.node, set()).add(b.topic)", "        pass", CON),
    # components
    Mutant("comp: all labels join", P, "    used = [l for l in links if l.label == \"consistent_with_propagation\"]", "    used = [l for l in links]", CON),
    Mutant("comp: outside window joins", P, "    used = [l for l in links if l.label == \"consistent_with_propagation\"]", "    used = [l for l in links if l.label in (\"consistent_with_propagation\", \"outside_declared_window\")]", CON),
    Mutant("comp: no union", P, "        if ra != rb:\n            parent[max(ra, rb)] = min(ra, rb)", "        pass", CON),
    Mutant("comp: truncated member ignored", P, "or any(sym[m].truncated for m in members if m in sym)", "or False", CON),
    Mutant("comp: incomplete links ignored", P, "        bad = links_incomplete or", "        bad = False or", CON),
    Mutant("comp: blockers ignored", P, "        bad = links_incomplete or bool(bl) or", "        bad = links_incomplete or", CON),
    Mutant("comp: blocker ids dropped", P, "defence=\"not_defended\" if bad else \"defended_structure\", blocker_ids=bl))", "defence=\"not_defended\" if bad else \"defended_structure\", blocker_ids=[]))", CON),
    Mutant("comp: lanes ignored for blockers", P, "        bl = sorted({b for lane in lanes for b in bbl.get(lane, [])})", "        bl = sorted({b for v in bbl.values() for b in v})", CON),
    Mutant("comp: unsorted output", P, "    out.sort(key=lambda c: (c.symptom_ids[0], c.component_id))", "    out.sort(key=lambda c: c.component_id, reverse=True)", CON),
    Mutant("comp: model allows empty", P, "        if not self.symptom_ids or", "        if False or", CON),
    # blockers chain-level
    Mutant("blk: chain-level blocker downgrades lanes", "physicore_collector/localization/blockers.py", "    if b.code in CHAIN_LEVEL_CODES:\n        return []", "    if False:\n        return []", CON),
]

# Justified equivalent mutant (removed from the denominator):
#  - "ana: cross-lane pairs linked": taking the UNION of the two nodes' lanes only adds lanes in which one node has no symptom, and the product of an empty
#    list with anything is zero links and zero total.
EQUIVALENT = ["ana: cross-lane pairs linked"]
