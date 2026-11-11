"""Canonical observations from evidence that the Phase 1 inventory describes.

What is reused: the inventory's artifact references, integrity verdicts, segment states, counts and log_time windows (which come
from the Collector's own `scan_recording`/`scan_bag`/manifest), the Collector's symlink rule (`first_symlink_component`), its
bounded file reader and its MCAP failure taxonomy.

What is new, and why: the Collector deliberately never decodes message payloads (`mcap_reader.NOT_VERIFIED`), so there is nothing
to reuse for reading a VALUE. This module iterates the MCAP records of an already-inventoried segment with the same `mcap`
library, bounded the same way, and decodes `json`-encoded payloads only. It does not re-derive structure: a segment is used
only if (a) the inventory says it is readable or partially readable, (b) its artifact integrity is verified/not applicable,
(c) its bytes still hash to the digest the inventory carries, and (d) the number of messages read equals the inventory's count.
Anything else makes the segment unusable and says why. Messages in other encodings (CDR, protobuf, ...) are not decoded:
their observations are marked `payload_undecodable`, never guessed.

Clock domains are never merged: a sample's domain is `mcap_log_time` (log_time) or `payload_stamp:<topic>:<field>`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from mcap.exceptions import McapError  # noqa: F401  (re-exported error family; the shared tuple below is what is caught)
from mcap.records import Channel, Message, Schema
from mcap.stream_reader import StreamReader
from pydantic import Field, field_serializer, model_serializer

from ..checksums import sha256_file
from ..export import first_symlink_component
from ..incident import DEFAULT_INCIDENT_DIR, INCIDENT_FILE
from ..inventory import EvidenceInventory
from ..mcap_reader import _FORMAT_ERRORS, _BoundedReader
from ..schemas import _Strict
from . import cdr as cdrmod
from .cdr import Codec, DecodeOutcome, Leaf, SchemaInfo

LOG_TIME_DOMAIN = "mcap_log_time"
MAX_MESSAGES_READ = 2_000_000  # a guard on memory, not a claim about typical size; exceeding it makes the segment incomplete

Quality = Literal["ok", "payload_undecodable", "payload_malformed", "payload_unsupported_type", "signal_missing", "signal_ambiguous",
                  "type_unsupported", "non_finite", "timestamp_missing"]
Value = Decimal | str | bool | None


class Observation(_Strict):
    """One sample of one signal, with where it came from and how trustworthy its decoding was."""

    observation_id: str  # "obs:<artifact relative path>#<message index in file order>"; stable for the same bytes
    artifact_ref: str  # the inventory's ref_id of the segment it was read from
    topic: str
    channel_id: int
    message_index: int  # position of the message in its file (0-based, file order)
    signal: str
    value: Value = None
    value_type: Literal["number", "string", "boolean", "none"]
    timestamp_ns: int | None  # in `clock_domain`; None if the timestamp field was missing/invalid
    timestamp_basis: str  # "mcap_log_time" or "payload_field:<path>"
    clock_domain: str
    quality: Quality
    parser_status: str  # readability of the source segment as established by the Collector: readable | partially_readable
    ros_type: str | None = None  # primitive ROS type of the leaf (`float32`, `int32`, ...) for CDR messages; omitted from JSON when unknown
    source_value: str | None = None
    source_unit: str | None = None
    normalized_unit: str | None = None
    unit_conversion: str | None = None
    robot_family: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unknown_ros_type(self, handler: Any) -> Any:  # keeps the JSON of JSON-payload investigations byte-identical to Phase 2
        d = handler(self)
        for key in ("ros_type", "source_value", "source_unit", "normalized_unit", "unit_conversion", "robot_family"):
            if d.get(key) is None:
                d.pop(key, None)
        return d

    @field_serializer("value")
    def _ser(self, v: Value) -> Any:  # exact decimals are written as their source text; `value_type` disambiguates them from strings
        return str(v) if isinstance(v, Decimal) else v


@dataclass
class Sample:
    """Internal, lightweight twin of `Observation` (one per message and signal)."""

    ts: int | None
    seq: tuple[int, int]  # (segment order, message index): deterministic tie-break, NEVER evidence of ordering
    value: Value
    quality: Quality
    channel_id: int
    artifact_ref: str
    segment_sha256: str
    parser_status: str
    ros_type: str | None = None
    source_value: str | None = None
    source_unit: str | None = None
    normalized_unit: str | None = None
    unit_conversion: str | None = None
    robot_family: str | None = None


@dataclass
class Msg:
    log_time: int
    order: int  # segment order
    index: int
    channel_id: int
    topic: str
    data: bytes | None  # None when the encoding is not decoded here (json and cdr payloads are kept)
    encoding: str
    artifact_ref: str
    segment_sha256: str
    parser_status: str
    codec: Codec | None = None  # for non-JSON payloads: how (and whether) this message can be decoded


@dataclass
class SegmentUse:
    artifact_ref: str | None
    relative_path: str
    order: int
    sha256: str | None
    state: Literal["usable_complete", "usable_partial", "unusable"]
    reason: str | None = None
    messages_read: int = 0


@dataclass
class Evidence:
    """Everything the engine may look at. Built once per investigation; nothing here is written anywhere."""

    segments: list[SegmentUse] = field(default_factory=list)
    messages: dict[str, list[Msg]] = field(default_factory=dict)  # topic -> messages, in segment then file order (NOT time order)
    topic_encodings: dict[str, set[str]] = field(default_factory=dict)
    topic_codecs: dict[str, list[Codec]] = field(default_factory=dict)  # topic -> the distinct codecs of its non-JSON messages
    complete: bool = False  # every segment of the recording was read in full
    incomplete_reasons: list[str] = field(default_factory=list)
    coverage: tuple[int, int] | None = None  # log_time [min, max] over the messages that were actually read
    blocked: str | None = None  # set when nothing at all can be read (inventory failed, no segments...)


# ---------------------------------------------------------------------------
# Evidence root (where inventory-relative paths live)
# ---------------------------------------------------------------------------

def resolve_evidence_root(inv: EvidenceInventory) -> Path:
    """The directory the inventory's artifact paths are relative to, derived the way the inventory derived them."""
    p = Path(inv.provenance.input.path)
    kind = inv.provenance.input.kind
    if kind == "raw_mcap":
        return p.parent
    if kind == "incident":
        if p.is_dir():
            return p if (p / INCIDENT_FILE).is_file() else p / DEFAULT_INCIDENT_DIR
        return p.parent
    return p if p.is_dir() else p.parent


# ---------------------------------------------------------------------------
# Reading one segment
# ---------------------------------------------------------------------------

def _decode(data: bytes) -> Any:
    def refuse(name: str) -> Any:
        raise ValueError(name)
    return json.loads(data.decode("utf-8"), parse_float=Decimal, parse_int=Decimal, parse_constant=refuse)


def _read_segment(path: Path, wanted: set[str], order: int, artifact_ref: str, sha: str, parser_status: str, budget: int,
                  codecs: dict[tuple[str, str | None], Codec]) -> tuple[list[Msg], int, dict[str, set[str]], list[str], bool]:
    """-> (wanted messages, messages read in total, topic encodings seen, errors, completed). Never raises for bad content."""
    schemas: dict[int, Schema] = {}
    channels: dict[int, Channel] = {}
    infos: dict[int, SchemaInfo | None] = {}
    out: list[Msg] = []
    encodings: dict[str, set[str]] = {}
    errors: list[str] = []
    total = 0
    size = path.stat().st_size
    completed = False
    with open(path, "rb") as f:
        reader = StreamReader(_BoundedReader(f, size), validate_crcs=True, record_size_limit=size)
        try:
            for rec in reader.records:
                if isinstance(rec, Schema):
                    schemas.setdefault(rec.id, rec)
                elif isinstance(rec, Channel):
                    channels.setdefault(rec.id, rec)
                    encodings.setdefault(rec.topic, set()).add(rec.message_encoding)
                elif isinstance(rec, Message):
                    ch = channels.get(rec.channel_id)
                    idx = total
                    total += 1
                    if total > budget:
                        errors.append(f"more than {budget} messages; reading stopped (memory guard)")
                        return out, total, encodings, errors, False
                    if ch is not None and ch.topic in wanted:
                        codec = None
                        if ch.message_encoding != "json":
                            if ch.schema_id not in infos:
                                sc = schemas.get(ch.schema_id)
                                infos[ch.schema_id] = SchemaInfo.of(sc.name, sc.encoding, sc.data) if sc is not None else None
                            info = infos[ch.schema_id]
                            key = (ch.message_encoding, info.sha256 if info is not None else None)
                            codec = codecs.get(key)
                            if codec is None:
                                codec = codecs[key] = cdrmod.make_codec(info, ch.message_encoding)
                        out.append(Msg(rec.log_time, order, idx, rec.channel_id, ch.topic,
                                       rec.data if (ch.message_encoding == "json" or (codec is not None and codec.status == "ok")) else None,
                                       ch.message_encoding, artifact_ref, sha, parser_status, codec))
            completed = True
        except _FORMAT_ERRORS as e:
            errors.append(f"{type(e).__name__}: {e}"[:300])
    return out, total, encodings, errors, completed


def load_evidence(inv: EvidenceInventory, wanted_topics: set[str], root: Path | None = None) -> Evidence:
    """Read, from the segments the inventory vouches for, the messages of `wanted_topics`. Read-only."""
    ev = Evidence()
    if inv.inspection.state == "failed":
        ev.blocked = "the evidence inventory failed: " + ("; ".join(inv.inspection.reasons) or "no reason recorded")
        return ev
    rec = inv.recording
    if not rec.segments:
        ev.blocked = "the inventory lists no recording segments"
        return ev
    root = root if root is not None else resolve_evidence_root(inv)
    by_ref = {a.ref_id: a for a in inv.artifacts}
    budget = MAX_MESSAGES_READ
    codecs: dict[tuple[str, str | None], Codec] = {}
    for seg in sorted(rec.segments, key=lambda s: s.order):
        su = SegmentUse(artifact_ref=seg.artifact_ref, relative_path=seg.relative_path, order=seg.order, sha256=None, state="unusable")
        ev.segments.append(su)
        art = by_ref.get(seg.artifact_ref) if seg.artifact_ref else None
        if art is None:
            su.reason = "the segment has no artifact reference in the inventory"
        elif seg.state not in ("readable", "partially_readable"):
            su.reason = f"the Collector established this segment as '{seg.state}'"
        elif art.integrity not in ("verified", "not_applicable"):
            su.reason = f"artifact integrity is '{art.integrity}'"
        elif art.sha256 is None:
            su.reason = "the inventory carries no digest to bind these bytes to"
        elif seg.observed_message_count is None:
            su.reason = "the inventory carries no message count to cross-check against"
        else:
            su.sha256 = art.sha256
            link = first_symlink_component(root, art.relative_path)
            fpath = root / art.relative_path
            if link is not None:
                su.reason = f"'{link}' is a symlink; it was not followed"
            elif not fpath.is_file():
                su.reason = "the file is missing or not a regular file now"
            else:
                try:
                    now = sha256_file(fpath)
                except OSError as e:
                    su.reason = f"the file could not be read: {e.strerror or e}"
                else:
                    if now != art.sha256:
                        su.reason = "the file's bytes no longer match the digest in the inventory (changed since it was inventoried)"
                    else:
                        status = seg.state
                        try:
                            msgs, total, enc, errs, done = _read_segment(fpath, wanted_topics, seg.order, art.ref_id, art.sha256, status, budget, codecs)
                        except OSError as e:
                            su.reason = f"the file could not be read: {e.strerror or e}"
                        else:
                            budget -= total
                            su.messages_read = total
                            if total != seg.observed_message_count and not (not done and status == "partially_readable"):
                                su.reason = (f"read {total} message(s) but the inventory counted {seg.observed_message_count}; "
                                             "the two disagree, so this segment is not used")
                            elif not done and status == "readable":
                                su.reason = "the inventory says readable, but reading failed: " + (errs[0] if errs else "unknown error")
                            else:
                                # a partially readable segment: the recovered messages are real, the rest is unknown
                                su.state = "usable_complete" if (done and status == "readable") else "usable_partial"
                                for m in msgs:
                                    ev.messages.setdefault(m.topic, []).append(m)
                                    if m.codec is not None and not any(c is m.codec for c in ev.topic_codecs.get(m.topic, ())):
                                        ev.topic_codecs.setdefault(m.topic, []).append(m.codec)
                                for t, es in enc.items():
                                    ev.topic_encodings.setdefault(t, set()).update(es)
                                if su.state == "usable_partial":
                                    su.reason = "partially readable: messages after the first read error are unknown"
        if su.state != "usable_complete":
            ev.incomplete_reasons.append(f"segment '{seg.relative_path}': {su.reason}")
    # (messages are put in time order, per signal, by the engine; nothing here depends on file or segment order)
    ev.complete = bool(ev.segments) and all(s.state == "usable_complete" for s in ev.segments) \
        and rec.readability is not None and rec.readability.value == "readable"
    if ev.segments and all(s.state == "usable_complete" for s in ev.segments) and not ev.complete:
        ev.incomplete_reasons.append("the recording as a whole is not established as readable by the Collector")
    firsts = [s.first_log_time_ns for s in rec.segments if s.first_log_time_ns is not None
              and any(u.order == s.order and u.state != "unusable" for u in ev.segments)]
    lasts = [s.last_log_time_ns for s in rec.segments if s.last_log_time_ns is not None
             and any(u.order == s.order and u.state != "unusable" for u in ev.segments)]
    if firsts and lasts:
        ev.coverage = (min(firsts), max(lasts))
    return ev


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

_MISSING = cdrmod._MISSING  # one sentinel for "no value", shared with the CDR adapter

# CDR failure statuses that surface as a per-sample quality (the rest are whole-topic conditions reported before evaluation)
_QUALITY_FOR_STATUS = {"payload_malformed": "payload_malformed", "payload_unsupported_type": "payload_unsupported_type"}  # others -> payload_undecodable

_STEPS: dict[str, list[cdrmod.Step] | None] = {}


def _steps(path: str) -> list[cdrmod.Step] | None:
    if path not in _STEPS:
        _STEPS[path] = cdrmod.parse_signal(path)
    return _STEPS[path]


def value_type(v: Value) -> Literal["number", "string", "boolean", "none"]:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, Decimal):
        return "number"
    if isinstance(v, str):
        return "string"
    return "none"


def leaf_value(leaf: Leaf) -> tuple[Value, Quality]:
    """A resolved leaf as a comparable value. Integers stay exact; floats become decimals of their own type's shortest text."""
    if leaf.status == "missing":
        return None, "signal_missing"
    if leaf.status == "ambiguous":
        return None, "signal_ambiguous"
    if leaf.status == "not_a_leaf":
        return None, "type_unsupported"
    v = leaf.raw
    if isinstance(v, bool):
        return v, "ok"
    if isinstance(v, Decimal):
        return v, "ok"
    if isinstance(v, int):
        return Decimal(v), "ok"
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None, "non_finite"
        return cdrmod.to_decimal(v, leaf.ros_type), "ok"
    if isinstance(v, str):
        return v, "ok"
    return None, "type_unsupported"


NS_PER_SEC = 1_000_000_000
TIME_TYPE = "builtin_interfaces/msg/Time"


def header_stamp_ns(root: Any, field: str, types: Any, root_type: str | None) -> int | None:
    """Exact integer nanoseconds of a builtin_interfaces/Time struct at `field`: sec * 10^9 + nanosec. None (never a guess) when the
    struct is absent, is not a Time (e.g. a Duration), has non-integer parts, a negative sec, or a nanosec outside [0, 10^9).
    A stamp of exactly zero is accepted as zero: it can be a legitimate simulation time, and this tool does not decide that it means 'unset'.
    Type check: for a CDR message the embedded schema must say the field is builtin_interfaces/msg/Time; for a JSON message no ROS type exists,
    so only the sec/nanosec structure is checked (the engine reports that limitation on the result)."""
    fsteps = _steps(field)
    if not fsteps:
        return None
    declared = cdrmod.struct_type_at(types, root_type, fsteps)
    if types and declared is not None and declared != TIME_TYPE:
        return None  # schema says this field is not a Time
    if types and declared is None:
        return None  # a typed message whose field type cannot be established is not trusted as a clock
    parts = []
    for leaf_name in ("sec", "nanosec"):
        lv, lq = leaf_value(cdrmod.resolve(root, _steps(f"{field}.{leaf_name}") or [], types, root_type))
        if lq != "ok" or not isinstance(lv, Decimal) or isinstance(lv, bool) or lv != lv.to_integral_value():
            return None
        parts.append(int(lv))
    sec, nsec = parts
    if sec < 0 or not 0 <= nsec < NS_PER_SEC:
        return None
    return sec * NS_PER_SEC + nsec


def extract(msg: Msg, decoded: Any, signal: str, time_source: str, time_field: str | None
            ) -> tuple[Value, Quality, int | None, str, str, str | None]:
    """-> (value, quality, timestamp_ns, timestamp_basis, clock_domain, ros_type) for one message."""
    if time_source == "log_time":
        ts, basis, domain = msg.log_time, "mcap_log_time", LOG_TIME_DOMAIN
    else:
        basis = f"payload_field:{time_field}" if time_source == "payload_field" else f"header_stamp:{time_field}"
        domain = f"payload_stamp:{msg.topic}:{time_field}"
        ts = None
    root: Any
    types = root_type = None
    if isinstance(decoded, DecodeOutcome):
        if decoded.status != "ok" or decoded.message is None:
            q = _QUALITY_FOR_STATUS.get(decoded.status, "payload_undecodable")
            return None, q, (ts if time_source == "log_time" else None), basis, domain, None  # type: ignore[return-value]
        root, types, root_type = decoded.message.root, decoded.message.types, decoded.message.root_type
    elif decoded is _MISSING:  # JSON that did not parse (or an encoding that is not decoded)
        return None, "payload_undecodable", (ts if time_source == "log_time" else None), basis, domain, None
    else:
        root = decoded
    if time_source == "header_stamp":
        ts = header_stamp_ns(root, time_field or "", types, root_type)
        if ts is None:
            return None, "timestamp_missing", None, basis, domain, None
    elif time_source == "payload_field":
        tsteps = _steps(time_field or "")
        tv, _tq = leaf_value(cdrmod.resolve(root, tsteps, types, root_type)) if tsteps else (None, "signal_missing")
        if isinstance(tv, Decimal) and not isinstance(tv, bool) and tv == tv.to_integral_value() and tv >= 0:
            ts = int(tv)
        else:
            return None, "timestamp_missing", None, basis, domain, None
    steps = _steps(signal)
    leaf = cdrmod.resolve(root, steps, types, root_type) if steps else Leaf("missing")
    value, quality = leaf_value(leaf)
    return value, quality, ts, basis, domain, leaf.ros_type


def decode_message(msg: Msg) -> Any:
    """JSON: the parsed value, or `_MISSING` when it does not parse. CDR (or any other codec-handled encoding): a `DecodeOutcome`.
    Never raises."""
    if msg.codec is not None:
        if msg.data is None:
            return DecodeOutcome(msg.codec.status, detail=msg.codec.detail)
        return msg.codec.decode(msg.data)
    if msg.data is None:
        return _MISSING
    try:
        return _decode(msg.data)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _MISSING
