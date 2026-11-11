"""The only bridge from the anomaly package to the accepted investigator (DESIGN 2.5): message views with lazy decode-once caching,
a full-scan leaf walker, array-length access, schema-embedded constant extraction and the M3b stamp resolver (reused unchanged).
Nothing here imports the third-party decoder; only investigator/cdr.py does."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Iterator

from physicore_collector.investigator import cdr as cdrmod
from physicore_collector.investigator import engine as _engine
from physicore_collector.investigator import observations as _obs
from physicore_collector.investigator.observations import Evidence, Msg

from .clock import LOG_TIME_DOMAIN, ClockedValue


def normalise_type(name: str | None) -> str | None:
    """`pkg/Type` -> `pkg/msg/Type` (exact; no substring or heuristic matching)."""
    if not name:
        return None
    if name.count("/") == 1:
        p, n = name.split("/")
        return f"{p}/msg/{n}"
    return name


# ---------------------------------------------------------------- schema-embedded constants
_CONST = re.compile(r"^\s*(?:u?int(?:8|16|32|64)|byte|char|bool|float32|float64|string)\s+([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+)\s*(?:#.*)?$")
_SECTION = re.compile(r"^={80}\s*$")
_MSG = re.compile(r"^MSG:\s*(\S+)\s*$")


def schema_constants(schema_text: bytes | str | None, root_type: str | None) -> dict[str, dict[str, int]]:
    """Integer constants declared in a recorded ros2msg schema, by (normalised) message type. The first section belongs to `root_type`;
    later sections start with `MSG: pkg/Type`. Never raises; an unparsable schema yields {}."""
    if schema_text is None:
        return {}
    try:
        text = schema_text.decode("utf-8") if isinstance(schema_text, (bytes, bytearray)) else schema_text
    except UnicodeDecodeError:
        return {}
    out: dict[str, dict[str, int]] = {}
    cur = normalise_type(root_type)
    expect_name = False
    for line in text.splitlines():
        if _SECTION.match(line):
            expect_name = True
            continue
        if expect_name:
            m = _MSG.match(line.strip())
            cur = normalise_type(m.group(1)) if m else None
            expect_name = False
            continue
        m = _CONST.match(line)
        if m and cur:
            out.setdefault(cur, {})[m.group(1)] = int(m.group(2))
    return out


# ---------------------------------------------------------------- decode accounting
@dataclass
class DecodeCounter:
    total: int = 0
    per_message: dict[tuple[int, int], int] = field(default_factory=dict)
    track_each: bool = True            # per-message tally is O(messages) memory: tests keep it, production runs switch it off
    labels: dict[tuple[str, str], str] = field(default_factory=dict)   # (topic, field) -> stamp_type_check label (a pass over the topic: cached)

    def record(self, key: tuple[int, int]) -> None:
        self.total += 1
        if self.track_each:
            self.per_message[key] = self.per_message.get(key, 0) + 1

    def max_per_message(self) -> int:
        return max(self.per_message.values(), default=0)


@dataclass(frozen=True)
class DecodeFailure:
    kind: str
    detail: str | None = None


@dataclass(frozen=True)
class Leaf:
    path: str
    value: Any
    kind: str          # "int" | "float" | "bool" | "str" | "bytes" | "none" | "other"


class MessageView:
    """One recorded message as the detectors see it. Decoding is lazy and happens at most once per message, however many detectors ask."""

    def __init__(self, msg: Msg, ev: Evidence, counter: DecodeCounter):
        self._msg, self._ev, self._counter = msg, ev, counter
        self._decoded: Any = None
        self._done = False

    topic = property(lambda s: s._msg.topic)
    message_index = property(lambda s: s._msg.index)
    order = property(lambda s: s._msg.order)
    artifact_ref = property(lambda s: s._msg.artifact_ref)
    sha256 = property(lambda s: s._msg.segment_sha256)
    payload_kind = property(lambda s: "cdr" if s._msg.codec is not None else "json")
    raw_size = property(lambda s: len(s._msg.data or b""))

    @property
    def log_time(self) -> ClockedValue:
        return ClockedValue(self._msg.log_time, LOG_TIME_DOMAIN)

    @property
    def log_time_ns(self) -> int:
        return self._msg.log_time

    def decoded(self) -> Any:
        """-> `cdr.Decoded` (CDR) | parsed JSON value | `DecodeFailure`. Never raises."""
        if not self._done:
            self._done = True
            self._counter.record((self._msg.order, self._msg.index))
            out = _obs.decode_message(self._msg)
            if isinstance(out, cdrmod.DecodeOutcome):
                self._decoded = out.message if (out.status == "ok" and out.message is not None) else DecodeFailure(str(out.status), out.detail)
            elif out is _obs._MISSING:
                self._decoded = DecodeFailure("json_unparseable")
            else:
                self._decoded = out
        return self._decoded

    def lenient_json(self) -> Any:
        """Only for JSON payloads the accepted loader refused (it rejects the NaN/Infinity tokens, which makes the whole message unparseable):
        parse the same bytes accepting those tokens as non-finite floats (DESIGN 6.6: tokens as the Python JSON parser accepts them). Never raises;
        -> parsed value or `DecodeFailure`. Not a second decode of a readable message: it runs only after `decoded()` failed."""
        import json
        if self._msg.codec is not None:
            return DecodeFailure("not_json")
        try:
            return json.loads((self._msg.data or b"").decode("utf-8"), parse_float=Decimal, parse_int=Decimal,
                              parse_constant=lambda tok: {"NaN": float("nan"), "Infinity": float("inf"), "-Infinity": float("-inf")}[tok])
        except Exception as e:  # noqa: BLE001
            return DecodeFailure("json_unparseable", type(e).__name__)

    @property
    def codec(self) -> Any:
        return self._msg.codec

    def root(self) -> Any:
        d = self.decoded()
        return d.root if isinstance(d, cdrmod.Decoded) else d

    def stamp(self, field_path: str) -> tuple[int | None, str]:
        """(exact ns or None, stamp_type_check label) by the accepted M3b rules, reused unchanged."""
        key = (self._msg.topic, field_path)
        label = self._counter.labels.get(key)
        if label is None:
            label = self._counter.labels[key] = _engine._stamp_type_check(self._ev, self._msg.topic, field_path)
        d = self.decoded()
        if isinstance(d, cdrmod.Decoded):
            return _obs.header_stamp_ns(d.root, field_path, d.types, d.root_type), label
        if isinstance(d, DecodeFailure):
            return None, label
        return _obs.header_stamp_ns(d, field_path, None, None), label

    def iter_leaves(self, selector: str | None = None) -> Iterator[Leaf]:
        return iter_leaves(self.root(), selector)

    def array_length(self, path: str) -> int | None:
        return array_length(self.root(), path)


# ---------------------------------------------------------------- leaf walker (full scan, no early exit)
def strict_int(v: Any) -> int | None:
    """An exact integer: a CDR int, or a JSON number written as an integer (the accepted loader parses every JSON number as Decimal, so
    `40` has exponent 0 and `40.0` does not). bool, float, string, null, NaN/Inf are not integers (strict, as in the M3b fix)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, Decimal) and v.is_finite() and v.as_tuple().exponent == 0:
        return int(v)
    return None


def _kind(v: Any) -> str:
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, Decimal):
        return "int" if strict_int(v) is not None else "float"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, (bytes, bytearray)):
        return "bytes"
    return "other"


def _is_seq(v: Any) -> bool:
    return isinstance(v, (list, tuple)) or type(v).__name__ in ("array", "ndarray")


def _is_msg(v: Any) -> bool:
    if isinstance(v, (dict, list, tuple, str, bytes, bytearray, memoryview, int, float, bool, Decimal)) or _is_seq(v):
        return False
    return isinstance(v, SimpleNamespace) or bool(cdrmod._fields_of(v))


def _children(node: Any) -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        return [(str(k), node[k]) for k in sorted(node, key=str)]
    return [(f, getattr(node, f)) for f in cdrmod._fields_of(node)]


def iter_leaves(root: Any, selector: str | None = None) -> Iterator[Leaf]:
    """Every scalar leaf, depth first, in field order (JSON objects in sorted key order). Arrays are walked element by element as `path[i]`;
    `bytes` are one leaf. No early exit: every leaf of the message is yielded. `selector`, if given, keeps only leaves whose path starts with it."""
    if isinstance(root, DecodeFailure):
        return
    stack: list[tuple[str, Any]] = [("", root)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, (bytes, bytearray)):
            leaf = Leaf(path, bytes(node), "bytes")
        elif _is_seq(node):
            for i in range(len(node) - 1, -1, -1):
                stack.append((f"{path}[{i}]", node[i]))
            continue
        elif isinstance(node, dict) or _is_msg(node):
            for name, child in reversed(_children(node)):
                stack.append((f"{path}.{name}" if path else name, child))
            continue
        else:
            v = node.item() if type(node).__name__.startswith(("float", "int", "uint", "bool")) and hasattr(node, "item") else node
            leaf = Leaf(path, v, _kind(v))
        if selector is None or leaf.path.startswith(selector):
            yield leaf


def array_length(root: Any, path: str) -> int | None:
    """Length of the sequence at a dotted `path`; None when the path is missing or not a sequence (strings and bytes are not arrays here)."""
    node = root
    if isinstance(node, DecodeFailure):
        return None
    for name in [p for p in path.split(".") if p]:
        node = cdrmod._child(node, name)
        if node is cdrmod._MISSING:
            return None
    if isinstance(node, (str, bytes, bytearray)) or not _is_seq(node):
        return None
    return len(node)


def field(node: Any, name: str) -> tuple[bool, Any]:
    """(present, value) of one named field of a decoded object: JSON dict key or CDR message attribute. A JSON null is present with value None."""
    v = cdrmod._child(node, name)
    return (False, None) if v is cdrmod._MISSING else (True, v)


def elements(node: Any) -> list[Any] | None:
    """The elements of a sequence-valued field as a list, or None if `node` is not a sequence (str/bytes are not sequences here)."""
    if isinstance(node, (str, bytes, bytearray)) or not _is_seq(node):
        return None
    return list(node)


def plain(v: Any) -> Any:
    """A JSON-parsed or decoded value as plain Python for display: integral Decimals become int, other Decimals text, sequences lists."""
    if isinstance(v, Decimal):
        i = strict_int(v)
        return i if i is not None else str(v)
    if isinstance(v, (list, tuple)) or type(v).__name__ in ("array", "ndarray"):
        return [plain(x) for x in v]
    if hasattr(v, "item") and type(v).__name__.startswith(("uint", "int", "float", "bool")):
        return v.item()
    return v


def number_text(v: Any) -> str | int | bool | None:
    """A scalar for the report: ints stay ints, bools stay bools, floats/Decimals become exact text ('nan', 'inf', '-inf' for non-finite),
    strings stay inert text. Null and containers -> None."""
    if isinstance(v, bool):
        return v
    v = plain(v) if not isinstance(v, (int, float, str, type(None))) else v
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return v          # a JSON Decimal rendered by plain(), or an inert string; never parsed
    return None


def exact_number(v: Any):
    """Fraction of a finite real number (CDR int/float, JSON Decimal), else None. bool, NaN, Inf, str, null are not numbers here."""
    from fractions import Fraction
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, Decimal)) or (isinstance(v, float)):
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            return None
        if isinstance(v, Decimal) and not v.is_finite():
            return None
        return Fraction(v)
    if hasattr(v, "item") and type(v).__name__.startswith(("uint", "int", "float")):
        return exact_number(v.item())
    return None


def is_real(v: Any) -> bool:
    """A real number as a value (not bool, str, null or container): CDR int/float, JSON Decimal, numpy scalar. NaN/Inf count as numbers."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float, Decimal)):
        return True
    return hasattr(v, "item") and type(v).__name__.startswith(("uint", "int", "float"))


def stamp_label(ev: Evidence, topic: str, field_path: str) -> str:
    """The accepted M3b stamp_type_check label (reused as is)."""
    return _engine._stamp_type_check(ev, topic, field_path)


def raw_stamp_text(root: Any, field_path: str) -> str:
    """The offending raw sec/nanosec of a stamp field as inert text (for the invalid-stamp ledger)."""
    node = root
    for part in [p for p in field_path.split(".") if p]:
        ok, node = field(node, part) if not isinstance(node, DecodeFailure) and node is not None else (False, None)
        if not ok:
            return "<stamp struct absent>"
    if not (isinstance(node, dict) or _is_msg(node)):
        return f"<not a struct: {type(node).__name__}>"
    out = []
    for n in ("sec", "nanosec"):
        ok, v = field(node, n)
        out.append(f"{n}={number_text(v) if ok and v is not None else ('null' if ok else '<absent>')!r}")
    return ", ".join(out)


def path_value(root: Any, field_path: str) -> tuple[bool, Any]:
    """(present, value) at a dotted path of struct fields."""
    node = root
    for part in [p for p in field_path.split(".") if p]:
        if isinstance(node, DecodeFailure):
            return False, None
        ok, node = field(node, part)
        if not ok:
            return False, None
    return True, node
