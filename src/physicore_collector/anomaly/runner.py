"""Orchestration (DESIGN 2): bind each enabled detector to topics, feed every topic's messages ONCE in the deterministic order
(log_time, segment order, message index) to all applicable detectors, collect results and findings in a total order.
Decoding happens lazily in the shared MessageView, so a message is decoded at most once and never when no detector asks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from physicore_collector.investigator.observations import Evidence, Msg

from .access import DecodeCounter, MessageView
from .config import AnomalyConfig
from .findings import DetectorResult, Finding, Reason

APPLICABLE = "applicable"


@dataclass
class Applicability:
    status: str                                   # applicable | not_applicable | not_configured | blind_spot
    topics: list[str] = field(default_factory=list)
    reasons: list[Reason] = field(default_factory=list)


@dataclass
class TopicCoverage:
    complete: bool
    incomplete_reasons: list[str]
    messages_examined: int
    coverage: tuple[int, int] | None


class Detector(Protocol):
    detector_id: str
    detector_version: str
    needs_decode: bool

    def candidates(self, topic_schemas: dict[str, set[str | None]], config: AnomalyConfig) -> set[str]: ...
    def bind(self, topic_schemas: dict[str, set[str | None]], ev: Evidence, config: AnomalyConfig) -> Applicability: ...
    def on_message(self, view: MessageView) -> None: ...
    def finish(self, coverage: TopicCoverage) -> tuple[DetectorResult, list[Finding]]: ...


@dataclass
class RunOutcome:
    results: list[DetectorResult]
    findings: list[Finding]
    decode_counter: DecodeCounter
    messages_fed: int


def topic_schema_names(inv: Any) -> dict[str, set[str | None]]:
    """topic -> exact recorded schema names (None when a channel recorded none, e.g. JSON), from the inventory."""
    out: dict[str, set[str | None]] = {}
    for t in getattr(inv, "topics", []) or []:
        out.setdefault(t.topic, set()).add(t.schema_name or None)
    return out


def ordered(msgs: list[Msg]) -> list[Msg]:
    """The deterministic order (log_time, segment order, message index); never the loader's arrival order, never hash order."""
    return sorted(msgs, key=lambda m: (m.log_time, m.order, m.index))


def wanted_topics(detectors: list[Detector], schemas: dict[str, set[str | None]], config: AnomalyConfig) -> set[str]:
    """Topics to load: what each ENABLED detector may need, decided from the inventory alone (before any message is read)."""
    return {t for d in detectors if config.enabled(d.detector_id) for t in d.candidates(schemas, config)}


def _not_configured(d: Detector) -> DetectorResult:
    return DetectorResult(detector_id=d.detector_id, detector_version=d.detector_version, status="not_configured")


def run(inv: Any, ev: Evidence, config: AnomalyConfig, detectors: list[Detector], *, track_decodes: bool = True) -> RunOutcome:
    schemas = topic_schema_names(inv)
    counter = DecodeCounter(track_each=track_decodes)
    results: list[DetectorResult] = []
    findings: list[Finding] = []
    bound: list[tuple[Detector, Applicability]] = []
    for d in sorted(detectors, key=lambda d: d.detector_id):       # config/registration order never matters
        if not config.enabled(d.detector_id):
            results.append(_not_configured(d))
            continue
        ap = d.bind(schemas, ev, config)
        if ap.status == APPLICABLE:
            bound.append((d, ap))
        else:
            results.append(DetectorResult(detector_id=d.detector_id, detector_version=d.detector_version, status=ap.status,
                                          topics=sorted(ap.topics), reasons=list(ap.reasons)))
    by_topic: dict[str, list[Detector]] = {}
    for d, ap in bound:
        for t in sorted(set(ap.topics)):
            by_topic.setdefault(t, []).append(d)
    fed = 0
    examined: dict[str, int] = {d.detector_id: 0 for d, _ in bound}
    for topic in sorted(by_topic):
        for m in ordered(ev.messages.get(topic, [])):
            view = MessageView(m, ev, counter)
            fed += 1
            for d in by_topic[topic]:
                examined[d.detector_id] += 1
                d.on_message(view)
    for d, _ap in bound:
        cov = TopicCoverage(ev.complete, list(ev.incomplete_reasons), examined[d.detector_id], ev.coverage)
        res, fs = d.finish(cov)
        results.append(res)
        findings.extend(fs)
    return RunOutcome(sorted(results, key=lambda r: r.detector_id), findings, counter, fed)
