"""Test-only detector for the runner skeleton: records what it is fed and optionally decodes."""
from __future__ import annotations

from physicore_collector.anomaly.config import validate_config
from physicore_collector.anomaly.findings import DetectorResult
from physicore_collector.anomaly.runner import APPLICABLE, Applicability

MIN_CFG = {
    "anomaly_config_version": 1,
    "limits": {"max_retained_observations": 5, "max_text_bytes": 64, "max_findings_per_detector": 100},
    "detectors": {"tierB.non_finite": {"enabled": True, "topics": ["/t"]}},
}


def config(detectors=None):
    raw = {**MIN_CFG, "detectors": detectors or MIN_CFG["detectors"]}
    lc = validate_config(raw)
    assert lc.config is not None, lc.problems
    return lc.config


class Probe:
    """Implements the Detector protocol; `detector_id` must be a known id because results are validated."""
    detector_version = "0.0.0"

    def __init__(self, detector_id: str, topics: list[str], *, decode: bool = False):
        self.detector_id, self.needs_decode, self._topics = detector_id, decode, topics
        self.seen: list[tuple[str, int, int, int]] = []

    def candidates(self, topic_schemas, config):
        return set(self._topics)

    def bind(self, topic_schemas, ev, cfg):
        return Applicability(APPLICABLE, [t for t in self._topics if t in ev.messages])

    def on_message(self, view):
        self.seen.append((view.topic, view.log_time_ns, view.order, view.message_index))
        if self.needs_decode:
            view.decoded()

    def finish(self, cov):
        return DetectorResult(detector_id=self.detector_id, detector_version=self.detector_version,
                              status="no_anomaly_observed", messages_examined=cov.messages_examined), []
