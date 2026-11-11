"""Strict robot-specific signal anomaly profiles and deterministic evaluation.

Thresholds are explicit per signal and expressed in the configured normalized unit.
This module never converts units, infers relationships, or treats anomalies as causes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .signal_rules import (
    DeclaredRelationship, SignalEvent, SignalPoint, detect_cross_signal_inconsistency,
    detect_discontinuities, detect_oscillation, detect_rate_of_change, detect_stuck,
    retain_short_events,
)


@dataclass(frozen=True)
class ExpectedTransition:
    start_ns: int
    end_ns: int
    clock_domain: str
    label: str


@dataclass(frozen=True)
class SignalRuleSpec:
    name: str
    unit: str
    rate_max: str | None = None
    stuck_epsilon: str | None = None
    stuck_min_duration_ns: int | None = None
    change_expected: bool = False
    discontinuity_max_delta: str | None = None
    oscillation_min_turns: int | None = None
    oscillation_min_amplitude: str | None = None


@dataclass(frozen=True)
class SignalProfile:
    robot_family: str
    signals: dict[str, SignalRuleSpec]
    relationships: tuple[DeclaredRelationship, ...]
    transitions: tuple[ExpectedTransition, ...]
    persistence_ns: int
    profile_version: int


@dataclass(frozen=True)
class EvaluatedSignalEvent:
    event: SignalEvent
    expected_transition_overlap: str | None = None


@dataclass(frozen=True)
class SignalProfileResult:
    robot_family: str
    events: tuple[EvaluatedSignalEvent, ...]
    signals_evaluated: tuple[str, ...]
    missing_signals: tuple[str, ...]
    profile_version: int


def _mapping(value: Any, where: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{where} contains unknown keys: {', '.join(sorted(map(str, unknown)))}")
    return value


def _positive_int(value: Any, where: str, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError(f"{where} must be an integer {'>= 0' if allow_zero else '> 0'}")
    return value


def _decimal_text(value: Any, where: str, *, allow_zero: bool = False) -> str:
    from decimal import Decimal, InvalidOperation
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a decimal string")
    try:
        n = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{where} must be a decimal string") from exc
    if not n.is_finite() or n < 0 or (n == 0 and not allow_zero):
        raise ValueError(f"{where} must be {'non-negative' if allow_zero else 'positive'} and finite")
    return value


def validate_signal_profile(raw: Any) -> SignalProfile:
    top = _mapping(raw, "$", {"profile_version", "robot_family", "persistence_ns", "signals", "relationships", "expected_transitions"})
    if top.get("profile_version") != 1 or type(top.get("profile_version")) is not int:
        raise ValueError("profile_version must be integer 1")
    family = top.get("robot_family")
    if not isinstance(family, str) or not family.strip():
        raise ValueError("robot_family must be a non-empty string")
    persistence = _positive_int(top.get("persistence_ns"), "persistence_ns", allow_zero=True)
    rows = top.get("signals")
    if not isinstance(rows, list) or not rows:
        raise ValueError("signals must be a non-empty list")
    signals: dict[str, SignalRuleSpec] = {}
    for i, item in enumerate(rows):
        p = f"signals[{i}]"
        m = _mapping(item, p, {"name", "unit", "rate_max", "stuck_epsilon", "stuck_min_duration_ns", "change_expected",
                               "discontinuity_max_delta", "oscillation_min_turns", "oscillation_min_amplitude"})
        name, unit = m.get("name"), m.get("unit")
        if not isinstance(name, str) or not name.strip() or name in signals:
            raise ValueError(f"{p}.name must be non-empty and unique")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"{p}.unit must be explicitly declared")
        rate = _decimal_text(m["rate_max"], f"{p}.rate_max") if "rate_max" in m else None
        stuck = _decimal_text(m["stuck_epsilon"], f"{p}.stuck_epsilon", allow_zero=True) if "stuck_epsilon" in m else None
        duration = _positive_int(m["stuck_min_duration_ns"], f"{p}.stuck_min_duration_ns") if "stuck_min_duration_ns" in m else None
        change = m.get("change_expected", False)
        if not isinstance(change, bool):
            raise ValueError(f"{p}.change_expected must be boolean")
        if (stuck is None) != (duration is None):
            raise ValueError(f"{p} must configure stuck_epsilon and stuck_min_duration_ns together")
        if stuck is not None and not change:
            raise ValueError(f"{p}.change_expected must be true when stuck detection is configured")
        disc = _decimal_text(m["discontinuity_max_delta"], f"{p}.discontinuity_max_delta") if "discontinuity_max_delta" in m else None
        turns = _positive_int(m["oscillation_min_turns"], f"{p}.oscillation_min_turns") if "oscillation_min_turns" in m else None
        amp = _decimal_text(m["oscillation_min_amplitude"], f"{p}.oscillation_min_amplitude") if "oscillation_min_amplitude" in m else None
        if (turns is None) != (amp is None):
            raise ValueError(f"{p} must configure oscillation_min_turns and oscillation_min_amplitude together")
        if turns is not None and turns < 2:
            raise ValueError(f"{p}.oscillation_min_turns must be >= 2")
        if not any((rate, stuck, disc, turns)):
            raise ValueError(f"{p} must enable at least one detector with explicit parameters")
        signals[name] = SignalRuleSpec(name, unit, rate, stuck, duration, change, disc, turns, amp)
    relationships = []
    rels = top.get("relationships", [])
    if not isinstance(rels, list):
        raise ValueError("relationships must be a list")
    for i, item in enumerate(rels):
        p = f"relationships[{i}]"
        m = _mapping(item, p, {"left_signal", "right_signal", "left_unit", "right_unit", "scale", "offset", "tolerance", "max_skew_ns"})
        for k in ("left_signal", "right_signal", "left_unit", "right_unit"):
            if not isinstance(m.get(k), str) or not m[k]:
                raise ValueError(f"{p}.{k} must be a non-empty string")
        if m["left_signal"] not in signals or m["right_signal"] not in signals:
            raise ValueError(f"{p} references an undeclared signal")
        if m["left_unit"] != signals[m["left_signal"]].unit or m["right_unit"] != signals[m["right_signal"]].unit:
            raise ValueError(f"{p} units must exactly match the signal profile")
        scale = _decimal_text(m.get("scale"), f"{p}.scale")
        offset = m.get("offset", "0")
        if not isinstance(offset, str):
            raise ValueError(f"{p}.offset must be a decimal string")
        from decimal import Decimal, InvalidOperation
        try:
            if not Decimal(offset).is_finite():
                raise ValueError()
        except (InvalidOperation, ValueError):
            raise ValueError(f"{p}.offset must be finite decimal text")
        tolerance = _decimal_text(m.get("tolerance"), f"{p}.tolerance", allow_zero=True)
        skew = _positive_int(m.get("max_skew_ns"), f"{p}.max_skew_ns", allow_zero=True)
        relationships.append(DeclaredRelationship(m["left_signal"], m["right_signal"], m["left_unit"], m["right_unit"],
                                                   scale, offset, tolerance, skew))
    transitions = []
    trs = top.get("expected_transitions", [])
    if not isinstance(trs, list):
        raise ValueError("expected_transitions must be a list")
    for i, item in enumerate(trs):
        p = f"expected_transitions[{i}]"
        m = _mapping(item, p, {"start_ns", "end_ns", "clock_domain", "label"})
        start = _positive_int(m.get("start_ns"), f"{p}.start_ns", allow_zero=True)
        end = _positive_int(m.get("end_ns"), f"{p}.end_ns", allow_zero=True)
        if end < start:
            raise ValueError(f"{p}.end_ns must be >= start_ns")
        if not isinstance(m.get("clock_domain"), str) or not m["clock_domain"]:
            raise ValueError(f"{p}.clock_domain must be non-empty")
        if not isinstance(m.get("label"), str) or not m["label"]:
            raise ValueError(f"{p}.label must be non-empty")
        transitions.append(ExpectedTransition(start, end, m["clock_domain"], m["label"]))
    return SignalProfile(family, signals, tuple(relationships), tuple(transitions), persistence, 1)


def load_signal_profile(path: str | Path) -> SignalProfile:
    p = Path(path)
    if p.stat().st_size > 1_048_576:
        raise ValueError("signal profile exceeds 1 MiB")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    return validate_signal_profile(raw)


def evaluate_signal_profile(profile: SignalProfile, samples: dict[str, Iterable[SignalPoint]]) -> SignalProfileResult:
    """Evaluate configured rules. Inputs must already be normalized and carry stable source references."""
    materialized = {name: tuple(points) for name, points in samples.items()}
    events: list[SignalEvent] = []
    for name, spec in profile.signals.items():
        points = materialized.get(name, ())
        if not points:
            continue
        if any(p.unit != spec.unit for p in points):
            raise ValueError(f"{name}: input unit does not match configured normalized unit {spec.unit!r}")
        if spec.rate_max is not None:
            events.extend(detect_rate_of_change(points, max_abs_rate=spec.rate_max))
        if spec.stuck_epsilon is not None:
            events.extend(detect_stuck(points, epsilon=spec.stuck_epsilon, min_duration_ns=spec.stuck_min_duration_ns or 0,
                                       change_expected=spec.change_expected))
        if spec.discontinuity_max_delta is not None:
            events.extend(detect_discontinuities(points, max_delta=spec.discontinuity_max_delta))
        if spec.oscillation_min_turns is not None:
            events.extend(detect_oscillation(points, min_turns=spec.oscillation_min_turns,
                                             min_amplitude=spec.oscillation_min_amplitude or "0"))
    for rel in profile.relationships:
        events.extend(detect_cross_signal_inconsistency(materialized.get(rel.left_signal, ()),
                                                        materialized.get(rel.right_signal, ()), rel))
    retained = retain_short_events(events, persistence_ns=profile.persistence_ns)
    wrapped = []
    for event in retained:
        labels = [t.label for t in profile.transitions if t.clock_domain == event.clock_domain
                  and event.start_ns <= t.end_ns and event.end_ns >= t.start_ns]
        wrapped.append(EvaluatedSignalEvent(event, ", ".join(sorted(set(labels))) if labels else None))
    wrapped.sort(key=lambda x: (x.event.clock_domain, x.event.start_ns, x.event.rule, x.event.source_refs))
    return SignalProfileResult(profile.robot_family, tuple(wrapped), tuple(sorted(n for n in profile.signals if materialized.get(n))),
                               tuple(sorted(n for n in profile.signals if not materialized.get(n))), profile.profile_version)
