"""Generates the committed M3b example (examples/m3b/): a SYNTHETIC ROS 2 style MCAP (CDR payloads, embedded ros2msg schemas) in which a
robot is e-stopped, closes a gripper and lifts, plus two expectation contracts (event_response on receipt time; one header.stamp range check) and their exact expected outputs.

    python -m tests.make_m3b_example examples/m3b        (needs extras `ros2` and `xcheck`)

Everything derives from fixed constants, so two runs give identical bytes. Not a recording from a robot or from ROS 2: it shows what the
check reports, not how a real system behaves."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests import cdr_fixtures as cf
from tests.cdr_fixtures import BASE, MS

RECORDING = "m3b_example.mcap"
SEC = 1_000_000_000
SIM0 = 5000 * SEC  # the publishers' (simulated) clock, deliberately unrelated to the recorder's log_time


def at(ms: int) -> int:
    return BASE + ms * MS


def sim(ms: int) -> int:
    """Header stamp: the publisher stamps 20 ms before the recorder receives the message."""
    return SIM0 + (ms - 20) * MS


def channels() -> list[cf.Channel]:
    lift = {0: 0.0, 250: 0.0, 500: 0.0, 750: 0.0, 1000: 0.0}
    grip_late = {2500: 0.2, 2750: 0.5}  # closes slowly: reaches 0.8 at 3000 ms
    js = []
    for ms in range(0, 4001, 250):
        lift_pos = lift.get(ms, 0.6)
        grip = 0.0 if ms < 2500 else grip_late.get(ms, 0.85)
        js.append((at(ms), cf.joint_state(sim(ms), ["lift", "gripper"], [lift_pos, grip])))
    odom = [(at(ms), cf.odometry(sim(ms), 0.5 * ms / 1000, 0.0, vx, 0.0))
            for ms, vx in [(0, 0.5), (250, 0.5), (500, 0.5), (750, 0.5), (1000, 0.5), (1250, 0.3), (1500, 0.0), (1750, 0.0), (2000, 0.0),
                           (2250, 0.0), (2500, 0.0), (2750, 0.0), (3000, 0.0), (3250, 0.0), (3500, 0.0), (3750, 0.0), (4000, 0.0)]]
    return [
        ("/mode", "std_msgs/msg/String", [(at(0), cf.string("AUTO")), (at(500), cf.string("AUTO"))]),
        ("/estop", "std_msgs/msg/Bool", [(at(0), cf.boolean(False)), (at(1000), cf.boolean(True)), (at(3500), cf.boolean(False))]),
        ("/cmd_vel", "geometry_msgs/msg/Twist",
         [(at(ms), cf.twist((x, 0.0, 0.0))) for ms, x in [(0, 0.5), (500, 0.5), (1150, 0.0), (2000, 0.0), (3000, 0.0)]]),
        ("/gripper/command", "std_msgs/msg/String", [(at(0), cf.string("OPEN")), (at(2000), cf.string("CLOSE")), (at(3500), cf.string("OPEN"))]),
        ("/joint_states", "sensor_msgs/msg/JointState", js),
        ("/odom", "nav_msgs/msg/Odometry", odom),
    ]


def _exp(eid: str, description: str, topic: str, signal: str, check: dict, **kw) -> dict:
    d = {"expectation_id": eid, "description": description, "topic": topic, "signal": signal, "clock_domain": "mcap_log_time", "check": check,
         "source": {"kind": "test_fixture", "reference": "tests/make_m3b_example.py"}}
    d.update(kw)
    return d


def _mode(max_age_ms: int) -> dict:
    return {"topic": "/mode", "signal": "data", "op": "eq", "value": "AUTO", "max_age_ns": max_age_ms * MS}


def contract(max_age_ms: int, cid: str) -> dict:
    return {"contract_schema_version": "1.0", "contract_id": cid, "description": "SYNTHETIC e-stop, gripper and lift timing expectations",
            "expectations": [
                _exp("ER-ESTOP", "While in AUTO mode (mode report no older than the limit), cmd_vel stops within 200 ms of the e-stop being pressed",
                     "/estop", "data", {"kind": "event_response", "trigger_op": "eq", "trigger_value": True,
                                        "response": {"topic": "/cmd_vel", "signal": "linear.x", "op": "eq", "value": 0}, "deadline_ns": 200 * MS},
                     applies_when=[_mode(max_age_ms)]),
                _exp("ER-GRIPPER", "The gripper reaches at least 0.8 within 500 ms of the CLOSE command",
                     "/gripper/command", "data", {"kind": "event_response", "trigger_op": "eq", "trigger_value": "CLOSE",
                                                  "response": {"topic": "/joint_states", "signal": "position[name=gripper]", "op": "ge", "value": 0.8},
                                                  "deadline_ns": 500 * MS}),
                _exp("ER-LIFT", "Odometry reports zero speed within 300 ms of the lift passing 0.5 (both received times)",
                     "/joint_states", "position[name=lift]",
                     {"kind": "event_response", "trigger_op": "gt", "trigger_value": 0.5,
                      "response": {"topic": "/odom", "signal": "twist.twist.linear.x", "op": "le", "value": 0}, "deadline_ns": 300 * MS}),
                _exp("HS-LIFT-LIMIT", "By the publisher's own header.stamp, the lift position never exceeds 0.7 (a single-signal check on a payload clock)",
                     "/joint_states", "position[name=lift]", {"kind": "range", "max": 0.7},
                     time_source="header_stamp", clock_domain="payload_stamp:/joint_states:header.stamp"),
            ]}


CONTRACT = contract(600, "m3b-example")
CONTRACT_STALE = contract(400, "m3b-example-stale-prerequisite")


def write_example(out: Path) -> None:
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator import investigate, investigation_json, parse_contract

    out.mkdir(parents=True, exist_ok=True)
    rec = out / RECORDING
    cf.write_cdr_mcap(rec, channels())
    for name, c in (("expectations.json", CONTRACT), ("expectations_stale_prerequisite.json", CONTRACT_STALE)):
        (out / name).write_text(json.dumps(c, indent=2) + "\n")
    for exp, outname in (("expectations.json", "expected_output.json"), ("expectations_stale_prerequisite.json", "expected_output_stale_prerequisite.json")):
        loaded = parse_contract(json.loads((out / exp).read_text()))
        assert loaded.contract is not None, loaded.problems
        (out / outname).write_text(investigation_json(investigate(inspect_evidence(rec), loaded)))


if __name__ == "__main__":
    write_example(Path(sys.argv[1] if len(sys.argv) > 1 else "examples/m3b"))
