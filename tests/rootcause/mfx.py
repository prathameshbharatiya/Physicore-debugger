"""Fixtures of the M4a tests: a valid system model (as a dict), writers for JSON and YAML, and a small recording."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

H64 = "ab" * 32


def valid() -> dict:
    return {
        "system_model_schema_version": 1, "declared_by": "ops team (test)", "system_name": "test robot",
        "operating_context": "indoor warehouse\nflat floor", "dependency_file_sha256": H64,
        "components": [
            {"id": "lidar", "role": "sensor", "description": "front lidar"},
            {"id": "nav", "role": "controller", "declared_by": "nav lead (test)"},
            {"id": "base", "role": "actuator"},
            {"id": "pc", "role": "compute"},
        ],
        "bindings": [
            {"component": "lidar", "role": "scan", "topic": "/lidar/scan", "field": "ranges", "type": "sensor_msgs/msg/LaserScan"},
            {"component": "nav", "role": "cmd", "topic": "/cmd_mode", "field": "data"},
            {"component": "base", "role": "state", "topic": "/robot_state", "field": "data", "declared_by": "base vendor (test)"},
            {"component": "pc", "role": "diag", "topic": "/diagnostics"},
        ],
        "versions": [
            {"component": "nav", "kind": "software", "name": "nav_stack", "version": "1.4.2", "digest": H64},
            {"component": "base", "kind": "hardware", "name": "motor_controller", "version": "rev C"},
        ],
        "constraints": [
            {"id": "c_range", "kind": "value_range", "component": "lidar", "role": "scan", "min": 0, "max": 12.5, "unit": "m"},
            {"id": "c_rate", "kind": "rate", "component": "lidar", "role": "scan", "min_hz": 8, "max_hz": 12},
            {"id": "c_deadline", "kind": "deadline", "trigger": {"component": "nav", "role": "cmd"}, "response": {"component": "base", "role": "state"}, "deadline_ns": 300000000},
            {"id": "c_redundancy", "kind": "redundancy_group", "members": ["lidar", "nav"], "min_healthy": 1},
        ],
        "interventions": [
            {"id": "i_param", "kind": "parameter_change", "component": "nav", "at_ns": 1_700_000_005_000_000_000, "clock_domain": "mcap_log_time",
             "parameter": "max_speed", "old_value": "0.5", "new_value": "0.8"},
            {"id": "i_restart", "kind": "restart", "component": "pc", "at_ns": 1_700_000_001_000_000_000, "clock_domain": "mcap_log_time"},
        ],
        "edges": [
            {"id": "e1", "from": "lidar", "to": "nav", "relation": "data_flow", "window_ns": 1_000_000_000},
            {"id": "e2", "from": "/cmd_mode", "to": "/robot_state", "relation": "command_response"},
        ],
    }


def mutated(fn) -> dict:
    d = copy.deepcopy(valid())
    fn(d)
    return d


def jbytes(d) -> bytes:
    return json.dumps(d).encode()


def ybytes(d) -> bytes:
    return yaml.safe_dump(d, sort_keys=False).encode()


def write(tmp: Path, d, name="model.json") -> Path:
    p = tmp / name
    p.write_bytes(ybytes(d) if name.endswith((".yaml", ".yml")) else jbytes(d))
    return p
