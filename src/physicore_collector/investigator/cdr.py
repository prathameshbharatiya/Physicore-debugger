"""Isolated adapter for CDR payloads in MCAP messages whose schema is an embedded `ros2msg` definition (M3a).

Nothing outside this module imports the third-party decoder. If it is not installed, every function here still works and reports
`decoder_unavailable`; no import error can escape.

Decoder: `mcap-ros2-support` (optional extra `ros2`). It decodes from the definition stored IN the file, so no ROS 2 install and no
message packages are needed. It supports `ros2msg` schemas only (not `ros2idl`), and not `wstring`. See docs/M3A_DECODING.md for
license / maintenance / dependency findings.

What this module guarantees (each has a test):
  * A failure is a STATUS, never an exception and never a made-up value: see `DecodeStatus`.
  * Values keep their identity: integers exactly, float64 as its shortest round-trip decimal, float32 as the shortest decimal that
    round-trips as float32 (so a float32 0.1 reads 0.1), booleans and strings unchanged. NaN/Inf is flagged (`non_finite`), not converted.
  * Signal paths resolve against the typed schema: a leaf's ROS type (`float32`, `int32`, ...) is reported with the value.
  * Image / point-cloud style payloads are refused (`payload_type_out_of_scope`), not decoded.
Not guaranteed: CDR has no checksum, so a corrupted but structurally valid payload decodes to wrong values (the MCAP chunk CRC is the
integrity layer); bytes trailing a valid message are not detected (the decoder does not report how much it consumed).
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import re
import struct
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Literal

DECODER_PACKAGE = "mcap-ros2-support"
MAX_CDR_MESSAGE_BYTES = 8 * 1024 * 1024  # a message larger than this is not decoded (memory/time guard, not a format limit)

# Whole-topic and per-message outcomes. Only `ok` yields values.
DecodeStatus = Literal[
    "ok",
    "decoder_unavailable",          # the optional extra is not installed (or failed to import)
    "message_encoding_unsupported",  # channel message_encoding is neither json nor cdr
    "schema_missing",               # a cdr channel with no schema record
    "schema_encoding_unsupported",  # schema is not `ros2msg` (e.g. ros2idl, jsonschema)
    "schema_invalid",               # the ros2msg text could not be turned into a decoder
    "payload_type_out_of_scope",    # Image / PointCloud2 and similar: deliberately not decoded
    "payload_too_large",
    "payload_malformed",            # truncated or structurally invalid CDR
    "payload_unsupported_type",     # a type the decoder cannot read (wstring)
]

OUT_OF_SCOPE_TYPES = frozenset({
    "sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage", "sensor_msgs/msg/PointCloud2", "sensor_msgs/msg/PointCloud",
    "stereo_msgs/msg/DisparityImage", "theora_image_transport/msg/Packet",
})

_PRIMITIVES = frozenset({"bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16", "int32", "uint32",
                         "int64", "uint64", "string", "wstring"})
_MISSING = object()


# ---------------------------------------------------------------------------
# Decoder availability (the ONLY place the third-party package is imported)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecoderInfo:
    available: bool
    name: str = DECODER_PACKAGE
    version: str | None = None
    reason: str | None = None  # why it is unavailable


def _load_decoder_module() -> Any:
    """Import the decoder. Separate so tests can simulate a missing extra."""
    return importlib.import_module("mcap_ros2.decoder")


def decoder_info() -> DecoderInfo:
    try:
        _load_decoder_module()
    except Exception as e:  # any import-time failure: missing package, incompatible dependency, ...
        return DecoderInfo(False, reason=f"{type(e).__name__}: {e}"[:200])
    try:
        ver = importlib.metadata.version(DECODER_PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        ver = None
    return DecoderInfo(True, version=ver)


# ---------------------------------------------------------------------------
# The embedded definition: a small index of field types (NOT a CDR parser)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldType:
    base: str  # primitive name or a fully qualified message type
    array: bool  # any array form: [], [N], [<=N]


_SECTION = re.compile(r"^={80}\s*$")
_TYPE_TOKEN = re.compile(r"^([A-Za-z0-9_/]+)(\[[^\]]*\])?(<=\d+)?$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")


def _qualify(t: str) -> str:
    """`pkg/Type` -> `pkg/msg/Type` (ros2msg definitions use both spellings)."""
    if t.count("/") == 1:
        p, n = t.split("/")
        return f"{p}/msg/{n}"
    return t


def parse_schema_index(name: str, text: str) -> dict[str, dict[str, FieldType]] | None:
    """Field types of `name` and every dependent type in a ros2msg definition, or None if it cannot be indexed.
    Constants (`uint8 X=1`) and defaults are ignored. This only answers 'what is the type of this field'; decoding is the library's job."""
    types: dict[str, dict[str, FieldType]] = {}
    name = _qualify(name)
    cur_pkg = name.split("/", 1)[0] if "/" in name else ""
    cur: dict[str, FieldType] = {}
    types[name] = cur
    for raw in text.splitlines():
        if _SECTION.match(raw):
            continue
        if raw.startswith("MSG: "):
            cur_name = _qualify(raw[5:].strip())
            cur_pkg = cur_name.split("/", 1)[0] if "/" in cur_name else ""
            cur = types.setdefault(cur_name, {})
            continue
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        tm = _TYPE_TOKEN.match(parts[0])
        nm = _NAME.match(parts[1]) if len(parts) == 2 else None
        if tm is None or nm is None:
            return None
        if parts[1][nm.end():].lstrip().startswith("="):
            continue  # a constant, not a field
        base = tm.group(1)
        if base not in _PRIMITIVES:
            if "/" not in base:
                base = "std_msgs/msg/Header" if base == "Header" else f"{cur_pkg}/msg/{base}"
            else:
                base = _qualify(base)
        cur[nm.group(0)] = FieldType(base=base, array=tm.group(2) is not None)
    return types


# ---------------------------------------------------------------------------
# Signal paths
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    name: str
    selector: tuple[str, str] | None = None  # (sibling array field, value): pick the element at the position of `value` there


_SEG = re.compile(r"^([A-Za-z0-9_-]+)(?:\[([A-Za-z0-9_-]+)=([A-Za-z0-9_/:-]+)\])?$")


def parse_signal(path: str) -> list[Step] | None:
    steps = []
    for seg in path.split("."):
        m = _SEG.match(seg)
        if m is None:
            return None
        steps.append(Step(m.group(1), (m.group(2), m.group(3)) if m.group(2) else None))
    return steps


def signal_path_problem(path: str) -> str | None:
    """Why `path` is not a valid signal path, or None. Dotted keys; an all-digit step indexes a list; `field[key=value]` picks the
    element of array `field` at the position where the sibling array `key` holds `value` (e.g. `position[name=joint2]`)."""
    if not path:
        return "is empty"
    for seg in path.split("."):
        if _SEG.match(seg) is None:
            return (f"has an invalid segment {seg!r} (use dotted keys of letters, digits, '_' or '-'; digits index a list; "
                    "'field[key=value]' selects by a sibling array)")
    return None


@dataclass(frozen=True)
class Leaf:
    """Result of resolving a signal path on one message."""

    status: Literal["ok", "missing", "ambiguous", "not_a_leaf"]
    raw: Any = None  # the primitive as decoded (bool / int / float / str / Decimal)
    ros_type: str | None = None  # primitive ROS type of the leaf, when the message came from a typed schema


def _fields_of(node: Any) -> tuple[str, ...]:
    """Field names of a decoded message object (the decoder generates classes with __slots__; a namespace uses __dict__)."""
    slots = getattr(type(node), "__slots__", None)
    if slots:
        return tuple(slots) if not isinstance(slots, str) else (slots,)
    return tuple(vars(node)) if hasattr(node, "__dict__") else ()  # a plain SimpleNamespace keeps its fields in __dict__


def _is_message_object(node: Any) -> bool:
    return not isinstance(node, (dict, list, tuple, str, bytes, bytearray, memoryview, int, float, bool, Decimal)) \
        and type(node).__name__ != "array" and bool(_fields_of(node))


def _child(node: Any, name: str) -> Any:
    if isinstance(node, dict):
        return node.get(name, _MISSING)
    if isinstance(node, SimpleNamespace) or _is_message_object(node):
        return getattr(node, name, _MISSING) if name in _fields_of(node) else _MISSING
    if isinstance(node, (list, tuple, bytes, bytearray, memoryview)) or type(node).__name__ == "array":
        if name.isdigit():
            i = int(name)
            return node[i] if i < len(node) else _MISSING
    return _MISSING


def _is_container(v: Any) -> bool:
    return isinstance(v, (dict, SimpleNamespace, list, tuple, bytes, bytearray, memoryview)) or type(v).__name__ == "array" or _is_message_object(v)


def resolve(root: Any, steps: list[Step], types: dict[str, dict[str, FieldType]] | None, root_type: str | None) -> Leaf:
    """Walk `steps` through a decoded message (a dict from JSON, or a namespace from CDR). Never raises."""
    node, ty = root, root_type
    elem_type: str | None = None
    for st in steps:
        if st.selector is not None:
            key, val = st.selector
            names = _child(node, key)
            arr = _child(node, st.name)
            if names is _MISSING or arr is _MISSING or not isinstance(names, (list, tuple)):
                return Leaf("missing")
            hits = [i for i, n in enumerate(names) if n == val]
            if not hits:
                return Leaf("missing")
            if len(hits) > 1:
                return Leaf("ambiguous")
            nxt = _child(arr, str(hits[0]))
            if nxt is _MISSING:
                return Leaf("missing")
            ft = types[ty].get(st.name) if (types and ty in types) else None
            elem_type = ft.base if ft is not None else None
            node, ty = nxt, elem_type
            continue
        nxt = _child(node, st.name)
        if nxt is _MISSING:
            return Leaf("missing")
        if st.name.isdigit() and not isinstance(node, (dict, SimpleNamespace)) and not _is_message_object(node):
            node = nxt  # list element: the type stays that of the array's base
            continue
        ft = types[ty].get(st.name) if (types and ty in types) else None
        node, ty = nxt, (ft.base if ft is not None else None)
    if _is_container(node):
        return Leaf("not_a_leaf")
    return Leaf("ok", raw=node, ros_type=ty if ty in _PRIMITIVES else None)


def struct_type_at(types: dict[str, dict[str, FieldType]] | None, root_type: str | None, steps: list[Step]) -> str | None:
    """Declared (schema) type of the field at `steps`, e.g. 'builtin_interfaces/msg/Time'; None when it cannot be told (no schema,
    selector steps, unknown field). Reads the field-type index only; it never touches message bytes."""
    if not types or root_type is None:
        return None
    ty = root_type
    for st in steps:
        if st.selector is not None or ty not in types:
            return None
        if st.name.isdigit():
            continue  # an array element has the array's base type
        ft = types[ty].get(st.name)
        if ft is None:
            return None
        ty = ft.base
    return ty


def field_type_at(types: dict[str, dict[str, FieldType]] | None, root_type: str | None, steps: list[Step]) -> tuple[str, str | None]:
    """Like `struct_type_at`, but says WHY a type could not be told. -> ("ok", declared type) | ("field_missing", None) (a type on the path is
    indexed and has no such field) | ("type_unknown", None) (no index, or a type on the path is not in it, or a selector is used)."""
    if not types or root_type is None:
        return "type_unknown", None
    ty = root_type
    for st in steps:
        if st.selector is not None or ty not in types:
            return "type_unknown", None
        if st.name.isdigit():
            continue
        ft = types[ty].get(st.name)
        if ft is None:
            return "field_missing", None
        ty = ft.base
    return "ok", ty


def float32_text(x: float) -> str:
    """Shortest decimal text that reads back as exactly this float32."""
    for p in range(1, 10):
        s = f"{x:.{p}g}"
        try:
            if struct.unpack("<f", struct.pack("<f", float(s)))[0] == x:
                return s
        except OverflowError:  # a short candidate can round just past FLT_MAX: not a match, try more digits
            continue
    return repr(x)


def to_decimal(x: float, ros_type: str | None) -> Decimal:
    return Decimal(float32_text(x) if ros_type == "float32" else repr(x))


# ---------------------------------------------------------------------------
# Codec: one per distinct schema
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemaInfo:
    name: str
    encoding: str
    data: bytes
    sha256: str

    @staticmethod
    def of(name: str, encoding: str, data: bytes) -> "SchemaInfo":
        return SchemaInfo(name, encoding, data, hashlib.sha256(data).hexdigest())


@dataclass
class Decoded:
    """A decoded message plus what is needed to read leaves from it."""

    root: Any
    types: dict[str, dict[str, FieldType]] | None
    root_type: str


@dataclass
class DecodeOutcome:
    status: DecodeStatus
    message: Decoded | None = None
    detail: str | None = None


@dataclass
class Codec:
    schema: SchemaInfo
    status: DecodeStatus
    message_encoding: str = ""
    detail: str | None = None
    _decode: Callable[[bytes], Any] | None = None
    types: dict[str, dict[str, FieldType]] | None = None
    attempted: int = 0
    decoded: int = 0
    failures: dict[str, int] = field(default_factory=dict)

    @property
    def root_type(self) -> str:
        """The qualified ROS type name of the messages this codec reads (e.g. 'sensor_msgs/msg/JointState')."""
        return _qualify(self.schema.name)

    def decode(self, data: bytes) -> DecodeOutcome:
        if self.status != "ok" or self._decode is None:
            return DecodeOutcome(self.status, detail=self.detail)
        self.attempted += 1
        if len(data) > MAX_CDR_MESSAGE_BYTES:
            return self._fail("payload_too_large", f"{len(data)} bytes exceeds {MAX_CDR_MESSAGE_BYTES}")
        try:
            root = self._decode(data)
        except NotImplementedError as e:
            return self._fail("payload_unsupported_type", str(e) or "unsupported field type")
        except Exception as e:  # noqa: BLE001 - third-party decoder: its failure modes (struct.error, ValueError, IndexError, UnicodeDecodeError, ...) are not a closed set
            return self._fail("payload_malformed", f"{type(e).__name__}: {e}"[:200])
        self.decoded += 1
        return DecodeOutcome("ok", Decoded(root, self.types, _qualify(self.schema.name)))

    def _fail(self, status: DecodeStatus, detail: str) -> DecodeOutcome:
        self.failures[status] = self.failures.get(status, 0) + 1
        return DecodeOutcome(status, detail=detail)


def make_codec(schema: SchemaInfo | None, message_encoding: str) -> Codec:
    """The codec for one (message encoding, schema). Construction never raises."""
    if schema is None:
        return Codec(SchemaInfo("", "", b"", ""), "schema_missing", message_encoding, "the channel has no schema record")
    if message_encoding != "cdr":
        return Codec(schema, "message_encoding_unsupported", message_encoding, f"message encoding '{message_encoding}' is not decoded")
    if schema.encoding != "ros2msg":
        return Codec(schema, "schema_encoding_unsupported", message_encoding, f"schema encoding '{schema.encoding}' (only 'ros2msg' is supported)")
    if schema.name in OUT_OF_SCOPE_TYPES:
        return Codec(schema, "payload_type_out_of_scope", message_encoding, f"{schema.name} (image / point-cloud payloads are out of scope)")
    info = decoder_info()
    if not info.available:
        return Codec(schema, "decoder_unavailable", message_encoding, f"install the optional extra: pip install physicore-collector[ros2] ({info.reason})")
    try:
        from mcap.records import Schema as McapSchema
        mod = _load_decoder_module()
        fn = mod.DecoderFactory().decoder_for("cdr", McapSchema(id=1, name=schema.name, encoding=schema.encoding, data=schema.data))
    except Exception as e:  # noqa: BLE001 - parsing arbitrary definitions through third-party code
        return Codec(schema, "schema_invalid", message_encoding, f"{type(e).__name__}: {e}"[:200])
    if fn is None:
        return Codec(schema, "schema_invalid", message_encoding, "the decoder rejected the schema")
    types = parse_schema_index(schema.name, schema.data.decode("utf-8", "replace"))
    return Codec(schema, "ok", message_encoding, None, fn, types)


def read_leaf(msg: Decoded, steps: list[Step]) -> Leaf:
    return resolve(msg.root, steps, msg.types, msg.root_type)
