"""Robot-family signal registries and explicit, provenance-preserving unit normalization.

A registry is configuration, not a source of observations: callers must supply the raw value
and its declared source unit. No unit is inferred from a topic or field name. Conversions are
affine transforms expressed as exact decimal strings and are retained in the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Mapping

import yaml


class RegistryError(ValueError):
    """Invalid or ambiguous robot signal registry."""


@dataclass(frozen=True)
class UnitTransform:
    """Exact conversion: normalized = source * scale + offset."""

    source_unit: str
    target_unit: str
    scale: str
    offset: str = "0"
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.source_unit or not self.target_unit:
            raise RegistryError("source_unit and target_unit are required")
        try:
            scale, offset = Decimal(self.scale), Decimal(self.offset)
        except InvalidOperation as exc:
            raise RegistryError("unit scale and offset must be decimal strings") from exc
        if not scale.is_finite() or scale == 0 or not offset.is_finite():
            raise RegistryError("unit scale must be finite and non-zero; offset must be finite")


@dataclass(frozen=True)
class SignalDefinition:
    canonical_name: str
    topic: str
    field_path: str
    semantic: str
    unit: str | None = None
    aliases: tuple[str, ...] = ()
    transforms: tuple[UnitTransform, ...] = ()
    source_unit: str | None = None

    def __post_init__(self) -> None:
        for name in (self.canonical_name, self.topic, self.field_path, self.semantic):
            if not name:
                raise RegistryError("canonical_name, topic, field_path and semantic are required")
        sources = [t.source_unit for t in self.transforms]
        if len(sources) != len(set(sources)):
            raise RegistryError(f"duplicate unit transform for {self.canonical_name}")


@dataclass(frozen=True)
class NormalizedSignal:
    robot_family: str
    canonical_name: str
    source_topic: str
    source_field_path: str
    source_value: str
    source_unit: str | None
    normalized_value: str
    normalized_unit: str | None
    conversion: str
    semantic: str


class SignalRegistry:
    """Immutable lookup for one robot family; aliases must be globally unambiguous."""

    def __init__(self, robot_family: str, definitions: tuple[SignalDefinition, ...]):
        if not robot_family:
            raise RegistryError("robot_family is required")
        self.robot_family = robot_family
        self.definitions = tuple(definitions)
        self._by_name: dict[str, SignalDefinition] = {}
        for definition in self.definitions:
            for name in (definition.canonical_name, *definition.aliases):
                if not name:
                    raise RegistryError("signal aliases must be non-empty")
                previous = self._by_name.get(name)
                if previous is not None and previous != definition:
                    raise RegistryError(f"ambiguous signal name or alias: {name}")
                self._by_name[name] = definition

    def resolve(self, name: str) -> SignalDefinition | None:
        """Resolve a canonical name or explicit alias; unknown names remain unknown."""
        return self._by_name.get(name)

    def normalize(self, name: str, raw_value: str | int | Decimal, *, source_unit: str | None) -> NormalizedSignal:
        definition = self.resolve(name)
        if definition is None:
            raise RegistryError(f"signal is not registered for robot family {self.robot_family}: {name}")
        if isinstance(raw_value, bool):
            raise RegistryError("boolean values cannot be normalized as numeric signals")
        try:
            raw = Decimal(str(raw_value))
        except (InvalidOperation, ValueError) as exc:
            raise RegistryError("signal value must be a finite decimal") from exc
        if not raw.is_finite():
            raise RegistryError("signal value must be finite")
        if source_unit != definition.unit:
            transform = next((t for t in definition.transforms if t.source_unit == source_unit), None)
            if transform is None:
                raise RegistryError(
                    f"no explicit conversion for {name}: {source_unit!r} to {definition.unit!r}"
                )
            if transform.target_unit != definition.unit:
                raise RegistryError("unit transform target does not match the registered unit")
            scale, offset = Decimal(transform.scale), Decimal(transform.offset)
            # Keep enough precision for the declared decimal operands; do not inherit the
            # process-wide Decimal default (28 digits) and silently round conversions.
            with localcontext() as context:
                context.prec = max(50, len(raw.as_tuple().digits) + len(scale.as_tuple().digits) + len(offset.as_tuple().digits) + 8)
                normalized = raw * scale + offset
            conversion = (
                f"{transform.source_unit}->{transform.target_unit}; "
                f"scale={transform.scale}; offset={transform.offset}"
                + (f"; rationale={transform.rationale}" if transform.rationale else "")
            )
        else:
            normalized = raw
            conversion = "identity"
        return NormalizedSignal(
            robot_family=self.robot_family,
            canonical_name=definition.canonical_name,
            source_topic=definition.topic,
            source_field_path=definition.field_path,
            source_value=str(raw),
            source_unit=source_unit,
            normalized_value=str(normalized),
            normalized_unit=definition.unit,
            conversion=conversion,
            semantic=definition.semantic,
        )


def registry_from_mapping(robot_family: str, data: Mapping[str, object]) -> SignalRegistry:
    """Build a registry from a plain mapping; reject unknown keys instead of silently ignoring them."""
    unknown = set(data) - {"signals"}
    if unknown:
        raise RegistryError(f"registry mapping has unknown keys: {sorted(unknown)}")
    if not isinstance(data.get("signals"), list):
        raise RegistryError("registry mapping must contain a 'signals' list")
    definitions: list[SignalDefinition] = []
    for index, item in enumerate(data["signals"]):
        if not isinstance(item, Mapping):
            raise RegistryError(f"signals[{index}] must be a mapping")
        allowed = {"canonical_name", "topic", "field_path", "semantic", "unit", "aliases", "transforms", "source_unit"}
        unknown = set(item) - allowed
        if unknown:
            raise RegistryError(f"signals[{index}] has unknown keys: {sorted(unknown)}")
        required = {"canonical_name", "topic", "field_path", "semantic"}
        missing = required - set(item)
        if missing:
            raise RegistryError(f"signals[{index}] is missing required keys: {sorted(missing)}")
        for key in ("canonical_name", "topic", "field_path", "semantic"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise RegistryError(f"signals[{index}].{key} must be a non-empty string")
        for key in ("unit", "source_unit"):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise RegistryError(f"signals[{index}].{key} must be a string")
        aliases = item.get("aliases", ())
        if not isinstance(aliases, (list, tuple)) or any(not isinstance(alias, str) or not alias for alias in aliases):
            raise RegistryError(f"signals[{index}].aliases must be a list of non-empty strings")
        raw_transforms = item.get("transforms", ())
        if not isinstance(raw_transforms, (list, tuple)) or any(not isinstance(t, Mapping) for t in raw_transforms):
            raise RegistryError(f"signals[{index}].transforms must be a list of mappings")
        try:
            transforms = tuple(UnitTransform(**t) for t in raw_transforms)
        except (TypeError, ValueError) as exc:
            raise RegistryError(f"signals[{index}] has an invalid unit transform: {exc}") from exc
        definitions.append(SignalDefinition(
            canonical_name=item["canonical_name"],
            topic=item["topic"],
            field_path=item["field_path"],
            semantic=item["semantic"],
            unit=item.get("unit"),
            aliases=tuple(aliases),
            transforms=transforms,
            source_unit=item.get("source_unit"),
        ))
    return SignalRegistry(robot_family, tuple(definitions))


def load_signal_registry(path: Path) -> SignalRegistry:
    """Load a robot-family registry from a bounded YAML or JSON file."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"cannot read signal registry: {exc}") from exc
    if len(raw) > 1024 * 1024:
        raise RegistryError("signal registry exceeds the 1 MiB limit")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RegistryError(f"invalid signal registry YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise RegistryError("signal registry root must be a mapping")
    if set(data) != {"robot_family", "signals"}:
        raise RegistryError("signal registry must contain exactly 'robot_family' and 'signals'")
    family = data["robot_family"]
    if not isinstance(family, str) or not family.strip():
        raise RegistryError("robot_family must be a non-empty string")
    return registry_from_mapping(family, {"signals": data["signals"]})
