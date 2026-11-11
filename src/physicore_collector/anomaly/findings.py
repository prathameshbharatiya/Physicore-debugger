"""Output models of M3c (DESIGN 4): findings, observation references, coverage, uncertainty, detector results, the report.
Strict (extra fields rejected). Optional-absent fields are omitted from the JSON, so the output carries only what is known.
There is intentionally no confidence/score field anywhere: no calibrated methodology exists (DESIGN 4.3)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .clock import LOG_TIME_DOMAIN
from .severity import SEVERITIES, SEVERITY_BASES

CATEGORIES = ("log_severity", "diagnostic_status", "goal_status", "power_status", "publishing_gap", "rate_change",
              "timestamp_regression", "timestamp_duplicate", "array_length_change", "non_finite_value")
# DESIGN 4.6 (closed vocabulary)
UNCERTAINTY_CODES = ("constants_from_builtin_table", "structure_only_stamp_type", "baseline_from_recording",
                     "baseline_contaminated_possible", "capture_loss_indistinguishable_from_silence", "first_seen_may_precede_recording",
                     "identity_unknown", "battery_identity_fields_empty", "text_truncated", "zero_stamp_valid_not_assumed_unset",
                     "invalid_stamps_excluded", "unmeasured_vs_nonfinite_indistinguishable", "nan_payload_bits_not_guaranteed",
                     "retained_observations_truncated")
RESULT_STATUSES = ("findings_reported", "no_anomaly_observed", "insufficient_evidence", "blind_spot", "not_configured", "not_applicable")
DETECTOR_IDS = ("tierA.ros_log", "tierA.diagnostics", "tierA.goal_status", "tierA.battery", "tierB.publishing_gap",
                "tierB.rate_change", "tierB.stamp_order", "tierB.array_length", "tierB.non_finite")
IDENTITY_STATES = ("known", "empty", "absent", "malformed")
EXIT_REASONS = ("invalid_config", "evidence_not_inspectable", "evidence_integrity_or_completeness", "findings_with_fail_on_findings")

# Words that would turn a descriptive explanation into a causal claim (DESIGN 0, TEST_PLAN 12). Applies to text this tool generates,
# never to retained data strings (e.g. a ROS log message that happens to contain such words).
FORBIDDEN_TERMS = ("caused", "cause", "because", "due to", "led to", "leading to", "root cause", "resulted in", "failed because",
                   "triggered", "culprit", "faulty", "crashed", "died", "stopped publishing", "dropped messages")
_FORBIDDEN_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(t) for t in FORBIDDEN_TERMS) + r")(?![A-Za-z])", re.IGNORECASE)


def find_forbidden(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _FORBIDDEN_RE.finditer(text)})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=False)


Scalar = Union[StrictBool, StrictInt, StrictStr, None]


class ClockedTime(_Strict):
    value_ns: StrictInt
    clock_domain: StrictStr


class PayloadStampRef(_Strict):
    value_ns: StrictInt
    clock_domain: StrictStr
    stamp_type_check: StrictStr


class BitInfo(_Strict):
    """Only what DESIGN 6.6.1 guarantees: class/sign always; bits for float64 and for +-Inf; never float32 NaN payload bits."""
    width: Literal[32, 64]
    sign: Literal[0, 1]
    bits_hex: Optional[StrictStr] = None
    bits_basis: StrictStr


class SourceRef(_Strict):
    artifact_ref: StrictStr
    sha256: StrictStr

    @field_validator("sha256")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v


class ObservationRef(_Strict):
    artifact_ref: StrictStr
    sha256: StrictStr
    topic: StrictStr
    message_index: StrictInt          # Msg.index: meaningful only together with artifact_ref
    order: StrictInt                  # Msg.order (segment order)
    log_time_ns: StrictInt            # always present, domain mcap_log_time
    field_path: Optional[StrictStr] = None
    value: Scalar = None
    value_repr: Optional[StrictStr] = None
    bit_info: Optional[BitInfo] = None
    payload_stamp: Optional[PayloadStampRef] = None
    identity_state: Optional[Literal["known", "empty", "absent", "malformed"]] = None
    identity_raw: Optional[StrictStr] = None


class CoverageStatement(_Strict):
    topic_coverage: Literal["complete", "incomplete"]
    incomplete_reasons: list[StrictStr] = Field(default_factory=list)
    messages_examined: StrictInt = 0
    messages_undecodable: StrictInt = 0
    recording_span_ns: Optional[tuple[StrictInt, StrictInt]] = None   # mcap_log_time
    boundary_note: Optional[StrictStr] = None


class UncertaintyNote(_Strict):
    code: StrictStr
    params: Optional[dict[StrictStr, Union[StrictInt, StrictStr]]] = None

    @field_validator("code")
    @classmethod
    def _closed(cls, v: str) -> str:
        if v not in UNCERTAINTY_CODES:
            raise ValueError(f"'{v}' is not in the closed uncertainty vocabulary")
        return v


class Threshold(_Strict):
    name: StrictStr
    value: StrictStr
    unit: StrictStr
    source: Literal["config"]


class Finding(_Strict):
    finding_id: StrictStr
    detector_id: StrictStr
    detector_version: StrictStr
    severity: StrictStr
    severity_basis: StrictStr
    anomaly_category: StrictStr
    topic: StrictStr
    message_type: StrictStr
    field_path: Optional[StrictStr] = None
    field_path_template: Optional[StrictStr] = None
    first_observed_at: ClockedTime
    last_observed_at: Optional[ClockedTime] = None
    timestamp_basis: Literal["mcap_log_time", "payload_stamp"]
    clock_domain: StrictStr
    observed_value: Union[StrictBool, StrictInt, StrictStr, dict[str, Any], list[Any], None] = None
    expected_value: Union[StrictBool, StrictInt, StrictStr, None] = None
    expected_source: Optional[Literal["config", "contract", "recording_baseline"]] = None
    threshold: Optional[Threshold] = None
    occurrence_count: StrictInt
    observations: list[ObservationRef]
    observations_truncated: StrictBool = False
    source: list[SourceRef]
    coverage: CoverageStatement
    uncertainty: list[UncertaintyNote] = Field(default_factory=list)
    detector_parameters: dict[str, Any]
    explanation: StrictStr

    @field_validator("finding_id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.fullmatch(r"f-[0-9a-f]{16}", v):
            raise ValueError("finding_id must look like f-<16 hex>")
        return v

    @field_validator("detector_id")
    @classmethod
    def _det(cls, v: str) -> str:
        if v not in DETECTOR_IDS:
            raise ValueError(f"unknown detector_id '{v}'")
        return v

    @field_validator("detector_version")
    @classmethod
    def _ver(cls, v: str) -> str:
        if not re.fullmatch(r"\d+\.\d+\.\d+", v):
            raise ValueError("detector_version must be MAJOR.MINOR.PATCH")
        return v

    @field_validator("severity")
    @classmethod
    def _sev(cls, v: str) -> str:
        if v not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        return v

    @field_validator("severity_basis")
    @classmethod
    def _basis(cls, v: str) -> str:
        if v not in SEVERITY_BASES:
            raise ValueError(f"severity_basis must be one of {SEVERITY_BASES}")
        return v

    @field_validator("anomaly_category")
    @classmethod
    def _cat(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"anomaly_category must be one of {CATEGORIES}")
        return v

    @field_validator("occurrence_count")
    @classmethod
    def _count(cls, v: int) -> int:
        if v < 1:
            raise ValueError("occurrence_count is at least 1")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "Finding":
        if not self.source:
            raise ValueError("a finding must name at least one contributing artifact")
        keys = [(s.artifact_ref, s.sha256) for s in self.source]
        if keys != sorted(set(keys)):
            raise ValueError("source must list ALL contributing artifacts, sorted by (artifact_ref, sha256), without duplicates")
        if self.first_observed_at.clock_domain != self.clock_domain or (self.last_observed_at is not None and self.last_observed_at.clock_domain != self.clock_domain):
            raise ValueError("first/last observed time must be in the finding's single clock domain")
        if self.timestamp_basis == "mcap_log_time" and self.clock_domain != LOG_TIME_DOMAIN:
            raise ValueError("timestamp_basis mcap_log_time requires clock_domain mcap_log_time")
        if self.timestamp_basis == "payload_stamp" and not self.clock_domain.startswith("payload_stamp:"):
            raise ValueError("timestamp_basis payload_stamp requires a payload_stamp:<topic>:<field> clock domain")
        bad = find_forbidden(self.explanation)
        if bad:
            raise ValueError(f"explanation uses causal wording {bad}; findings describe observations only")
        return self


class Reason(_Strict):
    code: StrictStr
    detail: Optional[StrictStr] = None
    count: Optional[StrictInt] = None


class FindingsTruncated(_Strict):
    detector: StrictStr
    suppressed_exact_count: StrictInt


class DetectorResult(_Strict):
    detector_id: StrictStr
    detector_version: StrictStr
    status: StrictStr
    topics: list[StrictStr] = Field(default_factory=list)
    messages_examined: StrictInt = 0
    messages_undecodable: StrictInt = 0
    findings_count: StrictInt = 0
    reasons: list[Reason] = Field(default_factory=list)
    unrecognised_values: Optional[dict[StrictStr, StrictInt]] = None
    payload_malformed_field: StrictInt = 0
    identity_field_malformed: Optional[dict[StrictStr, StrictInt]] = None
    findings_truncated: Optional[FindingsTruncated] = None
    parameters_hash: Optional[StrictStr] = None

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in RESULT_STATUSES:
            raise ValueError(f"status must be one of {RESULT_STATUSES}")
        return v

    @field_validator("detector_id")
    @classmethod
    def _det(cls, v: str) -> str:
        if v not in DETECTOR_IDS:
            raise ValueError(f"unknown detector_id '{v}'")
        return v


class AnomalyReport(_Strict):
    anomaly_schema_version: StrictStr
    tool_version: StrictStr
    config_hash: Optional[StrictStr] = None
    evidence: list[SourceRef] = Field(default_factory=list)
    clock_domains: list[StrictStr] = Field(default_factory=list)
    ordering_key: StrictStr
    severity_disclaimer: StrictStr
    limitations: list[StrictStr]
    exit_code: StrictInt
    exit_reasons: list[StrictStr] = Field(default_factory=list)
    results: list[DetectorResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    config_problems: Optional[list[dict[StrictStr, StrictStr]]] = None

    @field_validator("exit_reasons")
    @classmethod
    def _reasons(cls, v: list[str]) -> list[str]:
        for r in v:
            if r not in EXIT_REASONS:
                raise ValueError(f"unknown exit reason '{r}'")
        return v

    @model_validator(mode="after")
    def _unique_ids(self) -> "AnomalyReport":
        ids = [f.finding_id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("two distinct findings produced the same finding_id; refusing to emit an ambiguous report")
        return self


# ---------------------------------------------------------------- canonical JSON and identity

def _reject_floats(obj: Any, path: str = "$") -> None:
    if isinstance(obj, float):
        raise TypeError(f"floats are not allowed in canonical identity data (at {path})")
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_floats(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _reject_floats(v, f"{path}[{i}]")


def canonical_json(obj: Any) -> str:
    """Compact, key-sorted, UTF-8-safe JSON used for hashing. Integers/strings only (no floats)."""
    _reject_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_parameters(params: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()


def derive_finding_id(*, detector_id: str, detector_version: str, parameters_hash: str,
                      contributing_artifacts: Iterable[tuple[str, str]], topic: str, field_path_template: Optional[str],
                      group_key: Any, first_artifact_ref: str, first_message_index: int) -> str:
    """DESIGN 4.2. The identity covers ALL contributing artifacts (ref + sha256, sorted, unique), never just the first one."""
    arts = sorted(set((str(r), str(s)) for r, s in contributing_artifacts))
    if not arts:
        raise ValueError("a finding identity needs at least one contributing artifact")
    ident = {
        "detector_id": detector_id, "detector_version": detector_version, "detector_parameters_hash": parameters_hash,
        "contributing_artifacts": [{"artifact_ref": r, "sha256": s} for r, s in arts],
        "topic": topic, "field_path_template": field_path_template, "group_key": group_key,
        "first_observation": {"artifact_ref": first_artifact_ref, "message_index": first_message_index},
    }
    return "f-" + hashlib.sha256(canonical_json(ident).encode("utf-8")).hexdigest()[:16]


def report_json(report: AnomalyReport) -> str:
    """Deterministic JSON of the report: sorted keys, 2-space indent, absent optionals omitted, trailing newline."""
    return json.dumps(report.model_dump(mode="json", exclude_none=True), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
