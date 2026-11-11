"""Loaders for the optional trigger and metadata JSON files.

Both are strict JSON (duplicate keys rejected), read in one streaming pass so the
SHA-256 and the parsed content describe the same bytes. The exact original bytes are
kept so export can preserve them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .checksums import FileTooLargeError, read_and_hash
from .errors import MetadataError, PhysiCoreError, TriggerError
from .issues import format_issue, json_no_duplicates
from .schemas import Trigger

MAX_INPUT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class LoadedMetadata:
    data: dict[str, Any]
    raw: bytes
    sha256: str
    path: Path


@dataclass(frozen=True)
class LoadedTrigger:
    trigger: Trigger
    raw: bytes
    sha256: str
    path: Path


def _read_json_object(
    path: str | Path, kind: str, error_cls: type[PhysiCoreError], max_bytes: int
) -> tuple[dict[str, Any], bytes, str, Path]:
    p = Path(path)
    if not p.exists():
        raise error_cls(f"{kind} file not found: '{p}'", hint="Check the path.")
    if not p.is_file():
        raise error_cls(f"{kind} path is not a regular file: '{p}'")
    try:
        raw, digest = read_and_hash(p, max_bytes=max_bytes)
    except FileTooLargeError as e:
        raise error_cls(f"{kind} file '{p}' is larger than {e.max_bytes} bytes.") from e
    except OSError as e:
        raise error_cls(f"Cannot read {kind} file '{p}': {e.strerror or e}") from e
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise error_cls(f"{kind} file '{p}' is not valid UTF-8 text (byte offset {e.start}).") from e
    try:
        data = json.loads(text, object_pairs_hook=json_no_duplicates)
    except json.JSONDecodeError as e:
        raise error_cls(
            f"{kind} file '{p}' is not valid JSON at line {e.lineno}, column {e.colno}: {e.msg}"
        ) from e
    except ValueError as e:  # duplicate keys
        raise error_cls(f"{kind} file '{p}' is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise error_cls(
            f"{kind} file '{p}' must contain a JSON object at the top level, got {type(data).__name__}."
        )
    return data, raw, digest, p


def load_metadata(path: str | Path, *, max_bytes: int = MAX_INPUT_BYTES) -> LoadedMetadata:
    data, raw, digest, p = _read_json_object(path, "Metadata", MetadataError, max_bytes)
    return LoadedMetadata(data=data, raw=raw, sha256=digest, path=p)


def load_trigger(path: str | Path, *, max_bytes: int = MAX_INPUT_BYTES) -> LoadedTrigger:
    data, raw, digest, p = _read_json_object(path, "Trigger", TriggerError, max_bytes)
    try:
        trig = Trigger.model_validate(data)
    except ValidationError as e:
        issues = [format_issue(err) for err in e.errors(include_url=False)]
        lines = "\n".join(f"  - {i}" for i in issues)
        raise TriggerError(
            f"Trigger file '{p}' failed validation ({len(issues)} error(s)):\n{lines}",
            hint="A trigger needs trigger_id, timestamp_ns (integer >= 0) and timestamp_basis.",
        ) from e
    return LoadedTrigger(trigger=trig, raw=raw, sha256=digest, path=p)
