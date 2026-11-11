"""Strict interval order, temporal layers, earliest observable symptom and its defence (DESIGN 4.2, FR-PRE-01..04). Per lane (clock domain); lanes are never merged.
layer(X) = length of the longest strictly-preceding chain ending at X, minus one; symptoms of one layer are pairwise unordered. Computed in O(n log n)."""
from __future__ import annotations

import heapq
from typing import Iterable

from pydantic import StrictInt, StrictStr, field_validator, model_validator

from .intervals import Interval
from .report import Strict
from .symptoms import Symptom
from .vocabulary import (DEFENCE_STATES, NONE_OBSERVED_SENTENCE, NOT_COMPARED_ACROSS_LANES, NOT_DEFENDED_SENTENCE, SCOPE_SENTENCE, TIED_SENTENCE)


def layer_map(entries: Iterable[tuple[str, Interval]]) -> dict[str, int]:
    """symptom id -> layer, for entries of ONE lane. Sort by lower bound; a min-heap on upper bounds settles every earlier symptom that strictly precedes the
    current one (its upper bound is below the current lower bound), so the layer is 1 + the best settled layer."""
    items = sorted(((iv.lower_ns, iv.upper_ns, sid, iv) for sid, iv in entries), key=lambda t: (t[0], t[1], t[2]))
    domains = {t[3].domain for t in items}
    if len(domains) > 1:
        raise ValueError("layers are computed per lane; the entries mix clock domains")
    out: dict[str, int] = {}
    heap: list[tuple[int, int]] = []
    settled = -1
    for lo, up, sid, _iv in items:
        if sid in out:
            raise ValueError(f"duplicate symptom id {sid}")
        while heap and heap[0][0] < lo:
            settled = max(settled, heapq.heappop(heap)[1])
        layer = settled + 1
        out[sid] = layer
        heapq.heappush(heap, (up, layer))
    return out


class Earliest(Strict):
    symptom_ids: list[StrictStr]
    defence: StrictStr
    blocker_ids: list[StrictStr]
    statement: StrictStr

    @field_validator("defence")
    @classmethod
    def _d(cls, v: str) -> str:
        if v not in DEFENCE_STATES:
            raise ValueError(f"defence must be one of {DEFENCE_STATES}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "Earliest":
        n = len(self.symptom_ids)
        if self.defence == "none_observed" and n != 0:
            raise ValueError("none_observed means no symptom")
        if self.defence == "tied" and n < 2:
            raise ValueError("tied needs at least two symptoms")
        if self.defence in ("defended", "not_defended") and n != 1:
            raise ValueError("defended / not_defended name exactly one symptom")
        if self.defence == "defended" and self.blocker_ids:
            raise ValueError("a defended claim cannot have blockers")
        return self


class Lane(Strict):
    domain: StrictStr
    symptom_count: StrictInt
    layers: list[list[StrictStr]]
    earliest: Earliest


def _earliest(ids: list[str], truncated: dict[str, bool], blocker_ids: list[str]) -> Earliest:
    blockers = sorted(set(blocker_ids))
    if not ids:
        return Earliest(symptom_ids=[], defence="none_observed", blocker_ids=blockers, statement=NONE_OBSERVED_SENTENCE)
    if len(ids) > 1:
        return Earliest(symptom_ids=ids, defence="tied", blocker_ids=blockers, statement=TIED_SENTENCE)
    if blockers or truncated[ids[0]]:
        return Earliest(symptom_ids=ids, defence="not_defended", blocker_ids=blockers, statement=NOT_DEFENDED_SENTENCE)
    return Earliest(symptom_ids=ids, defence="defended", blocker_ids=[], statement=SCOPE_SENTENCE)


def build_lane(domain: str, entries: list[tuple[str, Interval, bool]], blocker_ids: list[str]) -> Lane:
    """entries = (symptom id, onset in this lane, truncated)."""
    lm = layer_map((sid, iv) for sid, iv, _t in entries)
    by_layer: dict[int, list[str]] = {}
    for sid, layer in lm.items():
        by_layer.setdefault(layer, []).append(sid)
    layers = [sorted(by_layer[k]) for k in sorted(by_layer)]
    first = layers[0] if layers else []
    return Lane(domain=domain, symptom_count=len(entries), layers=layers,
                earliest=_earliest(first, {sid: t for sid, _iv, t in entries}, blocker_ids))


def build_lanes(symptoms: Iterable[Symptom], blockers_by_lane: dict[str, list[str]] | None = None,
                extra_lanes: Iterable[str] = ()) -> tuple[list[Lane], str | None]:
    """-> (lanes sorted by domain name, cross-lane statement when more than one lane exists). A lane exists when a symptom is placed in it or it is named in
    `extra_lanes` (a lane with blockers but no symptom is reported as none_observed)."""
    blockers_by_lane = blockers_by_lane or {}
    per: dict[str, list[tuple[str, Interval, bool]]] = {}
    for s in symptoms:
        for lane in s.lanes:
            iv = s.onset_in(lane)
            assert iv is not None
            per.setdefault(lane, []).append((s.symptom_id, iv, s.truncated))
    for lane in extra_lanes:
        per.setdefault(lane, [])
    lanes = [build_lane(d, per[d], blockers_by_lane.get(d, [])) for d in sorted(per)]
    return lanes, (NOT_COMPARED_ACROSS_LANES if len(lanes) > 1 else None)
