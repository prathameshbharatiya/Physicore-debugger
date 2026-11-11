import pytest
from pydantic import ValidationError

from physicore_collector.profile import load_profile
from physicore_collector.schemas import RobotProfile


def prof(**over):
    d = {"profile_schema_version": "1.1", "profile_id": "p", "topics": [{"name": "/a"}], "capture": {}}
    d.update(over)
    return d


def test_day1_profile_still_loads_and_has_no_capture(example_profile):
    lp = load_profile(example_profile)
    assert lp.profile.capture is None and lp.profile.profile_schema_version == "1.0"


def test_capture_requires_profile_version_1_1():
    with pytest.raises(ValidationError, match="requires profile_schema_version '1.1'"):
        RobotProfile.model_validate(prof(profile_schema_version="1.0"))
    assert RobotProfile.model_validate(prof()).capture.storage_id == "mcap"


def test_capture_defaults_are_small_and_safe():
    c = RobotProfile.model_validate(prof()).capture
    assert c.topics is None and c.max_bag_size_bytes is None and c.stop_timeout_seconds == 15.0 and c.min_free_disk_bytes is None


@pytest.mark.parametrize("cap, frag", [
    ({"storage_id": "sqlite3"}, "storage_id"),
    ({"topics": []}, "must not be empty"),
    ({"topics": ["no_slash"]}, "starting with '/'"),
    ({"topics": ["/a b"]}, "whitespace"),
    ({"topics": ["/a", "/a"]}, "duplicate capture topics"),
    ({"max_bag_size_bytes": 0}, "max_bag_size_bytes"),
    ({"max_bag_duration_seconds": -1}, "max_bag_duration_seconds"),
    ({"stop_timeout_seconds": 0}, "stop_timeout_seconds"),
    ({"stop_timeout_seconds": float("inf")}, "stop_timeout_seconds"),
    ({"unknown_key": 1}, "unknown_key"),
    ({"identity": {"pilot": "x"}}, "pilot"),
])
def test_invalid_capture_settings_are_rejected(cap, frag):
    with pytest.raises(ValidationError, match=frag):
        RobotProfile.model_validate(prof(capture=cap))


def test_profile_file_with_capture_loads_and_renders(tmp_path):
    from physicore_collector.render import render_profile
    p = tmp_path / "p.yaml"
    p.write_text('profile_schema_version: "1.1"\nprofile_id: x\ntopics: [{name: /a}]\ncapture: {max_bag_size_bytes: 5, topics: [/a]}\n')
    lp = load_profile(p)
    assert "capture:" in render_profile(lp) and "split size 5 B" in render_profile(lp)


def test_unknown_profile_version_message_lists_all_supported():
    # Day 3 deliberately added "1.2"; the message must list exactly the supported versions.
    with pytest.raises(ValidationError, match=r"\['1.0', '1.1', '1.2'\]"):
        RobotProfile.model_validate(prof(profile_schema_version="2.0"))


def test_shipped_capture_example_profile_loads():
    from pathlib import Path
    lp = load_profile(Path(__file__).parent.parent / "examples" / "profiles" / "capture_example.profile.yaml")
    assert lp.profile.capture.max_bag_size_bytes == 268435456 and lp.profile.capture.identity.robot_id == "arm-07"


# ---------------------------------------------------------------- Day 3: profile 1.2 monitoring settings


def test_monitoring_settings_require_profile_1_2():
    for field, value in (("heartbeat_seconds", 2.0), ("disk_warn_free_bytes", 10), ("disk_critical_free_bytes", 5)):
        with pytest.raises(ValidationError, match=f"capture.{field}.*require profile_schema_version '1.2'"):
            RobotProfile.model_validate(prof(capture={field: value}))  # prof() declares 1.1
        assert RobotProfile.model_validate(prof(profile_schema_version="1.2", capture={field: value}))


def test_disk_thresholds_must_be_ordered_and_positive():
    with pytest.raises(ValidationError, match="warn_free_bytes must be greater than"):
        RobotProfile.model_validate(prof(profile_schema_version="1.2", capture={"disk_warn_free_bytes": 5, "disk_critical_free_bytes": 5}))
    with pytest.raises(ValidationError, match="disk_critical_free_bytes"):
        RobotProfile.model_validate(prof(profile_schema_version="1.2", capture={"disk_critical_free_bytes": 0}))
    with pytest.raises(ValidationError, match="heartbeat_seconds"):
        RobotProfile.model_validate(prof(profile_schema_version="1.2", capture={"heartbeat_seconds": 0.1}))


def test_unused_monitoring_fields_do_not_leak_into_serialized_profiles():
    """A 1.1 profile must serialize exactly as under Day 2 (older readers forbid unknown keys), and
    round-trip; a 1.2 profile that sets the fields serializes them."""
    p11 = RobotProfile.model_validate(prof())
    dumped = p11.model_dump(mode="json")
    assert set(dumped["capture"]) == {"storage_id", "topics", "max_bag_size_bytes", "max_bag_duration_seconds",
                                      "stop_timeout_seconds", "min_free_disk_bytes", "identity"}
    assert RobotProfile.model_validate(dumped) == p11
    p12 = RobotProfile.model_validate(prof(profile_schema_version="1.2", capture={"disk_critical_free_bytes": 99}))
    d12 = p12.model_dump(mode="json")["capture"]
    assert d12["disk_critical_free_bytes"] == 99 and "disk_warn_free_bytes" not in d12
    assert RobotProfile.model_validate(p12.model_dump(mode="json")) == p12
