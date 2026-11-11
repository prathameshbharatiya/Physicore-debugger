"""tierB.rate_change (DESIGN 6.3): per-window message counts in receipt time (mcap_log_time). Fixed non-overlapping windows aligned at the topic's first
captured message; first and last windows always discarded, plus explicit trims; baseline = lower median of evaluated windows; exact rational ratios,
inclusive boundaries; each direction optional (at least one is required by the configuration)."""
from __future__ import annotations

from bisect import bisect_left
from fractions import Fraction

from ..findings import Reason, Threshold
from ..runner import TopicCoverage
from ..severity import severity_for
from .base import Group, note
from .timing import TimingBase, lower_median


class RateChangeDetector(TimingBase):
    detector_id = "tierB.rate_change"
    detector_version = "1.0.0"

    def finish(self, cov: TopicCoverage):
        P = self.params
        W = P["window_ns"]
        ts, te = P["trim_start_windows"], P["trim_end_windows"]
        lo = Fraction(P["low_ratio"]) if "low_ratio" in P else None
        hi = Fraction(P["high_ratio"]) if "high_ratio" in P else None
        findings = []
        per_topic: list[Reason] = []
        any_insufficient = False
        for topic in sorted(self.state):
            tt = self.state[topic]
            n = len(tt.t)
            if n == 0:
                any_insufficient = True
                per_topic.append(Reason(code="too_few_windows", detail=f"{topic}: no messages"))
                continue
            t0 = tt.t[0]
            K = (tt.t[n - 1] - t0) // W                    # index of the last window (it contains the last message)
            counts = [0] * (K + 1)                          # windows inside the captured span; empty ones count 0
            for x in tt.t:
                counts[(x - t0) // W] += 1
            first_eval, last_eval = 1 + ts, K - 1 - te      # window 0 and window K are always discarded
            ev_idx = list(range(first_eval, last_eval + 1)) if last_eval >= first_eval else []
            if len(ev_idx) < P["min_windows"]:
                any_insufficient = True
                per_topic.append(Reason(code="too_few_windows", detail=f"{topic}: evaluated={len(ev_idx)}, required={P['min_windows']}", count=len(ev_idx)))
                continue
            B = lower_median([counts[k] for k in ev_idx])
            if B < P["min_baseline_count"]:
                any_insufficient = True
                per_topic.append(Reason(code="baseline_count_too_small", detail=f"{topic}: baseline={B}, required={P['min_baseline_count']}"))
                continue
            arts = tt.artifacts()
            anomalous_total = 0
            for direction, ratio in (("low", lo), ("high", hi)):
                if ratio is None:
                    continue
                hits = [k for k in ev_idx if (counts[k] <= ratio * B if direction == "low" else counts[k] >= ratio * B)]   # inclusive
                anomalous_total += len(hits)
                runs: list[list[int]] = []
                for k in hits:
                    if runs and runs[-1][-1] == k - 1:
                        runs[-1].append(k)
                    else:
                        runs.append([k])
                for run in runs:
                    key = ("rate", topic, direction, run[0])
                    if not self.collector.admit(key):
                        continue
                    start, end = t0 + run[0] * W, t0 + (run[-1] + 1) * W
                    i_first = bisect_left(tt.t, start)                    # first message at/after the window start (the last window holds one)
                    i_last = bisect_left(tt.t, end) - 1                   # last message before the end of the run (may precede the run if empty)
                    a = tt.occ(i_first, topic, log_time_ns=start)
                    b = tt.occ(max(i_last, 0), topic, log_time_ns=t0 + run[-1] * W)
                    refs = [tt.obs(i_first, topic)] + ([tt.obs(i_last, topic)] if i_last > i_first else [])
                    g = Group(key, a, b, len(run), refs[: self.lim.max_retained_observations], set(arts))
                    cs = [counts[k] for k in run]
                    cap = self.lim.max_retained_observations
                    observed = {"direction": direction, "window_ns": W, "windows": len(run), "first_window_start_ns": start,
                                "last_window_start_ns": t0 + run[-1] * W, "min_count": min(cs), "max_count": max(cs), "baseline_count": B,
                                "evaluated_windows": len(ev_idx), "window_counts": cs[:cap]}
                    notes = [note("baseline_from_recording")]
                    expl = (f"{len(run)} consecutive window(s) of {W} ns held between {min(cs)} and {max(cs)} message(s), "
                            f"{'at most' if direction == 'low' else 'at least'} {P[direction + '_ratio']} x the baseline of {B} message(s) per window; "
                            f"first window starts at log_time {start} ns.")
                    thr = Threshold(name=f"{direction}_ratio", value=P[direction + "_ratio"], unit="ratio of baseline window count", source="config")
                    findings.append(self.base_finding(
                        g, cov, message_type=self.types.get(topic, ""), severity=severity_for("tierB.rate_change"), category="rate_change",
                        field_path=None, template=None, group_key=["rate", direction, a.artifact_ref, a.index, len(run)], observed=observed,
                        explanation=expl, notes=notes + [], times=(start, t0 + run[-1] * W if len(run) > 1 else None),
                        obs_truncated=len(g.obs) < len(refs), artifacts=arts, expected=B, expected_source="recording_baseline", threshold=thr))
            if anomalous_total * 4 >= len(ev_idx):
                for f in findings:
                    if f.topic == topic and not any(u.code == "baseline_contaminated_possible" for u in f.uncertainty):
                        f.uncertainty.append(note("baseline_contaminated_possible", {"anomalous_windows": anomalous_total, "evaluated_windows": len(ev_idx)}))
                        f.uncertainty.sort(key=lambda u: u.code)
        self._pt = (per_topic, any_insufficient)
        return self.assemble(cov, findings)

    def decide(self, cov: TopicCoverage, n_findings: int):
        per_topic, any_insufficient = self._pt
        return self.decide_topics(cov, n_findings, per_topic, any_insufficient)
