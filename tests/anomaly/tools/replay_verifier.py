"""Provenance replay verifier (TEST_PLAN X-03/X-05; test-only). For every finding and every retained observation of a report it re-reads the artifact
FILE with the plain `mcap` reader (not the accepted loader), and checks: sha256 of the file equals the reference and is listed in the finding's
`source`; a message exists at `message_index` (position in file order); its topic and log_time match; and, where the observation carries a
field_path and a value, the value found at that path matches (CDR payloads are re-decoded with the independent `rosbags` serializer).
Returns a list of human-readable problems; an empty list means everything replayed."""
from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from pathlib import Path

from mcap.reader import make_reader

_STEP = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and v.is_finite() else v
    if hasattr(v, "item") and type(v).__name__.startswith(("int", "uint", "float", "bool")):
        return v.item()
    return v


def _resolve(root, path: str):
    node = root
    for m in _STEP.finditer(path):
        name, idx = m.groups()
        if name is not None:
            node = node.get(name, KeyError) if isinstance(node, dict) else getattr(node, name, KeyError)
        else:
            try:
                node = node[int(idx)]
            except (IndexError, TypeError, KeyError):
                return KeyError
        if node is KeyError:
            return KeyError
    return node


def _cls(x):
    if isinstance(x, float) or (isinstance(x, Decimal) and not x.is_finite()):
        x = float(x)
        if math.isnan(x):
            return "nan"
        if math.isinf(x):
            return "inf" if x > 0 else "-inf"
    return None


def _decode(schema, channel, data):
    if channel.message_encoding == "json":
        return json.loads(data.decode("utf-8"), parse_float=Decimal, parse_int=Decimal, parse_constant=lambda t: {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}[t])
    from tests import cdr_fixtures as cf
    return cf.typestore().deserialize_cdr(data, schema.name)


def _value_ok(obs, found) -> str | None:
    want = obs.value
    f = _plain(found)
    if isinstance(want, str) and want in ("nan", "inf", "-inf"):
        return None if _cls(f) == want else f"expected non-finite class {want!r}, file has {f!r}"
    if hasattr(f, "__len__") and not isinstance(f, (str, bytes)):
        return None if len(f) == want else f"expected length {want}, file has {len(f)}"
    if isinstance(f, str):
        return None if isinstance(want, str) and f.startswith(want) else f"expected text {want!r}, file has {f[:40]!r}"
    if isinstance(f, bool) or isinstance(want, bool):
        return None if f == want else f"expected {want!r}, file has {f!r}"
    try:
        return None if Decimal(str(f)) == Decimal(str(want)) else f"expected {want!r}, file has {f!r}"
    except Exception:    # noqa: BLE001
        return f"cannot compare {want!r} with {f!r}"


def verify_report(report, root: Path) -> list[str]:
    problems: list[str] = []
    cache: dict[str, tuple[str, list]] = {}

    def messages(ref: str):
        if ref not in cache:
            p = Path(root) / ref.removeprefix("artifact:")
            data = p.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            with open(p, "rb") as fh:
                rows = [(s, c, m) for s, c, m in make_reader(fh).iter_messages(log_time_order=False)]
            cache[ref] = (sha, rows)
        return cache[ref]

    for f in report.findings:
        listed = {(s.artifact_ref, s.sha256) for s in f.source}
        for o in f.observations:
            where = f"{f.finding_id} {o.artifact_ref}#{o.message_index}"
            try:
                sha, rows = messages(o.artifact_ref)
            except OSError as e:
                problems.append(f"{where}: cannot re-read the artifact: {e}")
                continue
            if sha != o.sha256:
                problems.append(f"{where}: file sha256 {sha[:12]} != reference {o.sha256[:12]}")
            if (o.artifact_ref, o.sha256) not in listed:
                problems.append(f"{where}: artifact not listed in the finding's source")
            if not 0 <= o.message_index < len(rows):
                problems.append(f"{where}: no message at that index")
                continue
            schema, channel, msg = rows[o.message_index]
            if channel.topic != o.topic:
                problems.append(f"{where}: topic {channel.topic} != {o.topic}")
            if msg.log_time != o.log_time_ns:
                problems.append(f"{where}: log_time {msg.log_time} != {o.log_time_ns}")
            if o.field_path is not None and o.value is not None:
                try:
                    found = _resolve(_decode(schema, channel, msg.data), o.field_path)
                except Exception as e:    # noqa: BLE001
                    problems.append(f"{where}: cannot decode independently: {type(e).__name__}")
                    continue
                if found is KeyError:
                    problems.append(f"{where}: {o.field_path} not found in the message")
                else:
                    bad = _value_ok(o, found)
                    if bad:
                        problems.append(f"{where}: {o.field_path}: {bad}")
            if o.payload_stamp is not None:
                try:
                    stamp_field = o.payload_stamp.clock_domain.split(":", 2)[2]               # payload_stamp:<topic>:<field>
                    st = _resolve(_decode(schema, channel, msg.data), stamp_field)
                    ns = int(_plain(_resolve(st, "sec"))) * 10**9 + int(_plain(_resolve(st, "nanosec")))
                    if ns != o.payload_stamp.value_ns:
                        problems.append(f"{where}: stamp {ns} != {o.payload_stamp.value_ns}")
                except Exception as e:    # noqa: BLE001
                    problems.append(f"{where}: stamp not replayable: {type(e).__name__}")
    return problems
