"""Strict parser for a rosbag2 `metadata.yaml`.

Format reference (read from the upstream source, not guessed): rosbag2_storage/include/
rosbag2_storage/yaml.hpp in https://github.com/ros2/rosbag2 (Apache-2.0). The file has one root
key `rosbag2_bagfile_information` holding: version, storage_identifier, duration
{nanoseconds}, starting_time {nanoseconds_since_epoch}, message_count,
topics_with_message_count[{topic_metadata{name,type,serialization_format,...}, message_count}],
compression_format, compression_mode, relative_file_paths[], files[{path, starting_time,
duration, message_count}] (version >= 5), custom_data (>= 6), ros_distro (>= 8).

rosbag2 writes this file when the writer *closes* (SequentialWriter::close), so a missing file is
consistent with an interrupted recording, but does not prove one.

This module only parses and validates structure. It never decides completeness and never touches
the recording files. Problems are returned as messages, not raised, so a malformed metadata file
becomes an explicit finding instead of aborting inspection of the segments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from .profile import UniqueKeyLoader
from .schemas import BagMetadataSummary

MAX_METADATA_BYTES = 8 * 1024 * 1024  # many topics x QoS can be large, but never this large
SUPPORTED_METADATA_VERSIONS = range(3, 10)  # 3..9 inclusive: the layouts this parser understands
ROOT_KEY = "rosbag2_bagfile_information"


@dataclass(frozen=True)
class ParsedBagMetadata:
    summary: BagMetadataSummary
    # (path, message_count) per `files[]` entry, in file order; empty for metadata version < 5
    file_entries: tuple[tuple[str, int], ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nonneg_int(node: dict, key: str, where: str, errors: list[str]) -> int | None:
    v = node.get(key)
    if not _is_int(v) or v < 0:
        errors.append(f"{where}.{key} must be a non-negative integer, got {v!r}")
        return None
    return v


def _nested_ns(node: dict, key: str, inner: str, where: str, errors: list[str]) -> int | None:
    sub = node.get(key)
    if not isinstance(sub, dict):
        errors.append(f"{where}.{key} must be a mapping with '{inner}', got {type(sub).__name__}")
        return None
    return _nonneg_int(sub, inner, f"{where}.{key}", errors)


def parse_bag_metadata(raw: bytes) -> tuple[ParsedBagMetadata | None, list[str]]:
    """Return (parsed, errors). `parsed` is None iff `errors` is non-empty."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        return None, [f"metadata.yaml is not valid UTF-8 text (byte offset {e.start})"]
    if not text.strip():
        return None, ["metadata.yaml is empty"]
    try:
        doc = yaml.load(text, Loader=UniqueKeyLoader)  # noqa: S506 (SafeLoader subclass)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        return None, [f"metadata.yaml is not valid YAML{where}: {getattr(e, 'problem', None) or e}"]
    if not isinstance(doc, dict) or not isinstance(doc.get(ROOT_KEY), dict):
        return None, [f"metadata.yaml has no '{ROOT_KEY}' mapping at the top level"]
    node: dict = doc[ROOT_KEY]

    errors: list[str] = []
    warnings: list[str] = []
    w = ROOT_KEY

    version = node.get("version")
    if not _is_int(version):
        errors.append(f"{w}.version must be an integer, got {version!r}")
        version_ok = False
    elif version not in SUPPORTED_METADATA_VERSIONS:
        errors.append(
            f"unsupported rosbag2 metadata version {version} (this tool understands "
            f"{SUPPORTED_METADATA_VERSIONS.start}..{SUPPORTED_METADATA_VERSIONS.stop - 1}); "
            "its fields are not interpreted"
        )
        version_ok = False
    else:
        version_ok = True

    storage = node.get("storage_identifier")
    if not isinstance(storage, str) or not storage:
        errors.append(f"{w}.storage_identifier must be a non-empty string, got {storage!r}")

    message_count = _nonneg_int(node, "message_count", w, errors)
    duration_ns = _nested_ns(node, "duration", "nanoseconds", w, errors)
    start_ns = _nested_ns(node, "starting_time", "nanoseconds_since_epoch", w, errors)

    paths = node.get("relative_file_paths")
    if not isinstance(paths, list) or not all(isinstance(x, str) and x for x in paths):
        errors.append(f"{w}.relative_file_paths must be a list of non-empty strings")
        paths = []
    elif not paths:
        errors.append(f"{w}.relative_file_paths is empty: the metadata lists no storage files")

    topic_counts: dict[str, int] = {}
    twmc = node.get("topics_with_message_count")
    if not isinstance(twmc, list):
        errors.append(f"{w}.topics_with_message_count must be a list")
    else:
        for i, entry in enumerate(twmc):
            loc = f"{w}.topics_with_message_count[{i}]"
            tm = entry.get("topic_metadata") if isinstance(entry, dict) else None
            name = tm.get("name") if isinstance(tm, dict) else None
            cnt = entry.get("message_count") if isinstance(entry, dict) else None
            if not isinstance(name, str) or not name:
                errors.append(f"{loc}.topic_metadata.name must be a non-empty string")
            elif not _is_int(cnt) or cnt < 0:
                errors.append(f"{loc}.message_count must be a non-negative integer, got {cnt!r}")
            elif name in topic_counts:
                errors.append(f"{loc}: topic {name!r} is listed twice")
            else:
                topic_counts[name] = cnt

    compression_format = compression_mode = None
    if _is_int(version) and version >= 3:
        for key in ("compression_format", "compression_mode"):
            v = node.get(key)
            if not isinstance(v, str):
                errors.append(f"{w}.{key} must be a string (may be empty), got {v!r}")
        compression_format = node.get("compression_format") if isinstance(node.get("compression_format"), str) else None
        compression_mode = node.get("compression_mode") if isinstance(node.get("compression_mode"), str) else None

    file_entries: list[tuple[str, int]] = []
    if _is_int(version) and version >= 5:
        files = node.get("files")
        if not isinstance(files, list):
            errors.append(f"{w}.files must be a list (metadata version {version})")
        else:
            for i, f in enumerate(files):
                loc = f"{w}.files[{i}]"
                if not isinstance(f, dict) or not isinstance(f.get("path"), str) or not f["path"]:
                    errors.append(f"{loc}.path must be a non-empty string")
                    continue
                c = _nonneg_int(f, "message_count", loc, errors)
                if c is not None:
                    file_entries.append((f["path"], c))
    else:
        warnings.append("metadata version < 5 has no per-file message counts; per-file comparison unavailable")

    custom: dict[str, str] = {}
    cd = node.get("custom_data")
    if cd is not None:
        if isinstance(cd, dict):
            custom = {str(k): str(v) for k, v in cd.items()}
        else:
            errors.append(f"{w}.custom_data must be a mapping when present")
    distro = node.get("ros_distro")
    ros_distro = distro if isinstance(distro, str) and distro else None

    if errors or not version_ok or message_count is None:
        return None, errors or ["metadata could not be interpreted"]

    return (
        ParsedBagMetadata(
            summary=BagMetadataSummary(
                metadata_version=version, storage_identifier=storage,
                compression_format=compression_format, compression_mode=compression_mode,
                message_count=message_count, ros_distro=ros_distro,
                starting_time_ns=start_ns, duration_ns=duration_ns,
                relative_file_paths=list(paths), topic_message_counts=topic_counts,
                custom_data=custom,
            ),
            file_entries=tuple(file_entries),
            warnings=tuple(warnings),
        ),
        [],
    )
