"""Schema field-kind index for the Tier B array/non-finite detectors. The accepted `cdr.parse_schema_index` records only THAT a field is an array;
DESIGN 6.5 needs to know which form (`T[]`, `T[<=N]`, `T[N]`) and 6.6.1 needs float32 vs float64, so this module reads the recorded ros2msg text itself
(read-only, never touches message bytes). Paths use `name`, `name[*]` (wildcard element) or `name[3]` (index) steps joined by dots."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SECTION = re.compile(r"^={80}\s*$")
_TOKEN = re.compile(r"^([A-Za-z0-9_/]+)(?:<=\d+)?(?:\[([^\]]*)\])?(?:<=\d+)?$")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")
_STEP = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[(\*|\d+)\])?$")
PRIMS = frozenset({"bool", "byte", "char", "float32", "float64", "int8", "uint8", "int16", "uint16", "int32", "uint32", "int64", "uint64",
                   "string", "wstring"})


@dataclass(frozen=True)
class FT:
    base: str
    kind: str          # "scalar" | "fixed" | "sequence" (unbounded or bounded)


def _q(t: str) -> str:
    if t.count("/") == 1:
        p, n = t.split("/")
        return f"{p}/msg/{n}"
    return t


def parse(root_type: str, text: str | bytes | None) -> dict[str, dict[str, FT]] | None:
    """{qualified type -> {field -> FT}} or None when the text cannot be indexed."""
    if text is None:
        return None
    if isinstance(text, (bytes, bytearray)):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError:
            return None
    root = _q(root_type)
    pkg = root.split("/", 1)[0] if "/" in root else ""
    types: dict[str, dict[str, FT]] = {}
    cur = types.setdefault(root, {})
    for raw in text.splitlines():
        if _SECTION.match(raw):
            continue
        if raw.startswith("MSG: "):
            name = _q(raw[5:].strip())
            pkg = name.split("/", 1)[0] if "/" in name else ""
            cur = types.setdefault(name, {})
            continue
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        tm = _TOKEN.match(parts[0])
        nm = _NAME.match(parts[1]) if len(parts) == 2 else None
        if tm is None or nm is None:
            return None
        if parts[1][nm.end():].lstrip().startswith("="):
            continue
        base = tm.group(1)
        if base not in PRIMS:
            base = "std_msgs/msg/Header" if base == "Header" else (_q(base) if "/" in base else f"{pkg}/msg/{base}")
        br = tm.group(2)
        kind = "scalar" if br is None else ("fixed" if br.isdigit() else "sequence")
        cur[nm.group(0)] = FT(base, kind)
    return types


def resolve(types: dict[str, dict[str, FT]] | None, root_type: str, path: str) -> tuple[str, FT | None]:
    """Declared type of the field at `path` (steps `a`, `a[*]`, `a[3]`). -> ("ok", FT of the LAST field) | ("field_missing", None) |
    ("not_resolvable", None) | ("bad_path", None). An `[*]`/`[i]` step on a non-array field is a bad path; an array step used WITHOUT an index
    before another step is a bad path as well (the element must be selected)."""
    if not types:
        return "not_resolvable", None
    steps = path.split(".")
    ty = _q(root_type)
    ft: FT | None = None
    for i, s in enumerate(steps):
        m = _STEP.match(s)
        if m is None:
            return "bad_path", None
        if ty not in types:
            return "not_resolvable", None
        ft = types[ty].get(m.group(1))
        if ft is None:
            return "field_missing", None
        last = i == len(steps) - 1
        if m.group(2) is not None:
            if ft.kind == "scalar":
                return "bad_path", None
            ft = FT(ft.base, "scalar") if not last else ft
        elif not last and ft.kind != "scalar":
            return "bad_path", None
        ty = ft.base
    return "ok", ft


def template(path: str) -> str:
    """Exact path -> template (`poses[3].position.x` -> `poses[*].position.x`)."""
    return re.sub(r"\[\d+\]", "[*]", path)
