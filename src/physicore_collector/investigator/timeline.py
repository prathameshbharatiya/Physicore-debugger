"""Evidence-linked incident timeline primitives (M3d foundation).

Events are ordered only within their own clock domain. This module deliberately does not
synchronize clocks, infer physical event time from receipt time, or promote an anomaly to a cause.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


EventClass = Literal["sensor", "controller", "actuator", "transport", "recorder", "operator", "anomaly", "transition", "divergence"]
TimeKind = Literal["observed", "inferred"]


@dataclass(frozen=True)
class EventSource:
    recording: str
    topic: str | None
    message_index: int | None
    artifact_sha256: str
    reference: str

    def __post_init__(self) -> None:
        if not self.recording or not self.artifact_sha256 or not self.reference:
            raise ValueError("recording, artifact_sha256 and reference are required")
        if self.message_index is not None and self.message_index < 0:
            raise ValueError("message_index must be non-negative")


@dataclass(frozen=True)
class TimelineEvent:
    event_id: str
    event_class: EventClass
    label: str
    time_ns: int | None
    clock_domain: str | None
    time_kind: TimeKind
    source: EventSource
    details: str = ""

    def __post_init__(self) -> None:
        if not self.event_id or not self.label:
            raise ValueError("event_id and label are required")
        if self.time_ns is None:
            if self.clock_domain is not None:
                raise ValueError("an event without a timestamp cannot declare a clock domain")
        elif not self.clock_domain:
            raise ValueError("a timestamped event must declare a clock domain")
        if self.time_kind == "inferred" and self.time_ns is None:
            raise ValueError("an inferred time requires an explicit timestamp")


@dataclass(frozen=True)
class TimelineGroup:
    clock_domain: str
    events: tuple[TimelineEvent, ...]


@dataclass(frozen=True)
class IncidentTimeline:
    groups: tuple[TimelineGroup, ...]
    unplaced: tuple[TimelineEvent, ...]
    cross_domain_ordering: str = "not_established"


def build_timeline(events: Iterable[TimelineEvent]) -> IncidentTimeline:
    """Build deterministic per-clock timelines; no ordering is asserted between clock domains."""
    materialized = tuple(events)
    ids = [event.event_id for event in materialized]
    if len(ids) != len(set(ids)):
        raise ValueError("event_id values must be unique")
    by_domain: dict[str, list[TimelineEvent]] = {}
    unplaced: list[TimelineEvent] = []
    for event in materialized:
        if event.time_ns is None or event.clock_domain is None:
            unplaced.append(event)
        else:
            by_domain.setdefault(event.clock_domain, []).append(event)
    groups = tuple(
        TimelineGroup(domain, tuple(sorted(group, key=lambda e: (e.time_ns, e.event_id))))
        for domain, group in sorted(by_domain.items())
    )
    return IncidentTimeline(groups, tuple(sorted(unplaced, key=lambda e: e.event_id)))


def context_window(group: TimelineGroup, event_id: str, *, before: int = 3, after: int = 3) -> tuple[TimelineEvent, ...]:
    """Return neighboring events in the SAME clock domain only."""
    if before < 0 or after < 0:
        raise ValueError("context sizes must be non-negative")
    indexes = [i for i, event in enumerate(group.events) if event.event_id == event_id]
    if not indexes:
        raise KeyError(event_id)
    i = indexes[0]
    return group.events[max(0, i - before): i + after + 1]
