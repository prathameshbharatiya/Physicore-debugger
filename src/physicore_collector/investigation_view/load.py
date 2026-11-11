"""Strict loading of one M3d report (FR-VW-01..03). Never raises for a bad file; returns problems instead."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import MAX_REPORT_BYTES, SUPPORTED_SCHEMA


@dataclass
class Loaded:
    report: object | None = None            # LocalizationReport
    sha256: str = ""
    size: int = 0
    problems: list[str] = field(default_factory=list)
    unreadable: bool = False                # the file could not be read at all (exit 2) as opposed to being invalid (exit 3)


def _pairs(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def _no_const(name):
    raise ValueError(f"{name} is not allowed")


def _no_float(text):
    raise ValueError(f"non-integer number {text!r} is not allowed")


def parse_report_bytes(data: bytes) -> Loaded:
    from pydantic import ValidationError

    from physicore_collector.localization.output import LocalizationReport

    res = Loaded(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    if len(data) > MAX_REPORT_BYTES:
        res.problems.append(f"the report is larger than {MAX_REPORT_BYTES} bytes")
        return res
    if data.startswith(b"\xef\xbb\xbf"):
        res.problems.append("the report starts with a byte order mark")
        return res
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        res.problems.append(f"the report is not valid UTF-8 (byte {e.start})")
        return res
    try:
        obj = json.loads(text, object_pairs_hook=_pairs, parse_constant=_no_const, parse_float=_no_float)
    except (ValueError, RecursionError) as e:
        res.problems.append(f"the report is not valid JSON: {e}")
        return res
    if not isinstance(obj, dict):
        res.problems.append("the report is not a JSON object")
        return res
    ver = obj.get("localization_schema_version")
    if ver != SUPPORTED_SCHEMA:
        res.problems.append(f"unsupported localization_schema_version {ver!r}; only {SUPPORTED_SCHEMA!r} is accepted")
        return res
    try:
        res.report = LocalizationReport.model_validate(obj)
    except ValidationError as e:
        for err in e.errors()[:20]:
            res.problems.append(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}")
        if len(e.errors()) > 20:
            res.problems.append(f"... {len(e.errors()) - 20} more validation problem(s)")
    except (ValueError, RecursionError) as e:
        res.problems.append(f"invalid report: {e}")
    return res


def read_report(path: str | Path) -> Loaded:
    p = Path(path)
    try:
        size = p.stat().st_size
        if size > MAX_REPORT_BYTES:
            return Loaded(size=size, problems=[f"the report is larger than {MAX_REPORT_BYTES} bytes"])
        with open(p, "rb") as fh:
            data = fh.read(MAX_REPORT_BYTES + 1)
    except OSError as e:
        return Loaded(problems=[f"cannot read the report: {e.strerror or e}"], unreadable=True)
    return parse_report_bytes(data)
