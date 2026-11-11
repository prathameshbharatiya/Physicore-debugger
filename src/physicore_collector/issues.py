"""Shared helpers for turning parse/validation failures into readable text."""

from __future__ import annotations

from typing import Any


def format_loc(loc: tuple[Any, ...]) -> str:
    """('topics', 1, 'name') -> 'topics[1].name'; empty loc -> '(profile)'."""
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "(profile)"


def format_issue(err: dict[str, Any]) -> str:
    """One Pydantic error dict -> 'field.path: problem'."""
    loc = format_loc(tuple(err["loc"]))
    if err["type"] == "extra_forbidden":
        msg = "unknown field (not part of the schema; check spelling)"
    else:
        msg = err["msg"]
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
    return f"{loc}: {msg}"


def json_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook for json.loads that rejects duplicate keys."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out
