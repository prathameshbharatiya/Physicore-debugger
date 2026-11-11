"""Deterministic synthetic fixture generator. No real robot data.

Usage:
    python -m tests.make_fixtures examples/fixtures

Everything is derived from fixed constants (no clocks, no randomness), so two
runs produce byte-identical files; a test enforces this and also checks that
the files committed under examples/fixtures match this generator.
"""

from __future__ import annotations

import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from mcap.writer import CompressionType, Writer

BASE_NS = 1_700_000_000_000_000_000  # fixed synthetic start time (nanoseconds)
NS = 1_000_000_000
LIBRARY = "physicore-collector-synthetic-fixtures"

DEFAULT_METADATA = {"session": {"operator": "alice", "session_label": "synthetic-valid"}}


@dataclass(frozen=True)
class StreamSpec:
    """One topic with explicit log times (nanoseconds)."""

    topic: str
    log_times_ns: tuple[int, ...]
    message_encoding: str = "json"
    schema_name: str = "synthetic/Sample"


def periodic(start_ns: int, period_ns: int, count: int, skip: range = range(0)) -> tuple[int, ...]:
    """count timestamps spaced by period_ns, omitting indices in `skip`."""
    return tuple(start_ns + i * period_ns for i in range(count) if i not in skip)


def joint_states(skip: range = range(0)) -> StreamSpec:
    return StreamSpec("/joint_states", periodic(BASE_NS, NS // 50, 100, skip))  # 50 Hz, 2.0 s


def camera() -> StreamSpec:
    return StreamSpec("/camera/image", periodic(BASE_NS, NS // 10, 20))  # 10 Hz, 2.0 s


def diagnostics() -> StreamSpec:
    return StreamSpec("/diagnostics", periodic(BASE_NS, NS, 3))  # 1 Hz


def build_mcap(
    streams: list[StreamSpec],
    *,
    metadata: dict[str, dict[str, str]] | None = None,
    compression: CompressionType = CompressionType.NONE,
    chunk_size: int = 2048,
    use_chunking: bool = True,
    payload_pad: int = 0,
) -> bytes:
    """Write a complete, finalized MCAP (with CRCs, statistics, summary) to bytes."""
    out = io.BytesIO()
    w = Writer(
        out,
        chunk_size=chunk_size,
        compression=compression,
        use_chunking=use_chunking,
        enable_crcs=True,
        enable_data_crcs=True,
    )
    w.start(profile="", library=LIBRARY)
    for name, values in (metadata or {}).items():
        w.add_metadata(name, values)
    events: list[tuple[int, int, int, int]] = []  # (log_time, stream idx, per-stream seq, channel_id)
    for idx, s in enumerate(streams):
        schema_id = w.register_schema(name=s.schema_name, encoding="jsonschema", data=b'{"type":"object"}')
        channel_id = w.register_channel(topic=s.topic, message_encoding=s.message_encoding, schema_id=schema_id)
        for seq, t in enumerate(sorted(s.log_times_ns)):
            events.append((t, idx, seq, channel_id))
    for t, _idx, seq, channel_id in sorted(events):
        w.add_message(
            channel_id=channel_id,
            log_time=t,
            publish_time=t,
            sequence=seq,
            data=json.dumps({"seq": seq} if not payload_pad else {"seq": seq, "pad": "x" * payload_pad}).encode(),
        )
    w.finish()
    return out.getvalue()


def valid_bytes() -> bytes:
    return build_mcap([joint_states(), camera(), diagnostics()], metadata=DEFAULT_METADATA)


def cadence_gap_bytes() -> bytes:
    # /joint_states drops indices 25..49: last message at +0.48 s, next at +1.00 s => 0.52 s gap
    # against a 0.04 s threshold. Other topics untouched.
    return build_mcap([joint_states(skip=range(25, 50)), camera(), diagnostics()], metadata=DEFAULT_METADATA)


def missing_topic_bytes() -> bytes:
    return build_mcap([joint_states(), diagnostics()], metadata=DEFAULT_METADATA)  # no /camera/image


def truncated_bytes() -> bytes:
    v = valid_bytes()
    return v[: int(len(v) * 0.6)]  # cut mid-file: no footer, no end magic


def corrupted_bytes() -> bytes:
    v = bytearray(valid_bytes())
    v[int(len(v) * 0.5)] ^= 0xFF  # flip one byte inside the data section
    return bytes(v)


def not_mcap_bytes() -> bytes:
    return b"This is not an MCAP file. It is plain text.\n"


TRIGGER_COMPATIBLE_SOURCE = "Synthetic fixture: trigger and recording log_time stamped from one simulated clock"


def _trigger(trigger_id: str, ts: int, basis: str, compat: dict | None) -> bytes:
    d: dict = {"trigger_id": trigger_id, "timestamp_ns": ts, "timestamp_basis": basis}
    if compat is not None:
        d["basis_compatibility"] = compat
    return (json.dumps(d, indent=2) + "\n").encode()


def trigger_files() -> dict[str, bytes]:
    inside = BASE_NS + NS  # 1.0 s into the recording
    attested = {"compatible_with_log_time": True, "established_by": TRIGGER_COMPATIBLE_SOURCE}
    return {
        "trigger_unknown_basis.json": _trigger("trg-unknown-basis", inside, "unknown", None),
        "trigger_labels_match_unattested.json": _trigger("trg-unattested", inside, "system_time", None),
        "trigger_declared_incompatible.json": _trigger(
            "trg-incompatible", inside, "sim_time",
            {"compatible_with_log_time": False, "established_by": "Synthetic fixture: trigger from a different simulator clock"},
        ),
        "trigger_attested_within.json": _trigger("trg-attested-within", inside, "system_time", attested),
        "trigger_attested_after.json": _trigger("trg-attested-after", BASE_NS + 10 * NS, "system_time", attested),
    }


def metadata_file_bytes() -> bytes:
    d = {"operator": "alice", "session_label": "synthetic-supplied", "note": "synthetic fixture metadata"}
    return (json.dumps(d, indent=2) + "\n").encode()


def all_fixture_files() -> dict[str, bytes]:
    """Relative path -> exact bytes, for every committed fixture."""
    files = {
        "valid.mcap": valid_bytes(),
        "cadence_gap.mcap": cadence_gap_bytes(),
        "missing_topic.mcap": missing_topic_bytes(),
        "truncated.mcap": truncated_bytes(),
        "corrupted.mcap": corrupted_bytes(),
        "not_mcap.mcap": not_mcap_bytes(),
        "metadata.json": metadata_file_bytes(),
    }
    for name, data in trigger_files().items():
        files[f"triggers/{name}"] = data
    return files


def write_fixtures(out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    written = []
    for rel, data in all_fixture_files().items():
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        written.append(p)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("examples/fixtures")
    for p in write_fixtures(target):
        print(f"wrote {p} ({p.stat().st_size} bytes)")
