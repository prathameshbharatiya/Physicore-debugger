"""Load and validate a versioned robot profile (YAML or JSON).

Validation rules live in `schemas.RobotProfile`; this module only handles
file access, parsing, hashing, and turning failures into actionable errors.

Strictness beyond plain YAML/JSON parsers (silent data loss is worse than an error):
  * duplicate mapping keys are rejected (PyYAML and json would keep the last one);
  * the top level must be a mapping; empty files are rejected;
  * the SHA-256 is computed from the same bytes that were parsed.
"""

from __future__ import annotations

import collections.abc
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .checksums import FileTooLargeError, read_and_hash
from .errors import (
    InputError,
    ProfileNotFoundError,
    ProfileParseError,
    ProfileValidationError,
)
from .issues import format_issue, json_no_duplicates
from .schemas import RobotProfile

MAX_PROFILE_BYTES = 1024 * 1024  # profiles are small config files
MAX_ISSUES_SHOWN = 20
YAML_SUFFIXES = (".yaml", ".yml")
JSON_SUFFIXES = (".json",)


@dataclass(frozen=True)
class LoadedProfile:
    profile: RobotProfile
    sha256: str  # of the raw profile file bytes
    path: Path
    source_format: str  # "yaml" | "json"


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys instead of keeping the last."""

    def construct_mapping(self, node, deep=False):  # type: ignore[override]
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if isinstance(key, collections.abc.Hashable):
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        None, None, f"duplicate key {key!r}", key_node.start_mark
                    )
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


UniqueKeyLoader = _UniqueKeyLoader  # public alias: reused for rosbag2 metadata.yaml parsing


def _parse_yaml(text: str, path: Path) -> Any:
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 (SafeLoader subclass)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        problem = getattr(e, "problem", None) or str(e)
        raise ProfileParseError(
            f"Profile '{path}' is not valid YAML{where}: {problem}",
            hint="Check indentation (use spaces, not tabs), colons, and quoting.",
        ) from e


def _parse_json(text: str, path: Path) -> Any:
    try:
        return json.loads(text, object_pairs_hook=json_no_duplicates)
    except json.JSONDecodeError as e:
        raise ProfileParseError(
            f"Profile '{path}' is not valid JSON at line {e.lineno}, column {e.colno}: {e.msg}",
            hint="Check for missing commas/quotes and trailing commas (not allowed in JSON).",
        ) from e
    except ValueError as e:  # duplicate keys
        raise ProfileParseError(f"Profile '{path}' is not valid JSON: {e}") from e


# --------------------------------------------------------------------------
# Error formatting
# --------------------------------------------------------------------------


def _validation_error(e: ValidationError, path: Path) -> ProfileValidationError:
    errors = e.errors(include_url=False)
    issues = [format_issue(err) for err in errors]
    shown = issues[:MAX_ISSUES_SHOWN]
    lines = "\n".join(f"  - {i}" for i in shown)
    if len(issues) > len(shown):
        lines += f"\n  ... and {len(issues) - len(shown)} more"
    plural = "error" if len(issues) == 1 else "errors"
    hint = None
    if any(
        tuple(err["loc"]) == ("profile_schema_version",) and err["type"] == "string_type"
        for err in errors
    ):
        hint = 'Quote the version so YAML keeps it as text, e.g. profile_schema_version: "1.0"'
    return ProfileValidationError(
        f"Profile '{path}' failed validation ({len(issues)} {plural}):\n{lines}",
        hint=hint,
        issues=issues,
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def load_profile(path: str | Path, *, max_bytes: int = MAX_PROFILE_BYTES) -> LoadedProfile:
    """Load, parse, and validate a profile file. Raises a `PhysiCoreError`
    subclass on any problem; never returns a partially valid profile."""
    p = Path(path)
    if not p.exists():
        raise ProfileNotFoundError(
            f"Profile file not found: '{p}'",
            hint="Check the path; see examples/profiles/example_robot.profile.yaml for a template.",
        )
    if not p.is_file():
        raise ProfileNotFoundError(f"Profile path is not a regular file: '{p}'")

    suffix = p.suffix.lower()
    if suffix in YAML_SUFFIXES:
        fmt = "yaml"
    elif suffix in JSON_SUFFIXES:
        fmt = "json"
    else:
        raise InputError(
            f"Unsupported profile file extension {p.suffix!r} for '{p}'",
            hint="Use .yaml, .yml, or .json.",
        )

    try:
        raw, digest = read_and_hash(p, max_bytes=max_bytes)
    except FileTooLargeError as e:
        raise InputError(
            f"Profile '{p}' is larger than {e.max_bytes} bytes; this does not look like a profile."
        ) from e
    except OSError as e:
        raise InputError(f"Cannot read profile '{p}': {e.strerror or e}") from e

    try:
        text = raw.decode("utf-8-sig")  # tolerate a UTF-8 BOM
    except UnicodeDecodeError as e:
        raise ProfileParseError(
            f"Profile '{p}' is not valid UTF-8 text (byte offset {e.start}).",
            hint="Save the file as UTF-8.",
        ) from e

    if not text.strip():
        raise ProfileParseError(f"Profile '{p}' is empty.")

    data = _parse_yaml(text, p) if fmt == "yaml" else _parse_json(text, p)
    if not isinstance(data, dict):
        raise ProfileParseError(
            f"Profile '{p}' must contain a mapping/object at the top level, "
            f"got {type(data).__name__}.",
            hint="The file should start with fields such as profile_schema_version and topics.",
        )

    try:
        profile = RobotProfile.model_validate(data)
    except ValidationError as e:
        raise _validation_error(e, p) from e

    return LoadedProfile(profile=profile, sha256=digest, path=p, source_format=fmt)
