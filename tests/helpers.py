"""Shared test data."""

VALID = {
    "profile_schema_version": "1.0",
    "profile_id": "test-arm",
    "robot_name": "Test Arm",
    "log_time_basis": "ros_time",
    "required_metadata_keys": ["operator"],
    "topics": [
        {"name": "/joint_states", "expected_hz": 50},
        {"name": "/camera", "max_gap_seconds": 0.5},
        {"name": "/diag", "required": False},
    ],
}
