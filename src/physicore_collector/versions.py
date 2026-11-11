"""Single source of truth for tool and schema versions.

TOOL_VERSION must match `version` in pyproject.toml (enforced by a test).
Schema versions are bumped independently when the on-disk format changes.
"""

TOOL_NAME = "physicore-collector"
TOOL_VERSION = "0.1.0"

# Version of the manifest JSON format written by `export` for a single-MCAP recording
# (byte-for-byte the Day 1 shape, so Day 1 tooling can still read those packages).
MANIFEST_SCHEMA_VERSION = "0.1"
# Written only when a package carries Day 2 content (a rosbag2 bag directory and/or a
# capture-health sidecar): adds the optional `recording_kind`, `bag` and `capture_health`
# sections and the `bag_metadata` / `capture_health` artifact roles.
MANIFEST_SCHEMA_VERSION_DAY2 = "0.2"
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = ("0.1", "0.2")

# Version of the validation report JSON format. Same rule: "0.2" only when the report contains
# the Day 2 `bag_structure` / `capture` check categories.
VALIDATION_REPORT_SCHEMA_VERSION = "0.1"
VALIDATION_REPORT_SCHEMA_VERSION_DAY2 = "0.2"
SUPPORTED_REPORT_SCHEMA_VERSIONS = ("0.1", "0.2")

# Version of the capture-health sidecar written by `capture run` / `capture start`.
# 1.1 (Day 3) adds process identity, heartbeat/lifecycle timing, disk status, stop reason,
# reconciliation, and the `starting` / `interrupted` / `stopped_disk_critical` states.
# 1.0 sidecars (Day 2) still load; a 1.1 sidecar is not readable by Day 2 tooling.
CAPTURE_HEALTH_SCHEMA_VERSION = "1.1"
SUPPORTED_CAPTURE_HEALTH_SCHEMA_VERSIONS = ("1.0", "1.1")

# Robot profile schema versions this tool can load. Anything else is rejected
# with a clear error rather than being guessed at.
# "1.1" (Day 2) adds the optional `capture` section; "1.0" profiles load unchanged.
# "1.2" (Day 3) adds capture.heartbeat_seconds / disk_warn_free_bytes / disk_critical_free_bytes.
SUPPORTED_PROFILE_SCHEMA_VERSIONS = ("1.0", "1.1", "1.2")
PROFILE_VERSION_WITH_CAPTURE = "1.1"
PROFILE_VERSION_WITH_MONITORING = "1.2"

# Version of `incident.json` (the incident-preparation result written by `incident prepare` and by
# automatic preparation after a capture). It is a NEW file next to the unchanged export package
# (`package/`, manifest 0.1/0.2), so no existing format changed. Bump on any incompatible change.
INCIDENT_SCHEMA_VERSION = "1.0"
SUPPORTED_INCIDENT_SCHEMA_VERSIONS = ("1.0",)
# Version of `incident_attempt.json`, the per-session record of the last preparation attempt; it doubles as the
# pending-preparation queue entry. 1.1 (Day 5) adds the `deferred` state and scheduling fields; 1.0 files stay readable.
ATTEMPT_SCHEMA_VERSION = "1.1"
SUPPORTED_ATTEMPT_SCHEMA_VERSIONS = ("1.0", "1.1")
