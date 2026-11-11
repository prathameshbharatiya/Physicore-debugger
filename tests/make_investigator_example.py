"""Generates the committed Investigator example (examples/investigator/): a synthetic recording, an explicit expectation contract,
and the exact output the Investigator produces for them.

    python -m tests.make_investigator_example examples/investigator

Everything derives from fixed constants, so two runs give identical bytes; a test checks the committed files against this generator.
SYNTHETIC: not a recording from a real robot or from ROS 2."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from tests.investigator_fixtures import BASE, MS, build_json_mcap

RECORDING = "example_recording.mcap"


def streams():
    t = lambda ms: BASE + ms * MS  # noqa: E731
    return {
        "/arm/mode": [(t(0), {"state": "RUNNING"})],
        "/arm/temperature": [(t(100), {"celsius": 55.0}), (t(300), {"celsius": 62.5}), (t(500), {"celsius": 69.99}),
                             (t(700), {"celsius": 70.0}), (t(900), {"celsius": 70.01}), (t(1100), {"celsius": 70.0})],
        "/gripper/state": [(t(0), {"value": "IDLE"}), (t(500), {"value": "CLOSING"}), (t(800), {"value": "CLOSING"}),
                           (t(1100), {"value": "CLOSING"}), (t(1500), {"value": "CLOSED"}), (t(2000), {"value": "CLOSED"})],
    }


CONTRACT = {
    "contract_schema_version": "1.0",
    "contract_id": "example-arm-v1",
    "description": "Two supplied expectations for a synthetic arm recording.",
    "expectations": [
        {
            "expectation_id": "ARM-TEMP-LIMIT",
            "description": "While the arm is RUNNING its temperature must not exceed 70.0 C.",
            "topic": "/arm/temperature", "signal": "celsius",
            "clock_domain": "mcap_log_time",
            "applies_when": [{"topic": "/arm/mode", "signal": "state", "op": "eq", "value": "RUNNING"}],
            "check": {"kind": "range", "max": 70.0},
            "source": {"kind": "specification", "reference": "EXAMPLE-SPEC-4.2 (synthetic)", "description": "Thermal limit of the example arm."},
        },
        {
            "expectation_id": "GRIPPER-CLOSES-IN-TIME",
            "description": "Once the gripper starts CLOSING it must be CLOSED within 500 ms.",
            "topic": "/gripper/state", "signal": "value",
            "clock_domain": "mcap_log_time",
            "check": {"kind": "transition", "from_value": "CLOSING", "to_value": "CLOSED", "deadline_ns": 500 * MS},
            "source": {"kind": "operator_supplied", "reference": "EXAMPLE-TICKET-17 (synthetic)"},
        },
    ],
}


def write_example(out: Path) -> None:
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator import investigate, investigation_json, parse_contract, render_investigation

    out.mkdir(parents=True, exist_ok=True)
    rec = out / RECORDING
    rec.write_bytes(build_json_mcap(streams()))
    (out / "expectations.json").write_text(json.dumps(CONTRACT, indent=2) + "\n")
    result = investigate(inspect_evidence(rec), parse_contract(CONTRACT))
    (out / "expected_output.json").write_text(investigation_json(result))
    (out / "expected_report.txt").write_text(render_investigation(result))


if __name__ == "__main__":
    write_example(Path(sys.argv[1] if len(sys.argv) > 1 else "examples/investigator"))
