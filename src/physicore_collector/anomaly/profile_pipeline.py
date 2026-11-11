"""Evidence-backed execution adapter for robot-specific numeric signal profiles.

The adapter reuses the inventory's integrity checks, the Investigator's decoder and explicit
signal registry normalization. It emits profile events plus a per-clock-domain timeline; it
never orders events across clocks or treats an event as a diagnosis.
"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator.observations import load_evidence
from physicore_collector.investigator.engine import _Ctx
from physicore_collector.investigator.signal_registry import load_signal_registry
from physicore_collector.investigator.timeline import EventSource, TimelineEvent, build_timeline

from .signal_profile import evaluate_signal_profile, load_signal_profile
from .signal_rules import SignalPoint


def run_signal_profile_detection(path: str | Path, profile_path: str | Path, registry_path: str | Path) -> dict[str, Any]:
    """Decode, normalize and evaluate every declared signal; return JSON-ready events and timeline."""
    profile = load_signal_profile(profile_path)
    registry = load_signal_registry(Path(registry_path))
    if registry.robot_family != profile.robot_family:
        raise ValueError("signal profile robot_family does not match the signal registry")
    definitions = {d.canonical_name: d for d in registry.definitions}
    for name, spec in profile.signals.items():
        definition = definitions.get(name)
        if definition is None:
            raise ValueError(f"profile signal {name!r} is not declared as a canonical signal in the registry")
        if definition.unit != spec.unit:
            raise ValueError(f"profile signal {name!r} unit does not match the registry normalized unit")
    inv = inspect_evidence(path)
    topics = {d.topic for d in registry.definitions if d.canonical_name in profile.signals}
    ev = load_evidence(inv, topics)
    if ev.blocked:
        raise ValueError(ev.blocked)
    ctx = _Ctx(ev, inv, registry)
    samples: dict[str, list[SignalPoint]] = {}
    sample_topics: dict[str, str] = {}
    message_index = {}
    for topic, messages in ev.messages.items():
        for msg in messages:
            message_index[(msg.artifact_ref, msg.index)] = (topic, msg)
    for name in profile.signals:
        definition = definitions[name]
        points = []
        for sample in ctx.samples(definition.topic, name, "log_time", None):
            if sample.quality != "ok" or not isinstance(sample.value, Decimal) or sample.ts is None or sample.normalized_unit is None:
                continue
            source_ref = f"{sample.artifact_ref}#{sample.seq[1]}"
            points.append(SignalPoint(sample.ts, "mcap_log_time", sample.value, sample.normalized_unit, source_ref))
            sample_topics[source_ref] = definition.topic
        samples[name] = points
    result = evaluate_signal_profile(profile, samples)
    artifact_paths = {a.ref_id: a.relative_path for a in inv.artifacts}
    timeline_events = []
    event_rows = []
    for index, wrapped in enumerate(result.events, 1):
        event = wrapped.event
        refs = []
        for ref in event.source_refs:
            artifact_ref, raw_index = ref.rsplit("#", 1)
            msg_info = message_index.get((artifact_ref, int(raw_index)))
            if msg_info is None:
                continue
            topic, msg = msg_info
            refs.append({"artifact_ref": artifact_ref, "artifact_path": artifact_paths.get(artifact_ref, artifact_ref),
                         "sha256": msg.segment_sha256, "topic": topic, "message_index": int(raw_index), "reference": ref})
        event_id = f"sp-{index:06d}"
        event_rows.append({"event_id": event_id, "rule": event.rule, "start_ns": event.start_ns, "end_ns": event.end_ns,
                           "clock_domain": event.clock_domain, "unit": event.unit, "observed": list(event.observed),
                           "source_refs": refs, "persistent": event.persistent, "explanation": event.explanation,
                           "expected_transition_overlap": wrapped.expected_transition_overlap})
        if refs:
            primary = refs[0]
            source = EventSource(primary["artifact_path"], primary["topic"], primary["message_index"],
                                 primary["sha256"], primary["reference"])
            timeline_events.append(TimelineEvent(event_id, "anomaly", event.rule, event.start_ns, event.clock_domain,
                                                 "observed", source, event.explanation))
    timeline = build_timeline(timeline_events)
    return {
        "schema_version": "1.0", "robot_family": result.robot_family, "profile_version": result.profile_version,
        "evidence_complete": ev.complete, "incomplete_reasons": list(ev.incomplete_reasons),
        "signals_evaluated": list(result.signals_evaluated), "missing_signals": list(result.missing_signals),
        "events": event_rows,
        "timeline": {"groups": [{"clock_domain": g.clock_domain, "events": [
            {"event_id": e.event_id, "event_class": e.event_class, "label": e.label, "time_ns": e.time_ns,
             "clock_domain": e.clock_domain, "source": {"recording": e.source.recording, "topic": e.source.topic,
             "message_index": e.source.message_index, "artifact_sha256": e.source.artifact_sha256,
             "reference": e.source.reference}, "details": e.details} for e in g.events]} for g in timeline.groups],
            "unplaced": [], "cross_domain_ordering": timeline.cross_domain_ordering},
        "limitations": ["Events are observations under the supplied profile, not diagnoses.",
                        "Only mcap_log_time is used by this adapter; no payload-stamp clock is substituted.",
                        "Signal values must be decodable and explicitly normalizable through the supplied registry.",
                        "Cross-clock ordering is not established."]
    }


def render_signal_profile_text(report: dict[str, Any]) -> str:
    lines = [f"Numeric signal profile ({report['robot_family']}): {len(report['events'])} event(s)",
             f"Signals evaluated: {', '.join(report['signals_evaluated']) or 'none'}",
             f"Missing signals: {', '.join(report['missing_signals']) or 'none'}",
             f"Evidence complete: {'yes' if report['evidence_complete'] else 'no'}"]
    for event in report["events"]:
        lines.append(f"  {event['event_id']} [{event['rule']}] {event['start_ns']}..{event['end_ns']} ns "
                     f"({event['clock_domain']}); persistent={event['persistent']}; sources={len(event['source_refs'])}")
    lines.append("Timeline groups: " + ", ".join(g["clock_domain"] for g in report["timeline"]["groups"]))
    lines.append("Cross-domain ordering: not established")
    return "\n".join(lines) + "\n"
