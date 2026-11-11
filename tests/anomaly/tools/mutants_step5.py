from tests.anomaly.tools.mutate import Mutant

T = "tests/anomaly/"
GAP = (T + "test_tier_b_gap.py",)
RATE = (T + "test_tier_b_rate.py",)
G = "physicore_collector/anomaly/detectors/tier_b_gap.py"
R = "physicore_collector/anomaly/detectors/tier_b_rate.py"
TM = "physicore_collector/anomaly/detectors/timing.py"

MUTANTS = [
    # gap
    Mutant("gap: non-strict comparison (GAP-03)", G, "if d > G]", "if d >= G]", GAP),
    Mutant("gap: ceil replaced by floor (GAP-04)", G, "G = max(ceil_fraction(mult * B), floor)", "G = max(int(mult * B), floor)", GAP),
    Mutant("gap: floor ignored (GAP-05)", G, "G = max(ceil_fraction(mult * B), floor)", "G = ceil_fraction(mult * B)", GAP),
    Mutant("gap: floor wins always", G, "G = max(ceil_fraction(mult * B), floor)", "G = floor", GAP),
    Mutant("gap: multiplier ignored", G, "G = max(ceil_fraction(mult * B), floor)", "G = max(B, floor)", GAP),
    Mutant("gap: upper median (GAP-09)", TM, "return s[(len(s) - 1) // 2]", "return s[len(s) // 2]", GAP + RATE),
    Mutant("gap: min_intervals off by one (GAP-06)", G, "if len(ivs) < min_iv:", "if len(ivs) <= min_iv:", GAP),
    Mutant("gap: zero baseline falls back to floor (GAP-08)", G, "            if B == 0:\n                any_insufficient = True", "            if False:\n                any_insufficient = True", GAP),
    Mutant("gap: boundary reason dropped (GAP-11)", G, 'per_topic.append(Reason(code="boundary_gaps_not_evaluated"))', "pass", GAP),
    Mutant("gap: contamination threshold off (GAP-13)", G, "contaminated = len(flagged) * 4 >= len(ivs)", "contaminated = len(flagged) * 4 > len(ivs)", GAP),
    Mutant("gap: contamination never flagged (GAP-13)", G, "contaminated = len(flagged) * 4 >= len(ivs)", "contaminated = False", GAP),
    Mutant("gap: id covers only endpoints' artifacts (CS-04e)", G, "obs_truncated=len(g.obs) < 2, artifacts=arts,", "obs_truncated=len(g.obs) < 2, artifacts=[(a.artifact_ref, a.sha256), (b.artifact_ref, b.sha256)],", GAP),
    Mutant("gap: endpoints off by one (GAP-02)", G, "a, b = tt.occ(i - 1, topic), tt.occ(i, topic)", "a, b = tt.occ(i, topic), tt.occ(min(i + 1, n - 1), topic)", GAP),
    Mutant("gap: caveat dropped (GAP-18)", G, 'note("capture_loss_indistinguishable_from_silence")]', ']', GAP),
    Mutant("gap: baseline note dropped", G, 'notes = [note("baseline_from_recording"), ', 'notes = [', GAP),
    Mutant("gap: expected source wrong", G, 'expected_source="recording_baseline", threshold=thr))', 'expected_source="config", threshold=thr))', GAP),
    Mutant("gap: severity wrong (CS-06)", "physicore_collector/anomaly/severity.py", '"tierB": ("notice", "detector_policy")', '"tierB": ("warning", "detector_policy")', GAP + RATE),
    Mutant("gap: mixed types not blind (GAP-17)", TM, "            elif len({n for n in topic_schemas[t]}) > 1:", "            elif False:", GAP),
    Mutant("gap: cap ignored (GAP-19)", "physicore_collector/anomaly/detectors/base.py", "        if len(self.groups) >= self.max_findings:\n            self._suppressed.add(key)\n            return False", "        if False:\n            return False", GAP + RATE),
    Mutant("gap: incomplete recording ignored (GAP-15)", TM, "        if any_insufficient or not cov.complete:", "        if any_insufficient:", GAP + RATE),
    Mutant("gap: unsorted feed (GAP-16)", "physicore_collector/anomaly/runner.py", "    return sorted(msgs, key=lambda m: (m.log_time, m.order, m.index))", "    return list(msgs)", GAP),
    # rate
    Mutant("rate: first window not discarded (RATE-08)", R, "first_eval, last_eval = 1 + ts, K - 1 - te", "first_eval, last_eval = ts, K - 1 - te", RATE),
    Mutant("rate: last window not discarded (RATE-08)", R, "first_eval, last_eval = 1 + ts, K - 1 - te", "first_eval, last_eval = 1 + ts, K - te", RATE),
    Mutant("rate: start trim ignored (RATE-06b)", R, "first_eval, last_eval = 1 + ts, K - 1 - te", "first_eval, last_eval = 1, K - 1 - te", RATE),
    Mutant("rate: end trim ignored (RATE-07b)", R, "first_eval, last_eval = 1 + ts, K - 1 - te", "first_eval, last_eval = 1 + ts, K - 1", RATE),
    Mutant("rate: low boundary exclusive (RATE-04)", R, "counts[k] <= ratio * B if direction", "counts[k] < ratio * B if direction", RATE),
    Mutant("rate: high boundary exclusive (RATE-04)", R, "counts[k] >= ratio * B)]", "counts[k] > ratio * B)]", RATE),
    Mutant("rate: ratio compared inexactly", R, "counts[k] <= ratio * B if direction", "float(counts[k]) <= float(ratio * B) - 0.2 if direction", RATE),
    Mutant("rate: min_windows off by one (RATE-09)", R, "if len(ev_idx) < P[\"min_windows\"]:", "if len(ev_idx) <= P[\"min_windows\"]:", RATE),
    Mutant("rate: min_baseline off by one (RATE-10)", R, "if B < P[\"min_baseline_count\"]:", "if B <= P[\"min_baseline_count\"]:", RATE),
    Mutant("rate: no merging of consecutive windows (RATE-02)", R, "if runs and runs[-1][-1] == k - 1:", "if False:", RATE),
    Mutant("rate: merges across gaps", R, "if runs and runs[-1][-1] == k - 1:", "if runs:", RATE),
    Mutant("rate: low direction always on (RATE-16)", R, "                if ratio is None:\n                    continue", "                if ratio is None:\n                    ratio = Fraction(1, 2)", RATE),
    Mutant("rate: alignment at time zero not first message (RATE-18)", R, "            t0 = tt.t[0]", "            t0 = (tt.t[0] // W) * W", RATE),
    Mutant("rate: empty windows not counted (RATE-11)", R, "            counts = [0] * (K + 1)                          # windows inside the captured span; empty ones count 0", "            counts = [1] * (K + 1)", RATE),
    Mutant("rate: windows closed on the right edge", R, "counts[(x - t0) // W] += 1", "counts[min(K, max(0, -((t0 - x) // W) - (1 if (x - t0) % W == 0 and x != t0 else 0)))] += 1", RATE),
    Mutant("rate: contamination threshold (RATE-13)", R, "if anomalous_total * 4 >= len(ev_idx):", "if anomalous_total * 4 > len(ev_idx) * 4:", RATE),
    Mutant("rate: id covers only first message artifact (CS-04e)", R, "obs_truncated=len(g.obs) < len(refs), artifacts=arts,", "obs_truncated=len(g.obs) < len(refs), artifacts=[(a.artifact_ref, a.sha256)],", RATE),
    Mutant("rate: expected value wrong", R, "expected=B, expected_source", "expected=B + 1, expected_source", RATE),
    Mutant("rate: windows exact count off", R, "g = Group(key, a, b, len(run),", "g = Group(key, a, b, len(run) + 1,", RATE),
    Mutant("rate: upper median baseline (RATE-13)", TM, "return s[(len(s) - 1) // 2]", "return s[-1]", RATE + GAP),
]
