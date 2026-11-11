"""Report assembly and rendering (skeleton for step 1: constants, ordering, canonical JSON, readable text).
All text generated here is descriptive; it is checked against the forbidden causal wording (findings.find_forbidden)."""
from __future__ import annotations

from typing import Iterable

from . import ANOMALY_SCHEMA_VERSION
from .clock import LOG_TIME_DOMAIN
from .findings import AnomalyReport, DetectorResult, Finding, SourceRef, find_forbidden, report_json

SEVERITY_DISCLAIMER = ("Severity is an ordered label of the indicator class. It is not a calibrated failure probability "
                       "and not a statement of physical impact.")
ORDERING_KEY = "first observation log_time_ns, detector_id, topic, field_path, artifact_ref, message_index, finding_id"

# Always present in every report (DESIGN 6.2, 12.4).
LIMITATIONS = (
    "late-message detection is not performed (deferred)",
    "findings describe observations in the recording; they are not diagnoses",
    "a gap or rate change between captured messages cannot be told apart from capture loss or from true silence",
    "synthetic-data validation does not establish compatibility with real robot recordings; real ROS recording validation is outstanding",
    "decoder licensing and redistribution review (mcap-ros2-support) is outstanding",
    "ROS documentation verification is outstanding where not independently confirmed; constants and field types were checked against the rosbags typestore",
    "exact float32 NaN payload bits are not recoverable from the decoder; only class and sign are reported for float32 NaN",
)


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    def key(f: Finding):
        o = f.observations[0] if f.observations else None
        return (o.log_time_ns if o else f.first_observed_at.value_ns, f.detector_id, f.topic, f.field_path or "",
                o.artifact_ref if o else "", o.message_index if o else -1, f.finding_id)
    return sorted(findings, key=key)


def new_report(*, tool_version: str, exit_code: int, exit_reasons: list[str], config_hash: str | None = None,
               evidence: list[SourceRef] | None = None, results: list[DetectorResult] | None = None,
               findings: list[Finding] | None = None, config_problems: list[dict[str, str]] | None = None) -> AnomalyReport:
    ev = sorted(evidence or [], key=lambda s: (s.artifact_ref, s.sha256))
    return AnomalyReport(
        anomaly_schema_version=ANOMALY_SCHEMA_VERSION, tool_version=tool_version, config_hash=config_hash, evidence=ev,
        clock_domains=[LOG_TIME_DOMAIN], ordering_key=ORDERING_KEY, severity_disclaimer=SEVERITY_DISCLAIMER,
        limitations=list(LIMITATIONS), exit_code=exit_code, exit_reasons=exit_reasons,
        results=sorted(results or [], key=lambda r: r.detector_id), findings=sort_findings(findings or []),
        config_problems=config_problems)


def generated_strings(report: AnomalyReport) -> list[str]:
    """Every string this tool generated (not retained data): used by the wording tests."""
    out = [report.severity_disclaimer, *report.limitations]
    for r in report.results:
        out += [x for rs in r.reasons for x in ([rs.code] + ([rs.detail] if rs.detail else []))]
    for f in report.findings:
        out.append(f.explanation)
    return out


def check_wording(report: AnomalyReport) -> list[str]:
    bad: list[str] = []
    for s in generated_strings(report):
        bad += find_forbidden(s)
    return sorted(set(bad))


def render_json(report: AnomalyReport) -> str:
    return report_json(report)


def render_text(report: AnomalyReport) -> str:
    lines = [f"Anomaly detection report (schema {report.anomaly_schema_version})", f"exit code: {report.exit_code}"
             + (f" ({', '.join(report.exit_reasons)})" if report.exit_reasons else ""), report.severity_disclaimer, ""]
    if report.config_problems:
        lines.append("Invalid anomaly configuration:")
        lines += [f"  {p['path']}: {p['message']}" for p in report.config_problems]
        lines.append("")
    for r in report.results:
        lines.append(f"{r.detector_id} v{r.detector_version}: {r.status}" + (f" ({r.findings_count} findings)" if r.findings_count else ""))
        for rs in r.reasons:
            lines.append(f"    {rs.code}" + (f": {rs.detail}" if rs.detail else ""))
    if report.findings:
        lines.append("")
        lines.append("Findings:")
        for f in report.findings:
            lines.append(f"  {f.finding_id} [{f.severity}] {f.detector_id} {f.topic}: {f.explanation}")
    lines += ["", "Limitations:"] + [f"  - {s}" for s in report.limitations]
    return "\n".join(lines) + "\n"
