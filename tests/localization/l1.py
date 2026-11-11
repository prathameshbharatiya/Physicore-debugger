"""Symptom-level (L1) builders: hand-made Symptom objects with declared onsets, no recording involved (TEST_PLAN 1)."""
from __future__ import annotations

from physicore_collector.localization.symptoms import EvidenceRef, Placement, Symptom, SourceCoverage

LOG = "mcap_log_time"


def sym(sid: str, lo: int, hi: int | None = None, *, lane: str = LOG, topic: str = "/t", truncated: bool = False, secondary: list[tuple[str, int, int]] | None = None,
        severity: str = "warning", category: str = "log_severity", identities: dict | None = None) -> Symptom:
    hi = lo if hi is None else hi
    return Symptom(
        symptom_id=sid, source="m3c_finding", source_id=f"f-{sid}", detector_or_rule="tierA.ros_log", category=category, topic=topic, node=topic,
        identities=identities or {}, severity=severity, severity_basis="source_declared_level",
        extent={"domain": lane, "lower_ns": lo, "upper_ns": hi}, onset={"domain": lane, "lower_ns": lo, "upper_ns": hi},
        onset_basis="received_message" if lo == hi else "silence_between_captured_messages",
        secondary_onsets=[Placement(lane=l, onset={"domain": l, "lower_ns": a, "upper_ns": b}, onset_basis="payload_stamp_value") for l, a, b in (secondary or [])],
        evidence=[EvidenceRef(artifact_ref="artifact:x.mcap", sha256="a" * 64, topic=topic, message_index=0, time_ns=lo, clock_domain=lane)],
        source_coverage=SourceCoverage(topic_coverage="complete"), truncated=truncated)
