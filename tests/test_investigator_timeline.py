import pytest

from physicore_collector.investigator.timeline import (
    EventSource, TimelineEvent, build_timeline, context_window,
)


def source(index=0):
    return EventSource("run.mcap", "/joint_states", index, "a" * 64, f"run.mcap#{index}")


def event(event_id, timestamp, domain="mcap_log_time", *, kind="sensor", time_kind="observed", index=0):
    return TimelineEvent(event_id, kind, event_id, timestamp, domain, time_kind, source(index))


def test_timeline_orders_events_only_inside_each_clock_domain():
    timeline = build_timeline([
        event("later-log", 20),
        event("early-log", 10),
        event("payload", 1, "payload_stamp:/imu:header.stamp"),
    ])

    assert [group.clock_domain for group in timeline.groups] == [
        "mcap_log_time", "payload_stamp:/imu:header.stamp",
    ]
    assert [e.event_id for e in timeline.groups[0].events] == ["early-log", "later-log"]
    assert timeline.cross_domain_ordering == "not_established"


def test_unplaced_events_and_inferred_time_remain_explicit():
    unplaced = TimelineEvent("unknown", "recorder", "timestamp unavailable", None, None, "observed", source())
    inferred = event("estimated", 12, time_kind="inferred")
    timeline = build_timeline([unplaced, inferred])

    assert [e.event_id for e in timeline.unplaced] == ["unknown"]
    assert timeline.groups[0].events[0].time_kind == "inferred"


def test_context_window_is_limited_to_the_same_clock_group():
    group = build_timeline([event(f"e{i}", i) for i in range(6)]).groups[0]

    assert [e.event_id for e in context_window(group, "e3", before=1, after=2)] == ["e2", "e3", "e4", "e5"]


def test_duplicate_ids_and_undeclared_time_domains_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        build_timeline([event("same", 1), event("same", 2)])
    with pytest.raises(ValueError, match="declare a clock domain"):
        TimelineEvent("bad", "sensor", "bad", 1, None, "observed", source())


def test_invalid_message_reference_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        source(-1)
