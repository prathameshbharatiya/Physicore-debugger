"""Generates the committed M3a example (examples/m3a/): a synthetic ROS 2 style MCAP with CDR payloads and embedded ros2msg schemas,
the exact decoded values expected from it, and an expectation contract that uses typed signals.

    python -m tests.make_cdr_example examples/m3a        (needs the `xcheck` extra: the CDR bytes come from the independent `rosbags` serializer)

Everything derives from fixed constants, so two runs give identical bytes. SYNTHETIC: not a recording from a robot or from ROS 2."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from tests import cdr_fixtures as cf
from tests.cdr_fixtures import t

RECORDING = "cdr_example.mcap"
TOPICS = ["/joint_states", "/odom", "/cmd_vel", "/status/mode", "/status/count", "/status/estop", "/temperature", "/battery"]


def channels() -> list[cf.Channel]:
    names = ["shoulder", "elbow"]
    return [
        ("/joint_states", "sensor_msgs/msg/JointState",
         [(t(i), cf.joint_state(t(i), names, [0.1 * i, 0.5 * i], [0.0, 0.25], [1.5, 2.5])) for i in range(5)]),
        ("/odom", "nav_msgs/msg/Odometry", [(t(i), cf.odometry(t(i), 0.5 * i, 0.25 * i, 0.5, 0.125)) for i in range(3)]),
        ("/cmd_vel", "geometry_msgs/msg/Twist", [(t(i), cf.twist((0.5, 0.0, 0.0), (0.0, 0.0, 0.125 * i))) for i in range(3)]),
        ("/status/mode", "std_msgs/msg/String", [(t(0), cf.string("IDLE")), (t(2), cf.string("RUNNING"))]),
        ("/status/count", "std_msgs/msg/Int32", [(t(0), cf.int32(-7)), (t(1), cf.int32(2_147_483_647))]),
        ("/status/estop", "std_msgs/msg/Bool", [(t(0), cf.boolean(False)), (t(3), cf.boolean(True))]),
        ("/temperature", "std_msgs/msg/Float64", [(t(0), cf.float64(21.5)), (t(1), cf.float64(0.1 + 0.2))]),
        ("/battery", "sensor_msgs/msg/BatteryState", [(t(0), cf.battery(t(0), 12.3, 0.87)), (t(2), cf.battery(t(2), 12.1, 0.85, present=False))]),
    ]


CONTRACT = {
    "contract_schema_version": "1.0",
    "contract_id": "m3a-cdr-example",
    "description": "Typed-signal expectations over CDR messages (synthetic).",
    "expectations": [
        {
            "expectation_id": "ELBOW-LIMIT",
            "description": "The elbow joint position must not exceed 1.0 rad.",
            "topic": "/joint_states", "signal": "position[name=elbow]",
            "clock_domain": "mcap_log_time", "check": {"kind": "range", "max": 1.0},
            "source": {"kind": "specification", "reference": "EXAMPLE-SPEC-1 (synthetic)"},
        },
        {
            "expectation_id": "BATTERY-NOT-LOW",
            "description": "Battery voltage (float32 on the wire) must stay at or above 12.0 V.",
            "topic": "/battery", "signal": "voltage",
            "clock_domain": "mcap_log_time", "check": {"kind": "range", "min": 12.0},
            "source": {"kind": "operator_supplied", "reference": "EXAMPLE-TICKET-2 (synthetic)"},
        },
    ],
}


def expected_decoded(recording: Path) -> dict:
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.decoding import decode_topic

    inv = inspect_evidence(recording)
    out: dict = {"recording": recording.name, "recording_sha256": hashlib.sha256(recording.read_bytes()).hexdigest(),
                 "note": "decoded leaf values per topic; numbers are exact decimal text, `ros_type` is the wire type", "topics": {}}
    for topic in TOPICS:
        v = decode_topic(inv, topic, limit=100)
        out["topics"][topic] = {"schema_name": v.schema_name, "schema_encoding": v.schema_encoding, "schema_sha256": v.schema_sha256,
                                "message_encoding": v.message_encoding, "status": v.status,
                                "messages": [m.model_dump(mode="json") for m in v.messages]}
    return out


def write_example(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rec = out / RECORDING
    cf.write_cdr_mcap(rec, channels())
    (out / "expectations.json").write_text(json.dumps(CONTRACT, indent=2) + "\n")
    (out / "expected_decoded.json").write_text(json.dumps(expected_decoded(rec), indent=2) + "\n")


if __name__ == "__main__":
    write_example(Path(sys.argv[1] if len(sys.argv) > 1 else "examples/m3a"))
