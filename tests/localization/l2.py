"""Recording-level (L2) fixtures: synthetic MCAP bags in which the REAL M3c detectors (and M2) produce the findings (TEST_PLAN 1).
Expected values in tests are computed from the declarative spec, never from detector or M3d code."""
from __future__ import annotations

import json
from pathlib import Path

from physicore_collector.anomaly.service import run_anomaly_detection
from tests import investigator_fixtures as F
from tests.anomaly import fixtures as X

BASE, MS = F.BASE, F.MS
LIMITS = {"max_retained_observations": 5, "max_text_bytes": 64, "max_findings_per_detector": 100}
NS = 1_000_000_000

LOG = "rcl_interfaces/msg/Log"
DIAG = "diagnostic_msgs/msg/DiagnosticArray"
GOAL = "actionlib_msgs/msg/GoalStatusArray"
BAT = "sensor_msgs/msg/BatteryState"
SCAN = "sensor_msgs/msg/LaserScan"
HDR = "std_msgs/msg/Header"
F64 = "std_msgs/msg/Float64"


def at(i, step=MS, start=BASE):
    return start + i * step


def stream(payloads, start=BASE, step=MS):
    return [(start + i * step, p) for i, p in enumerate(payloads)]


def log(level, name="nav", msg="m"):
    return {"level": level, "name": name, "msg": msg}


def diag(level, name="motor", hw="hw1"):
    return {"status": [{"level": level, "name": name, "hardware_id": hw, "message": "m", "values": []}]}


def goal(status, gid="g1"):
    return {"status_list": [{"goal_id": {"stamp": {"sec": 1, "nanosec": 2}, "id": gid}, "status": status, "text": ""}]}


def battery(health=1, serial="S1", location="slot0"):
    return {"voltage": 12.5, "percentage": 0.8, "power_supply_status": 2, "power_supply_health": health, "power_supply_technology": 3,
            "present": True, "serial_number": serial, "location": location}


def stamp_msg(ns):
    return {"header": {"stamp": {"sec": ns // NS, "nanosec": ns % NS}, "frame_id": "a"}}


def build_bag(tmp: Path, topics: dict[str, tuple[str, list]], *, segments: list[dict[str, list]] | None = None) -> Path:
    """One MCAP segment per entry of `segments` (default: a single segment holding every topic). `topics` = {topic: (ros type, messages)}."""
    types = {t: ty for t, (ty, _m) in topics.items()}
    segs = segments if segments is not None else [{t: m for t, (_ty, m) in topics.items()}]
    return X.write_typed_bag(tmp / "bag", segs, types)


def write_config(tmp: Path, detectors: dict, limits: dict | None = None) -> Path:
    p = tmp / "anomaly.json"
    p.write_text(json.dumps({"anomaly_config_version": 1, "limits": {**LIMITS, **(limits or {})}, "detectors": detectors}))
    return p


def m3c(tmp: Path, topics: dict[str, tuple[str, list]], detectors: dict, *, limits: dict | None = None, segments=None):
    bag = build_bag(tmp, topics, segments=segments)
    cfg = write_config(tmp, detectors, limits)
    return run_anomaly_detection(bag, cfg), bag, cfg


# detector configurations used across the suites
GAP = {"tierB.publishing_gap": {"enabled": True, "topics": ["/t"], "min_intervals": 10, "multiplier": "3", "absolute_floor_ns": 1}}
RATE = {"tierB.rate_change": {"enabled": True, "topics": ["/t"], "window_ns": NS, "min_windows": 3, "min_baseline_count": 2, "trim_start_windows": 0,
                              "trim_end_windows": 0, "low_ratio": "0.5", "high_ratio": "2"}}
STAMP = {"tierB.stamp_order": {"enabled": True, "topics": ["/t"], "fields": ["header.stamp"], "partition_by": []}}
ARR = {"tierB.array_length": {"enabled": True, "rules": [{"topic": "/t", "path": "ranges", "expected_length": 360}]}}
NONFIN = {"tierB.non_finite": {"enabled": True, "topics": ["/t"], "include_paths": [], "exclude_paths": []}}
