"""Synthetic rosbag2 bag-directory builder for tests. No ROS 2 needed.

Produces a directory with real MCAP segments (via tests.make_fixtures) and a metadata.yaml in the
layout rosbag2 writes (metadata version 9; see src/physicore_collector/bag_metadata.py for the
upstream reference). The bytes are synthetic: this proves the Collector reads that LAYOUT, not
that a real rosbag2 recorder produces it (see DAY2_IMPLEMENTATION_REPORT.md for what was and was
not verified against real rosbag2).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests import make_fixtures as mf

TYPE_NAME = "synthetic/msg/Sample"


def split_by_time(streams: list[mf.StreamSpec], boundaries_ns: list[int]) -> list[list[mf.StreamSpec]]:
    """Cut every stream at the given absolute log_time boundaries -> len(boundaries)+1 segments."""
    edges = [-1, *boundaries_ns, 2**63]
    out: list[list[mf.StreamSpec]] = []
    for lo, hi in zip(edges, edges[1:], strict=False):
        seg = []
        for s in streams:
            ts = tuple(t for t in s.log_times_ns if lo < t <= hi) if lo >= 0 else tuple(t for t in s.log_times_ns if t <= hi)
            if ts:
                seg.append(mf.StreamSpec(s.topic, ts, s.message_encoding, s.schema_name))
        out.append(seg)
    return out


def standard_streams() -> list[mf.StreamSpec]:
    return [mf.joint_states(), mf.camera(), mf.diagnostics()]


def three_way_split() -> list[list[mf.StreamSpec]]:
    b = mf.BASE_NS
    # boundaries fall BETWEEN 50 Hz samples (20 ms apart): no artificial gap is introduced
    return split_by_time(standard_streams(), [b + 670_000_000 - 1, b + 1_330_000_000 - 1])


def metadata_dict(
    segment_files: list[tuple[str, list[mf.StreamSpec]]],
    *, version: int = 9, storage: str = "mcap", compression_mode: str = "", compression_format: str = "",
) -> dict:
    topics: dict[str, int] = {}
    allt: list[int] = []
    files = []
    for fname, streams in segment_files:
        times = [t for s in streams for t in s.log_times_ns]
        for s in streams:
            topics[s.topic] = topics.get(s.topic, 0) + len(s.log_times_ns)
        allt += times
        files.append({
            "path": fname,
            "starting_time": {"nanoseconds_since_epoch": min(times) if times else 0},
            "duration": {"nanoseconds": (max(times) - min(times)) if times else 0},
            "message_count": len(times),
        })
    info = {
        "version": version,
        "storage_identifier": storage,
        "duration": {"nanoseconds": (max(allt) - min(allt)) if allt else 0},
        "starting_time": {"nanoseconds_since_epoch": min(allt) if allt else 0},
        "message_count": len(allt),
        "topics_with_message_count": [
            {"topic_metadata": {"name": n, "type": TYPE_NAME, "serialization_format": "cdr",
                                "offered_qos_profiles": [], "type_description_hash": "RIHS01_0"},
             "message_count": c}
            for n, c in sorted(topics.items())
        ],
        "compression_format": compression_format,
        "compression_mode": compression_mode,
        "relative_file_paths": [f for f, _ in segment_files],
        "files": files,
        "custom_data": {},
        "ros_distro": "synthetic",
    }
    if version < 5:
        del info["files"]
    return {"rosbag2_bagfile_information": info}


def write_bag(
    root: Path,
    segments: list[list[mf.StreamSpec]] | None = None,
    *,
    with_metadata: bool = True,
    storage: str = "mcap",
    compression_mode: str = "",
    metadata_edit=None,
    name: str | None = None,
    mcap_metadata=mf.DEFAULT_METADATA,
    payload_pad: int = 0,
) -> Path:
    """Create `root` (must not exist) as a bag. `metadata_edit(dict)` may mutate the metadata
    dict before it is written. Returns `root`."""
    segments = segments if segments is not None else [standard_streams()]
    root.mkdir(parents=True)
    base = name or root.name
    ext = "mcap" if storage == "mcap" else "db3"
    files = []
    for i, streams in enumerate(segments):
        fname = f"{base}_{i}.{ext}"
        if storage == "mcap":
            (root / fname).write_bytes(mf.build_mcap(streams, metadata=mcap_metadata if i == 0 else None, payload_pad=payload_pad))
        else:
            (root / fname).write_bytes(b"SQLite format 3\x00 synthetic placeholder, not readable by this tool")
        files.append((fname, streams))
    if with_metadata:
        md = metadata_dict(files, storage=storage, compression_mode=compression_mode)
        if metadata_edit is not None:
            metadata_edit(md["rosbag2_bagfile_information"])
        (root / "metadata.yaml").write_text(yaml.safe_dump(md, sort_keys=False))
    return root
