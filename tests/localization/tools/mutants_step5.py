from tests.localization.tools.mutate import Mutant

P = "physicore_collector/localization/blockers.py"
T = "tests/localization/"
BLK = (T + "test_blk.py",)

MUTANTS = [
    # lane mapping
    Mutant("lanes: lane scope ignored", P, "    if s.lane is not None:\n        return [s.lane] if s.lane in known else []", "    if False:\n        return []", BLK),
    Mutant("lanes: lane scope not checked against known lanes", P, "return [s.lane] if s.lane in known else []", "return [s.lane]", BLK),
    Mutant("lanes: detector blocker hits every lane", P, "    if s.detector is not None:\n        want", "    if False:\n        want", BLK),
    Mutant("lanes: detector blocker misses log lane", P, "d == LOG_TIME_DOMAIN or (s.detector", "False or (s.detector", BLK),
    Mutant("lanes: stamp lanes for every detector", P, "(s.detector == STAMP_DETECTOR and d.startswith(STAMP_PREFIX))", "d.startswith(STAMP_PREFIX)", BLK),
    Mutant("lanes: stamp lanes never", P, "(s.detector == STAMP_DETECTOR and d.startswith(STAMP_PREFIX))", "False", BLK),
    Mutant("lanes: unscoped blocker hits no lane", P, "    return known\n\n\ndef blockers_by_lane", "    return []\n\n\ndef blockers_by_lane", BLK),
    Mutant("by_lane: ids not de-duplicated", P, "{k: sorted(set(v)) for k, v in sorted(out.items())}", "{k: sorted(v) for k, v in sorted(out.items())}", BLK),
    Mutant("by_lane: ids unsorted", P, "{k: sorted(set(v)) for k, v in sorted(out.items())}", "{k: list(set(v)) for k, v in sorted(out.items())}", BLK),
    # inventory
    Mutant("inv: inspection state ignored", P, '    if inv.inspection.state != "complete":', "    if False:", BLK),
    Mutant("inv: integrity ignored", P, '    if inv.integrity.state in ("tampered", "unverifiable"):', "    if False:", BLK),
    Mutant("inv: standing gap codes block", P, 'g.kind == "known_gap" and g.code in INCOMPLETE_GAP_CODES', 'g.kind == "known_gap"', BLK),
    Mutant("inv: unreadable segment gap ignored", P, 'if g.kind == "segment_not_readable" or', "if False or", BLK),
    Mutant("inv: gap artifact digest dropped", P, "arts = [ArtifactDigest(artifact_ref=g.artifact_ref, sha256=digests.get(g.artifact_ref))] if g.artifact_ref else []", "arts = []", BLK),
    Mutant("inv: exit_code fallback removed", P, "    if not out and exit_code(inv) != 0:", "    if False:", BLK),
    Mutant("inv: fallback fires on clean evidence", P, "    if not out and exit_code(inv) != 0:", "    if not out:", BLK),
    # detector results
    Mutant("det: blind_spot status ignored", P, '        if r.status == "blind_spot" or blind:', "        if blind:", BLK),
    Mutant("det: not_applicable blind reasons block", P, 'if r.status not in ("not_applicable", "not_configured") else []', "else []", BLK),
    Mutant("det: insufficient evidence ignored", P, '        if r.status == "insufficient_evidence":', "        if False:", BLK),
    Mutant("det: undecodable count from wrong field", P, "count=x.count if x.count is not None else r.messages_undecodable, artifacts=arts))", "count=r.messages_undecodable + 1, artifacts=arts))", BLK),
    Mutant("det: undecodable blocker dropped", P, '            if x.code == "undecodable_messages":', '            if False:', BLK),
    Mutant("det: unparseable count lost", P, 'scope, count=x.count, artifacts=arts))', "scope, count=None, artifacts=arts))", BLK),
    Mutant("det: unparseable blocker dropped", P, '            elif x.code == "unparseable_messages":', "            elif False:", BLK),
    Mutant("det: truncation count off by one", P, "count=r.findings_truncated.suppressed_exact_count, artifacts=arts))", "count=r.findings_truncated.suppressed_exact_count - 1, artifacts=arts))", BLK),
    Mutant("det: truncation ignored", P, "        if r.findings_truncated is not None:", "        if False:", BLK),
    Mutant("det: not_configured becomes a blocker", P, '        if r.status == "not_configured":\n            cv.append(Caveat(code="detector_not_configured"', '        if r.status == "not_configured":\n            bl.append(make_blocker("detector_blind_spot", rec("status"), scope, artifacts=arts))\n            cv.append(Caveat(code="detector_not_configured"', BLK),
    Mutant("det: not_configured caveat dropped", P, '        if r.status == "not_configured":', "        if False:", BLK),
    Mutant("det: not_applicable caveat dropped", P, '        if r.status == "not_applicable":', "        if False:", BLK),
    Mutant("det: baseline caveats for every detector", P, "        if r.detector_id in BASELINE_DETECTORS and", "        if True and", BLK),
    Mutant("det: capture-loss caveat for every detector", P, '        if r.detector_id == "tierB.publishing_gap" and r.status in', '        if True and r.status in', BLK),
    Mutant("det: capture-loss caveat without a run", P, 'if r.detector_id == "tierB.publishing_gap" and r.status in ("findings_reported", "no_anomaly_observed"):', 'if r.detector_id == "tierB.publishing_gap":', BLK),
    Mutant("det: boundary caveat dropped", P, '            elif x.code == "boundary_gaps_not_evaluated":', "            elif False:", BLK),
    Mutant("det: source kind of undecodable wrong", P, 'SourceRecord(kind="detector_counter", ref=r.detector_id, field="reasons.undecodable_messages")', 'SourceRecord(kind="detector_result", ref=r.detector_id, field="reasons.undecodable_messages")', BLK),
    Mutant("det: blind-spot artifacts dropped", P, '            bl.append(make_blocker("detector_blind_spot", rec("reasons" if blind else "status"), scope, artifacts=arts))', '            bl.append(make_blocker("detector_blind_spot", rec("reasons" if blind else "status"), scope))', BLK),
    # symptoms
    Mutant("sym: truncated observations ignored", P, "        if s.truncated:", "        if False:", BLK),
    Mutant("sym: observation refs dropped", P, "clock_domain=e.clock_domain) for e in s.evidence]\n            out.append(make_blocker(\"observations_truncated\"", "clock_domain=e.clock_domain) for e in []]\n            out.append(make_blocker(\"observations_truncated\"", BLK),
    Mutant("sym: incomplete coverage ignored", P, '        if s.source_coverage.topic_coverage == "incomplete":', "        if False:", BLK),
    Mutant("sym: coverage blocker carries observations", P, 'BlockerScope(topic=s.topic), artifacts=_evidence_digests(s.evidence)))\n        if s.truncated', 'BlockerScope(topic=s.topic), artifacts=_evidence_digests(s.evidence), observations=[ObservationRef(artifact_ref=e.artifact_ref, message_index=e.message_index) for e in s.evidence]))\n        if s.truncated', BLK),
    # M2
    Mutant("m2: not-evaluated statuses ignored", P, '        if status in ("insufficient_evidence", "incomparable_timebases", "invalid_expectation"):', "        if False:", BLK),
    Mutant("m2: incomparable not a blocker", P, '("insufficient_evidence", "incomparable_timebases", "invalid_expectation")', '("insufficient_evidence", "invalid_expectation")', BLK),
    Mutant("m2: blind spot ignored", P, "        if er.blind_spot_count > 0 or er.blind_spot_unplaced:", "        if False:", BLK),
    Mutant("m2: unplaced blind spot ignored", P, "        if er.blind_spot_count > 0 or er.blind_spot_unplaced:", "        if er.blind_spot_count > 0:", BLK),
    Mutant("m2: blind spot count wrong", P, "src(\"blind_spots\"), scope, count=er.blind_spot_count,", "src(\"blind_spots\"), scope, count=er.blind_spot_count + 1,", BLK),
    Mutant("m2: observation index wrong", P, "message_index=p[1], time_ns=b.time_ns", "message_index=p[1] + 1, time_ns=b.time_ns", BLK),
    Mutant("m2: unresolvable observation invented", P, "                if p is None or p[0] not in paths:\n                    continue", "                if p is None:\n                    continue\n                if p[0] not in paths:\n                    obs.append(ObservationRef(artifact_ref='artifact:' + p[0], message_index=p[1]))\n                    continue", BLK),
    Mutant("m2: lane scope lost", P, "scope = BlockerScope(lane=er.clock_domain, expectation=er.expectation_id)", "scope = BlockerScope(expectation=er.expectation_id)", BLK),
    Mutant("m2: incompleteness ignored", P, "        if not er.evidence_complete and status != \"invalid_expectation\":", "        if False:", BLK),
    Mutant("m2: obs id parse wrong split", P, 'path, _, idx = obs_id[4:].rpartition("#")', 'path, _, idx = obs_id[4:].partition("#")', BLK),
    # limits, caveats, ids, models
    Mutant("cap: symptom cap blocker dropped", P, "    if suppressed_symptoms > 0:", "    if False:", BLK),
    Mutant("cap: fires at zero", P, "    if suppressed_symptoms > 0:", "    if suppressed_symptoms >= 0:", BLK),
    Mutant("cap: count wrong", P, 'count=suppressed_symptoms, artifacts=arts))', 'count=suppressed_symptoms + 1, artifacts=arts))', BLK),
    Mutant("caveat: validation-outstanding caveat dropped", P, '    cv.append(Caveat(code="real_recording_validation_outstanding", detail=CAVEAT_DETAIL["real_recording_validation_outstanding"]))', "    pass", BLK),
    Mutant("caveat: receipt-order caveat dropped", P, '    cv.append(Caveat(code="receipt_order_not_occurrence_order", detail=CAVEAT_DETAIL["receipt_order_not_occurrence_order"]))', "    pass", BLK),
    Mutant("build: duplicates kept by id", P, "        seen.setdefault(b.blocker_id, b)", "        seen[b.blocker_id + str(len(seen))] = b", BLK),
    Mutant("build: blockers unsorted", P, "blockers=[seen[k] for k in sorted(seen)]", "blockers=[seen[k] for k in reversed(sorted(seen))]", BLK),
    Mutant("id: count not in identity", P, 'ident = {"code": code, "scope": scope.model_dump(), "source": source.model_dump(), "count": count}', 'ident = {"code": code, "scope": scope.model_dump(), "source": source.model_dump()}', BLK),
    Mutant("id: scope not in identity", P, '"scope": scope.model_dump(), "source": source.model_dump(), "count": count}', '"source": source.model_dump(), "count": count}', BLK),
    Mutant("id: source not in identity", P, '"scope": scope.model_dump(), "source": source.model_dump(), "count": count}', '"scope": scope.model_dump(), "count": count}', BLK),
    Mutant("model: free-text detail accepted", P, '        if self.detail != DETAIL[self.code]:', "        if False:", BLK),
    Mutant("model: negative count accepted", P, "        if self.count is not None and self.count < 0:", "        if False:", BLK),
    Mutant("model: blocker code vocabulary open", P, "        if v not in BLOCKER_CODES:", "        if False:", BLK),
    Mutant("model: source kind vocabulary open", P, "        if v not in BLOCKER_SOURCE_KINDS:", "        if False:", BLK),
    Mutant("model: caveat free text accepted", P, '        if self.detail != CAVEAT_DETAIL[self.code]:', "        if False:", BLK),
    Mutant("model: caveat vocabulary open", P, "        if v not in CAVEAT_CODES:", "        if False:", BLK),
    Mutant("make: artifacts not sorted", P, "arts = sorted({(a.artifact_ref, a.sha256) for a in artifacts}, key=lambda t: (t[0], t[1] or \"\"))", "arts = list({(a.artifact_ref, a.sha256) for a in artifacts})", BLK),
    Mutant("make: observations not de-duplicated", P, "obs = sorted({(o.artifact_ref, o.message_index, o.sha256, o.topic, o.time_ns, o.clock_domain) for o in observations},", "obs = sorted([(o.artifact_ref, o.message_index, o.sha256, o.topic, o.time_ns, o.clock_domain) for o in observations],", BLK),
]

EQUIVALENT: list[str] = []
