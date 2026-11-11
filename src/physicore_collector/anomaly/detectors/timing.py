"""Shared machinery of the Tier B receipt-time detectors (gap, rate): topic selection from explicit config, compact per-topic timing store
(16 bytes per message), exact integer/rational helpers. Domain: mcap_log_time only. No decoding."""
from __future__ import annotations

from array import array
from fractions import Fraction

from ..access import MessageView
from ..config import AnomalyConfig
from ..findings import ObservationRef, Reason, hash_parameters
from ..runner import APPLICABLE, Applicability, TopicCoverage
from .base import Collector, DetectorCommon, Group, Ledger, Occ


def lower_median(values: list[int]) -> int:
    """Element at sorted index (n-1)//2: an integer, exactly reproducible (DESIGN 6 common definitions)."""
    s = sorted(values)
    return s[(len(s) - 1) // 2]


def ceil_fraction(f: Fraction) -> int:
    return -((-f.numerator) // f.denominator)


class TopicTimes:
    """log_time (8 B) and packed (segment order, message index) (8 B) per message, in the runner's deterministic order, plus the artifacts
    seen (every artifact holding a message of the topic is a contributing artifact of a baseline-dependent finding)."""

    def __init__(self) -> None:
        self.t = array("q")
        self.k = array("q")
        self.arts: dict[int, tuple[str, str]] = {}

    def add(self, view: MessageView) -> None:
        self.t.append(view.log_time_ns)
        self.k.append((view.order << 32) | view.message_index)
        self.arts.setdefault(view.order, (view.artifact_ref, view.sha256))

    def occ(self, i: int, topic: str, log_time_ns: int | None = None) -> Occ:
        order, index = self.k[i] >> 32, self.k[i] & 0xFFFFFFFF
        ref, sha = self.arts[order]
        return Occ(ref, sha, topic, index, order, self.t[i] if log_time_ns is None else log_time_ns)

    def obs(self, i: int, topic: str) -> ObservationRef:
        o = self.occ(i, topic)
        return ObservationRef(artifact_ref=o.artifact_ref, sha256=o.sha256, topic=topic, message_index=o.index, order=o.order, log_time_ns=o.log_time_ns)

    def artifacts(self) -> list[tuple[str, str]]:
        return sorted(set(self.arts.values()))


class TimingBase(DetectorCommon):
    needs_decode = False

    def candidates(self, topic_schemas, config: AnomalyConfig) -> set[str]:
        return {t for t in config.parameters(self.detector_id)["topics"] if t in topic_schemas}

    def bind(self, topic_schemas, ev, config) -> Applicability:
        self.params = config.parameters(self.detector_id)
        self.pparams_hash = hash_parameters(self.params)
        self.lim = config.limits
        self.ledger = Ledger()
        self.state: dict[str, TopicTimes] = {}
        self.collector = Collector(self.lim.max_findings_per_detector, self.lim.max_retained_observations)
        self.topic_reasons: list[Reason] = []
        self.types: dict[str, str] = {t: (sorted(n for n in names if n) or [""])[0] for t, names in topic_schemas.items()}
        usable, notrec, blind = [], [], []
        for t in self.params["topics"]:
            if t not in topic_schemas:
                notrec.append(Reason(code="topic_not_recorded", detail=t))
            elif len({n for n in topic_schemas[t]}) > 1:
                blind.append(Reason(code="mixed_types_on_topic", detail=t))
            else:
                usable.append(t)
                self.state[t] = TopicTimes()
        if usable:
            self.topic_reasons = blind + notrec
            return Applicability(APPLICABLE, usable, [])
        if blind:
            return Applicability("blind_spot", [], blind + notrec)
        return Applicability("not_applicable", [], notrec)

    def on_message(self, view: MessageView) -> None:
        self.state[view.topic].add(view)

    def decide_topics(self, cov: TopicCoverage, n_findings: int, per_topic: list[Reason], any_insufficient: bool) -> tuple[str, list[Reason]]:
        reasons = list(self.topic_reasons) + per_topic
        if not cov.complete:
            reasons.append(Reason(code="recording_incomplete", detail=(cov.incomplete_reasons[0] if cov.incomplete_reasons else None)))
        if n_findings:
            return "findings_reported", reasons
        if any_insufficient or not cov.complete:
            return "insufficient_evidence", reasons
        if cov.messages_examined == 0:
            return "insufficient_evidence", reasons + [Reason(code="no_messages_on_in_scope_topic")]
        return "no_anomaly_observed", reasons
