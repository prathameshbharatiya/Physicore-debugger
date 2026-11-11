from tests.localization.tools.mutate import Mutant

P = "physicore_collector/localization/precedence.py"
T = "tests/localization/"
PRE = (T + "test_pre.py",)

MUTANTS = [
    Mutant("layer: strict < becomes <=", P, "        while heap and heap[0][0] < lo:", "        while heap and heap[0][0] <= lo:", PRE),
    Mutant("layer: count of predecessors style (never settles max)", P, "            settled = max(settled, heapq.heappop(heap)[1])", "            settled = heapq.heappop(heap)[1]", PRE),
    Mutant("layer: settles min instead of max", P, "            settled = max(settled, heapq.heappop(heap)[1])", "            settled = min(settled, heapq.heappop(heap)[1]) if settled >= 0 else heapq.heappop(heap)[1]", PRE),
    Mutant("layer: off by one", P, "        layer = settled + 1\n", "        layer = settled + 2\n", PRE),
    Mutant("layer: pushes lower bound instead of upper", P, "        heapq.heappush(heap, (up, layer))", "        heapq.heappush(heap, (lo, layer))", PRE),
    Mutant("layer: pushes layer+1", P, "        heapq.heappush(heap, (up, layer))", "        heapq.heappush(heap, (up, layer + 1))", PRE),
    Mutant("layer: sort by upper bound", P, "key=lambda t: (t[0], t[1], t[2]))\n    domains", "key=lambda t: (t[1], t[0], t[2]))\n    domains", PRE),
    Mutant("layer: mixed domains allowed", P, "    if len(domains) > 1:", "    if False:", PRE),
    Mutant("layer: duplicate ids allowed", P, "        if sid in out:", "        if False:", PRE),
    Mutant("layers: unsorted within a layer", P, "    layers = [sorted(by_layer[k]) for k in sorted(by_layer)]", "    layers = [by_layer[k] for k in sorted(by_layer)]", PRE),
    Mutant("layers: reversed layer order", P, "    layers = [sorted(by_layer[k]) for k in sorted(by_layer)]", "    layers = [sorted(by_layer[k]) for k in sorted(by_layer, reverse=True)]", PRE),
    Mutant("earliest: last layer instead of first", P, "    first = layers[0] if layers else []", "    first = layers[-1] if layers else []", PRE),
    Mutant("earliest: tie collapses to first member", P, "    if len(ids) > 1:\n        return Earliest(symptom_ids=ids,", "    if len(ids) > 1:\n        return Earliest(symptom_ids=ids[:1],", PRE),
    Mutant("earliest: tie not reported", P, "    if len(ids) > 1:", "    if len(ids) > 2:", PRE),
    Mutant("earliest: blockers ignored", P, "    if blockers or truncated[ids[0]]:", "    if truncated[ids[0]]:", PRE),
    Mutant("earliest: truncation ignored", P, "    if blockers or truncated[ids[0]]:", "    if blockers:", PRE),
    Mutant("earliest: truncation of any symptom blocks", P, "    if blockers or truncated[ids[0]]:", "    if blockers or any(truncated.values()):", PRE),
    Mutant("earliest: blockers dropped from the report", P, "    blockers = sorted(set(blocker_ids))", "    blockers = []", PRE),
    Mutant("earliest: blockers not de-duplicated", P, "    blockers = sorted(set(blocker_ids))", "    blockers = sorted(blocker_ids)", PRE),
    Mutant("earliest: empty lane claims defended", P, '        return Earliest(symptom_ids=[], defence="none_observed", blocker_ids=blockers, statement=NONE_OBSERVED_SENTENCE)', '        return Earliest(symptom_ids=[], defence="none_observed", blocker_ids=[], statement=NONE_OBSERVED_SENTENCE)', PRE),
    Mutant("earliest: wrong statement for not_defended", P, 'blocker_ids=blockers, statement=NOT_DEFENDED_SENTENCE)', 'blocker_ids=blockers, statement=SCOPE_SENTENCE)', PRE),
    Mutant("earliest: wrong statement for tied", P, 'defence="tied", blocker_ids=blockers, statement=TIED_SENTENCE)', 'defence="tied", blocker_ids=blockers, statement=SCOPE_SENTENCE)', PRE),
    Mutant("model: none_observed with symptoms allowed", P, '        if self.defence == "none_observed" and n != 0:', "        if False:", PRE),
    Mutant("model: tied with one symptom allowed", P, '        if self.defence == "tied" and n < 2:', "        if False:", PRE),
    Mutant("model: defended with several symptoms allowed", P, '        if self.defence in ("defended", "not_defended") and n != 1:', "        if False:", PRE),
    Mutant("model: defended with blockers allowed", P, '        if self.defence == "defended" and self.blocker_ids:', "        if False:", PRE),
    Mutant("model: defence vocabulary open", P, "        if v not in DEFENCE_STATES:", "        if False:", PRE),
    Mutant("lane: symptom_count wrong", P, "    return Lane(domain=domain, symptom_count=len(entries),", "    return Lane(domain=domain, symptom_count=len(entries) + 1,", PRE),
    Mutant("lanes: secondary placements ignored", P, "        for lane in s.lanes:", "        for lane in s.lanes[:1]:", PRE),
    Mutant("lanes: not sorted by domain", P, "    lanes = [build_lane(d, per[d], blockers_by_lane.get(d, [])) for d in sorted(per)]", "    lanes = [build_lane(d, per[d], blockers_by_lane.get(d, [])) for d in reversed(sorted(per))]", PRE),
    Mutant("lanes: blockers applied to every lane", P, "blockers_by_lane.get(d, [])) for d in sorted(per)]", "[b for bl in blockers_by_lane.values() for b in bl]) for d in sorted(per)]", PRE),
    Mutant("lanes: extra lanes ignored", P, "    for lane in extra_lanes:\n        per.setdefault(lane, [])", "    pass", PRE),
    Mutant("lanes: no cross-lane statement", P, "(NOT_COMPARED_ACROSS_LANES if len(lanes) > 1 else None)", "None", PRE),
    Mutant("lanes: cross-lane statement for one lane", P, "(NOT_COMPARED_ACROSS_LANES if len(lanes) > 1 else None)", "(NOT_COMPARED_ACROSS_LANES if len(lanes) > 0 else None)", PRE),
    Mutant("lanes: truncated flag lost", P, "            per.setdefault(lane, []).append((s.symptom_id, iv, s.truncated))", "            per.setdefault(lane, []).append((s.symptom_id, iv, False))", PRE),
    Mutant("lane model allows extra fields", P, "class Lane(Strict):", "class Lane(Strict):\n    model_config = {'extra': 'allow'}", PRE),
]


# Justified equivalent mutants (removed from the denominator):
#  - "layer: sort ignores id tiebreak": layer values do not depend on the processing order of symptoms with identical (lower, upper) bounds
#    (none of them can precede another), and the reported layer lists are sorted by id afterwards.
#  - "earliest: defended carries blockers": the defended branch is reached only when `blockers` is empty, so passing `blockers` there is the same list.
EQUIVALENT = ["layer: sort ignores id tiebreak (unstable)", "earliest: defended carries blockers"]
