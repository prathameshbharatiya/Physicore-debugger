"""FX-ART (TEST_PLAN 1): valid alternate artifacts with genuinely different bytes and digests recomputed from the actual bytes by the
unmodified accepted loader. Nothing here edits metadata or digests; integrity validation is never relaxed."""
from __future__ import annotations

import hashlib
from pathlib import Path

from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator.observations import Evidence, load_evidence
from tests import investigator_fixtures as F

DEFAULT_CHUNK = 2048
ALT_CHUNK = 256


def log_stream(n: int = 5, *, start: int = F.BASE, step: int = F.MS, name: str = "nav", level: int = 40, tag: str = "x"):
    return [(start + i * step, {"level": level, "name": name, "msg": f"{tag}{i}"}) for i in range(n)]


def write_bag(root: Path, segments: list[F.Streams], *, chunk_size: int = DEFAULT_CHUNK, alternate: set[int] | None = None) -> Path:
    """A rosbag2 directory; the segments whose index is in `alternate` are re-serialised with a different MCAP chunk size, so they hold
    the same messages in genuinely different bytes."""
    bag = F.write_json_bag(root, segments, chunk_size=chunk_size)
    for i in alternate or set():
        (bag / f"{bag.name}_{i}.mcap").write_bytes(F.build_json_mcap(segments[i], chunk_size=ALT_CHUNK))
    return bag


def load(bag: Path, topics: set[str]):
    inv = inspect_evidence(bag)
    return inv, load_evidence(inv, topics)


def fingerprint(ev: Evidence, topic: str) -> list[tuple]:
    """Everything about the messages except which artifact bytes carried them."""
    return [(m.topic, m.order, m.index, m.log_time, m.data) for m in ev.messages[topic]]


def segment_digests(ev: Evidence, topic: str) -> dict[str, str]:
    return {m.artifact_ref: m.segment_sha256 for m in ev.messages[topic]}


def file_digests(bag: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(bag.glob("*.mcap"))}


# ---- typed recordings (schema names recorded exactly as a ROS recorder would) -------------------------------------------------
import io as _io
import json as _json

import yaml as _yaml
from mcap.writer import CompressionType as _CT, Writer as _W

from tests import make_fixtures as _mf
from tests.bagtools import metadata_dict as _md


def build_typed_json_mcap(streams: F.Streams, types: dict[str, str], *, chunk_size: int = DEFAULT_CHUNK, schema_text: dict[str, bytes] | None = None) -> bytes:
    """JSON-payload MCAP whose channels record the given schema NAME per topic (e.g. 'rcl_interfaces/msg/Log'). A payload given as bytes is
    written verbatim (exact JSON text or malformed JSON)."""
    out = _io.BytesIO()
    w = _W(out, chunk_size=chunk_size, compression=_CT.NONE, use_chunking=True, enable_crcs=True, enable_data_crcs=True)
    w.start(profile="", library=_mf.LIBRARY)
    events = []
    for idx, (topic, msgs) in enumerate(streams.items()):
        text = (schema_text or {}).get(types[topic], b'{"type":"object"}')
        sid = w.register_schema(name=types[topic], encoding="jsonschema", data=text)
        cid = w.register_channel(topic=topic, message_encoding="json", schema_id=sid)
        for seq, (t, payload) in enumerate(msgs):
            data = payload if isinstance(payload, bytes) else _json.dumps(payload).encode()
            events.append((t, idx, seq, cid, data))
    for t, _i, seq, cid, data in sorted(events, key=lambda e: e[:4]):
        w.add_message(channel_id=cid, log_time=t, publish_time=t, sequence=seq, data=data)
    w.finish()
    return out.getvalue()


def _write_meta(root: Path, files):
    (root / "metadata.yaml").write_text(_yaml.safe_dump(_md(files), sort_keys=False))


def write_typed_bag(root: Path, segments: list[F.Streams], types: dict[str, str], *, alternate: set[int] | None = None,
                    schema_text: dict[str, bytes] | None = None) -> Path:
    """Rosbag2 directory of JSON-payload MCAPs with named schemas; `alternate` segments use another chunk size (FX-ART)."""
    root.mkdir(parents=True)
    files = []
    for i, streams in enumerate(segments):
        fname = f"{root.name}_{i}.mcap"
        cs = ALT_CHUNK if i in (alternate or set()) else DEFAULT_CHUNK
        (root / fname).write_bytes(build_typed_json_mcap(streams, types, chunk_size=cs, schema_text=schema_text))
        files.append((fname, [_mf.StreamSpec(t, tuple(x for x, _ in m)) for t, m in streams.items()]))
    _write_meta(root, files)
    return root


def write_cdr_bag(root: Path, segments: list[list], **kw) -> Path:
    """Rosbag2 directory of CDR MCAPs; each segment is a list of (topic, msgtype, [(log_time, message object | raw bytes)])."""
    from tests import cdr_fixtures as cf
    root.mkdir(parents=True)
    files = []
    for i, chans in enumerate(segments):
        fname = f"{root.name}_{i}.mcap"
        (root / fname).write_bytes(cf.build_cdr_mcap(chans, **kw))
        files.append((fname, [_mf.StreamSpec(t, tuple(x for x, _ in m)) for t, _ty, m in chans]))
    _write_meta(root, files)
    return root
