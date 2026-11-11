from __future__ import annotations

from typing import Any

from physicore_collector.anomaly.clock import LOG_TIME_DOMAIN
from physicore_collector.anomaly.findings import (CoverageStatement, ClockedTime, Finding, ObservationRef, SourceRef, derive_finding_id,
                                                  hash_parameters)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def make_finding(**over: Any) -> Finding:
    params = {"example": 1}
    base: dict[str, Any] = dict(
        detector_id="tierA.ros_log", detector_version="1.0.0", severity="error", severity_basis="source_declared_level",
        anomaly_category="log_severity", topic="/rosout", message_type="rcl_interfaces/msg/Log", field_path="level",
        field_path_template="level", first_observed_at=ClockedTime(value_ns=10, clock_domain=LOG_TIME_DOMAIN),
        timestamp_basis="mcap_log_time", clock_domain=LOG_TIME_DOMAIN, observed_value=40, occurrence_count=1,
        observations=[ObservationRef(artifact_ref="seg0", sha256=SHA_A, topic="/rosout", message_index=0, order=0, log_time_ns=10,
                                     field_path="level", value=40)],
        source=[SourceRef(artifact_ref="seg0", sha256=SHA_A)],
        coverage=CoverageStatement(topic_coverage="complete", messages_examined=1), detector_parameters=params,
        explanation="Message 0 on /rosout reported level ERROR (40).")
    base.update(over)
    if "finding_id" not in base:
        base["finding_id"] = derive_finding_id(
            detector_id=base["detector_id"], detector_version=base["detector_version"], parameters_hash=hash_parameters(params),
            contributing_artifacts=[(s.artifact_ref, s.sha256) for s in base["source"]], topic=base["topic"],
            field_path_template=base["field_path_template"], group_key="k", first_artifact_ref="seg0", first_message_index=0)
    return Finding(**base)
