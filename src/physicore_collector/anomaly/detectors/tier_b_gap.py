"""tierB.publishing_gap (DESIGN 6.1): an interval between consecutive captured messages (receipt time, mcap_log_time) strictly greater than
max(ceil(multiplier x lower-median interval), absolute floor). Descriptive only: it cannot tell capture loss from silence."""
from __future__ import annotations

from fractions import Fraction

from ..findings import Reason, Threshold
from ..runner import TopicCoverage
from ..severity import severity_for
from .base import Group, note
from .timing import TimingBase, ceil_fraction, lower_median


class PublishingGapDetector(TimingBase):
    detector_id = "tierB.publishing_gap"
    detector_version = "1.0.0"

    def finish(self, cov: TopicCoverage):
        P = self.params
        mult = Fraction(P["multiplier"])
        floor, min_iv = P["absolute_floor_ns"], P["min_intervals"]
        findings = []
        per_topic: list[Reason] = []
        any_insufficient = False
        # D7: boundary gaps are never evaluated; the result always says so
        per_topic.append(Reason(code="boundary_gaps_not_evaluated"))
        for topic in sorted(self.state):
            tt = self.state[topic]
            n = len(tt.t)
            ivs = [tt.t[i] - tt.t[i - 1] for i in range(1, n)]
            if len(ivs) < min_iv:
                any_insufficient = True
                per_topic.append(Reason(code="too_few_intervals", detail=f"{topic}: intervals={len(ivs)}, required={min_iv}", count=len(ivs)))
                continue
            B = lower_median(ivs)
            if B == 0:
                any_insufficient = True
                per_topic.append(Reason(code="degenerate_baseline_zero", detail=topic))
                continue
            G = max(ceil_fraction(mult * B), floor)
            flagged = [i for i, d in enumerate(ivs, start=1) if d > G]           # strict
            contaminated = len(flagged) * 4 >= len(ivs)
            arts = tt.artifacts()
            for i in flagged:
                d = tt.t[i] - tt.t[i - 1]
                key = ("gap", topic, tt.k[i - 1], tt.k[i])
                if not self.collector.admit(key):
                    continue
                a, b = tt.occ(i - 1, topic), tt.occ(i, topic)
                g = Group(key, a, b, 1, [tt.obs(i - 1, topic), tt.obs(i, topic)][: self.lim.max_retained_observations], set(arts))
                observed = {"interval_ns": d, "threshold_ns": G, "baseline_interval_ns": B, "baseline_interval_count": len(ivs),
                            "multiplier": P["multiplier"], "absolute_floor_ns": floor,
                            "earlier": {"artifact_ref": a.artifact_ref, "message_index": a.index, "log_time_ns": a.log_time_ns},
                            "later": {"artifact_ref": b.artifact_ref, "message_index": b.index, "log_time_ns": b.log_time_ns}}
                notes = [note("baseline_from_recording"), note("capture_loss_indistinguishable_from_silence")]
                if contaminated:
                    notes.append(note("baseline_contaminated_possible", {"flagged_intervals": len(flagged), "intervals": len(ivs)}))
                expl = (f"No message was captured between message index {a.index} (log_time {a.log_time_ns} ns) and message index {b.index} "
                        f"(log_time {b.log_time_ns} ns): the interval of {d} ns exceeds the threshold of {G} ns "
                        f"(multiplier {P['multiplier']} x baseline {B} ns, floor {floor} ns).")
                thr = Threshold(name="gap_threshold_ns", value=str(G), unit="ns", source="config")
                findings.append(self.base_finding(
                    g, cov, message_type=self.types.get(topic, ""), severity=severity_for("tierB.publishing_gap"), category="publishing_gap", field_path=None,
                    template=None, group_key=["gap", b.artifact_ref, b.index], observed=observed, explanation=expl, notes=notes,
                    times=(a.log_time_ns, b.log_time_ns), obs_truncated=len(g.obs) < 2, artifacts=arts, expected=B,
                    expected_source="recording_baseline", threshold=thr))
        self._pt = (per_topic, any_insufficient)
        return self.assemble(cov, findings)

    def decide(self, cov: TopicCoverage, n_findings: int):
        per_topic, any_insufficient = self._pt
        return self.decide_topics(cov, n_findings, per_topic, any_insufficient)
