"""Robot-family signal registries and explicit, provenance-preserving unit normalization.

A registry is configuration, not a source of observations: callers must supply the raw value
and its declared source unit. No unit is inferred from a topic or field name. Conversions are
affine transforms expressed as exact decimal strings and are retained in the result.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Mapping


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
        if set(item) - allowed:
            raise RegistryError(f"signals[{index}] has unknown keys: {sorted(set(item) - allowed)}")
        transforms = tuple(UnitTransform(**t) for t in item.get("transforms", ()))
        definitions.append(SignalDefinition(
            canonical_name=str(item["canonical_name"]),
            topic=str(item["topic"]),
            field_path=str(item["field_path"]),
            semantic=str(item["semantic"]),
            unit=item.get("unit"),
            aliases=tuple(item.get("aliases", ())),
            transforms=transforms,
            source_unit=item.get("source_unit"),
        ))
    return SignalRegistry(robot_family, tuple(definitions))
