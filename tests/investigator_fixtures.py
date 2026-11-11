"""Synthetic evidence for Investigator tests: MCAP files whose messages carry JSON payloads with chosen values. No ROS 2, no
real robot data. Deterministic: every byte derives from the arguments."""

from __future__ import annotations

import io
import json
from pathlib import Path

import yaml
from mcap.writer import CompressionType, Writer

from tests import make_fixtures as mf
from tests.bagtools import metadata_dict

BASE = mf.BASE_NS
MS = 1_000_000
SEC = 1_000_000_000

Streams = dict[str, list[tuple[int, object]]]  # topic -> [(log_time_ns, payload)]


def build_json_mcap(streams: Streams, *, encodings: dict[str, str] | None = None, chunk_size: int = 2048) -> bytes:
    """One MCAP; payloads are JSON unless `encodings[topic]` says otherwise (then the bytes are opaque, not JSON)."""
    encodings = encodings or {}
    out = io.BytesIO()
    w = Writer(out, chunk_size=chunk_size, compression=CompressionType.NONE, use_chunking=True, enable_crcs=True, enable_data_crcs=True)
    w.start(profile="", library=mf.LIBRARY)
    events = []
    for idx, (topic, msgs) in enumerate(streams.items()):
        enc = encodings.get(topic, "json")
        sid = w.register_schema(name="synthetic/Sample", encoding="jsonschema", data=b'{"type":"object"}')
        cid = w.register_channel(topic=topic, message_encoding=enc, schema_id=sid)
        for seq, (t, payload) in enumerate(msgs):
            if isinstance(payload, bytes):
                data = payload  # raw bytes: lets a test put exact JSON text (or malformed JSON) in a json channel
            else:
                data = json.dumps(payload).encode() if enc == "json" else b"\x00\x01\x02\x03cdr-like"
            events.append((t, idx, seq, cid, data))
    for t, _i, seq, cid, data in sorted(events, key=lambda e: e[:4]):
        w.add_message(channel_id=cid, log_time=t, publish_time=t, sequence=seq, data=data)
    w.finish()
    return out.getvalue()


def write_mcap(path: Path, streams: Streams, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_json_mcap(streams, **kw))
    return path


def write_json_bag(root: Path, segments: list[Streams], **kw) -> Path:
    """A rosbag2 directory (metadata.yaml + split MCAP segments) with JSON payloads."""
    root.mkdir(parents=True)
    files = []
    for i, streams in enumerate(segments):
        fname = f"{root.name}_{i}.mcap"
        (root / fname).write_bytes(build_json_mcap(streams, **kw))
        files.append((fname, [mf.StreamSpec(t, tuple(x for x, _ in m)) for t, m in streams.items()]))
    (root / "metadata.yaml").write_text(yaml.safe_dump(metadata_dict(files), sort_keys=False))
    return root


def series(topic_values: list[object], *, start: int = BASE, step: int = 100 * MS, field: str = "v") -> list[tuple[int, object]]:
    """Messages {field: value} at start, start+step, ..."""
    return [(start + i * step, {field: v}) for i, v in enumerate(topic_values)]


def contract(expectations: list[dict], *, contract_id: str = "c1") -> dict:
    return {"contract_schema_version": "1.0", "contract_id": contract_id, "expectations": expectations}


def expectation(eid: str = "E1", *, topic: str = "/temp", signal: str = "v", check: dict | None = None, **kw) -> dict:
    d = {
        "expectation_id": eid, "description": f"{eid} fixture expectation", "topic": topic, "signal": signal,
        "clock_domain": "mcap_log_time", "check": check or {"kind": "range", "min": 0, "max": 10},
        "source": {"kind": "test_fixture", "reference": "tests/investigator_fixtures.py"},
    }
    d.update(kw)
    return d
