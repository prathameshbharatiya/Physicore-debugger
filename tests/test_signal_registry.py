from decimal import Decimal

import pytest

from physicore_collector.investigator.signal_registry import (
    RegistryError,
    SignalDefinition,
    SignalRegistry,
    UnitTransform,
    registry_from_mapping,
)


def test_alias_resolution_and_exact_unit_conversion_preserve_source_provenance():
    registry = SignalRegistry("example_arm", (
        SignalDefinition(
            canonical_name="joint_1.position",
            topic="/joint_states",
            field_path="position.0",
            semantic="joint_position",
            unit="rad",
            aliases=("shoulder.position",),
            transforms=(UnitTransform("deg", "rad", "0.01745329251994329576923690768", rationale="exact configured factor"),),
        ),
    ))

    result = registry.normalize("shoulder.position", Decimal("180"), source_unit="deg")

    assert result.robot_family == "example_arm"
    assert result.canonical_name == "joint_1.position"
    assert result.source_topic == "/joint_states"
    assert result.source_field_path == "position.0"
    assert result.source_value == "180"
    assert result.source_unit == "deg"
    assert result.normalized_unit == "rad"
    assert result.normalized_value == "3.14159265358979323846260938240"
    assert result.conversion.startswith("deg->rad; scale=")


def test_identity_conversion_does_not_change_value():
    registry = SignalRegistry("mobile_base", (
        SignalDefinition("base.velocity", "/odom", "twist.linear.x", "linear_velocity", "m/s"),
    ))

    result = registry.normalize("base.velocity", "1.250", source_unit="m/s")

    assert result.normalized_value == "1.250"
    assert result.conversion == "identity"


def test_unknown_alias_is_not_guessed():
    registry = SignalRegistry("mobile_base", (SignalDefinition(
        "base.velocity", "/odom", "twist.linear.x", "linear_velocity", "m/s",
    ),))

    with pytest.raises(RegistryError, match="not registered"):
        registry.normalize("velocity", 1, source_unit="m/s")


def test_missing_or_unconfigured_unit_conversion_is_rejected():
    registry = SignalRegistry("mobile_base", (SignalDefinition(
        "base.velocity", "/odom", "twist.linear.x", "linear_velocity", "m/s",
    ),))

    with pytest.raises(RegistryError, match="no explicit conversion"):
        registry.normalize("base.velocity", 10, source_unit="km/h")


def test_boolean_and_non_finite_values_are_rejected():
    registry = SignalRegistry("mobile_base", (SignalDefinition(
        "base.velocity", "/odom", "twist.linear.x", "linear_velocity", "m/s",
    ),))

    with pytest.raises(RegistryError, match="boolean"):
        registry.normalize("base.velocity", True, source_unit="m/s")
    with pytest.raises(RegistryError, match="finite"):
        registry.normalize("base.velocity", "NaN", source_unit="m/s")


def test_duplicate_alias_is_rejected():
    with pytest.raises(RegistryError, match="ambiguous"):
        SignalRegistry("robot", (
            SignalDefinition("a", "/a", "x", "position", "m", aliases=("shared",)),
            SignalDefinition("b", "/b", "x", "position", "m", aliases=("shared",)),
        ))


def test_mapping_loader_rejects_unknown_keys_and_builds_transforms():
    registry = registry_from_mapping("arm", {"signals": [{
        "canonical_name": "joint.position",
        "topic": "/joint_states",
        "field_path": "position.0",
        "semantic": "joint_position",
        "unit": "rad",
        "aliases": ["j1.position"],
        "transforms": [{"source_unit": "deg", "target_unit": "rad", "scale": "0.01745329251994329576923690768"}],
    }]})
    assert registry.resolve("j1.position").canonical_name == "joint.position"

    with pytest.raises(RegistryError, match="unknown keys"):
        registry_from_mapping("arm", {"signals": [], "defaults": {}})
