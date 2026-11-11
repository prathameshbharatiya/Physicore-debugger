"""Read-only view of decoded messages of one topic (M3a): what the typed adapter actually returns, with provenance.

`decode_topic` reads the messages the inventory vouches for (same rules as the Investigator: digest re-check, symlink refusal, count
cross-check) and lists every leaf value of the first `limit` messages in (log_time, segment, index) order. It is a debugging aid and the
basis of the committed example's expected values; it interprets nothing."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..inventory import EvidenceInventory
from ..schemas import ToolInfo, _Strict
from . import cdr as cdrmod
from .observations import LOG_TIME_DOMAIN, Evidence, decode_message, leaf_value, load_evidence, value_type

DECODED_SCHEMA_VERSION = "1.0"
MAX_ARRAY_LEAVES = 16  # arrays longer than this are listed up to this many elements and flagged


class DecoderStatus(_Strict):
    name: str
    available: bool
    version: str | None = None
    reason: str | None = None


class LeafValue(_Strict):
    path: str
    ros_type: str | None
    value_type: Literal["number", "string", "boolean", "none"]
    value: str | bool | None  # numbers as their exact decimal text; `value_type` says which it is
    quality: str


class DecodedMessage(_Strict):
    message_index: int  # position in its file (the same index an observation id carries)
    observation_prefix: str  # "obs:<artifact path>#<message_index>"
    artifact_ref: str
    artifact_sha256: str
    log_time_ns: int
    clock_domain: str = LOG_TIME_DOMAIN
    status: str  # `ok` or the DecodeStatus that prevented decoding
    detail: str | None = None
    leaves: list[LeafValue] = Field(default_factory=list)
    truncated_arrays: list[str] = Field(default_factory=list)


class TopicView(_Strict):
    decoded_schema_version: str = DECODED_SCHEMA_VERSION
    tool: ToolInfo = Field(default_factory=ToolInfo)
    topic: str
    status: str  # `ok`, `topic_not_found`, `evidence_unavailable`, or a topic-level DecodeStatus
    detail: str | None = None
    schema_name: str | None = None
    schema_encoding: str | None = None
    schema_sha256: str | None = None
    message_encoding: str | None = None
    decoder: DecoderStatus | None = None
    evidence_complete: bool = False
    messages: list[DecodedMessage] = Field(default_factory=list)


def _paths(node: Any, prefix: str, out: list[str], truncated: list[str]) -> None:
    """Every leaf path under `node` (arrays capped)."""
    if cdrmod._is_container(node) and not isinstance(node, (bytes, bytearray, memoryview)):
        if isinstance(node, (list, tuple)) or type(node).__name__ == "array":
            items = list(node)
            if len(items) > MAX_ARRAY_LEAVES:
                truncated.append(f"{prefix} ({len(items)} elements)")
                items = items[:MAX_ARRAY_LEAVES]
            for i, v in enumerate(items):
                _paths(v, f"{prefix}.{i}", out, truncated)
        elif isinstance(node, dict):
            for k in node:
                _paths(node[k], f"{prefix}.{k}" if prefix else str(k), out, truncated)
        else:
            for k in cdrmod._fields_of(node):
                _paths(getattr(node, k), f"{prefix}.{k}" if prefix else k, out, truncated)
        return
    out.append(prefix)


def decode_topic(inv: EvidenceInventory, topic: str, *, limit: int = 5, evidence: Evidence | None = None) -> TopicView:
    """Decode the first `limit` messages of `topic`. Never raises for bad content."""
    ev = evidence if evidence is not None else load_evidence(inv, {topic})
    info = cdrmod.decoder_info()
    view = TopicView(topic=topic, status="ok", evidence_complete=ev.complete,
                     decoder=DecoderStatus(name=info.name, available=info.available, version=info.version, reason=info.reason))
    if ev.blocked:
        view.status, view.detail = "evidence_unavailable", ev.blocked
        return view
    msgs = sorted(ev.messages.get(topic, []), key=lambda m: (m.log_time, m.order, m.index))
    if not msgs:
        view.status = "topic_not_found"
        view.detail = "no messages of this topic were available from the usable segments"
        return view
    paths = {a.ref_id: a.relative_path for a in inv.artifacts}
    codec = next((c for c in ev.topic_codecs.get(topic, [])), None)
    view.message_encoding = msgs[0].encoding
    if codec is not None:
        view.schema_name, view.schema_encoding = codec.schema.name or None, codec.schema.encoding or None
        view.schema_sha256 = codec.schema.sha256 or None
        if codec.status != "ok":
            view.status, view.detail = codec.status, codec.detail
    for m in msgs[:limit]:
        dm = DecodedMessage(message_index=m.index, observation_prefix=f"obs:{paths.get(m.artifact_ref, m.artifact_ref)}#{m.index}",
                            artifact_ref=m.artifact_ref, artifact_sha256=m.segment_sha256, log_time_ns=m.log_time, status="ok")
        decoded = decode_message(m)
        root: Any = None
        types = root_type = None
        if isinstance(decoded, cdrmod.DecodeOutcome):
            if decoded.status != "ok" or decoded.message is None:
                dm.status, dm.detail = decoded.status, decoded.detail
            else:
                root, types, root_type = decoded.message.root, decoded.message.types, decoded.message.root_type
        elif decoded is cdrmod._MISSING:
            dm.status, dm.detail = "payload_undecodable", "the payload is not valid JSON"
        else:
            root = decoded
        if dm.status == "ok":
            names: list[str] = []
            _paths(root, "", names, dm.truncated_arrays)
            for path in names:
                leaf = cdrmod.resolve(root, cdrmod.parse_signal(path) or [], types, root_type)
                val, quality = leaf_value(leaf)
                vt = value_type(val)
                dm.leaves.append(LeafValue(path=path, ros_type=leaf.ros_type, value_type=vt,
                                           value=(str(val) if vt == "number" else val), quality=quality))
        view.messages.append(dm)
    if view.status == "ok" and any(x.status != "ok" for x in view.messages):
        view.status = "partial"
    return view


def view_json(v: TopicView) -> str:
    return v.model_dump_json(indent=2) + "\n"


def render_view(v: TopicView) -> str:
    L = [f"Topic {v.topic}: {v.status}" + (f" ({v.detail})" if v.detail else "")]
    if v.schema_name:
        L.append(f"  schema {v.schema_name} [{v.schema_encoding}] sha256 {v.schema_sha256}; message encoding {v.message_encoding}")
    if v.decoder:
        L.append(f"  decoder {v.decoder.name} {v.decoder.version or ''} {'available' if v.decoder.available else 'NOT available'}")
    for m in v.messages:
        L.append(f"  #{m.message_index} log_time {m.log_time_ns} ns ({m.clock_domain}) [{m.status}]" + (f" {m.detail}" if m.detail else ""))
        for leaf in m.leaves:
            L.append(f"      {leaf.path} = {leaf.value!r}  ({leaf.value_type}{', ' + leaf.ros_type if leaf.ros_type else ''}"
                     f"{'' if leaf.quality == 'ok' else ', ' + leaf.quality})")
        for t in m.truncated_arrays:
            L.append(f"      (array truncated: {t})")
    return "\n".join(L) + "\n"
