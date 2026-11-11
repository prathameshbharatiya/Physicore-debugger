"""Validation engine: scan result + profile (+ metadata, trigger) -> ValidationReport.

Check catalogue (check_id; category; required?):

  readability                 READABILITY      required   pass iff the recording is fully readable
  topic_present:<topic>       REQUIRED_TOPIC   = profile `required` flag
                              pass: >=1 message; fail: absent or channel without messages.
                              In a truncated/malformed file an absent topic is INCONCLUSIVE
                              (it might sit in the unread part).
  cadence:<topic>             CADENCE          = profile `required` flag (only for topics with a
                              threshold). fail = observed log_time gap(s) over threshold, which
                              is TIMING EVIDENCE, NOT PROOF OF MESSAGE LOSS.
                              not_applicable: topic has no messages (see topic_present);
                              inconclusive: fewer than 2 messages.
  metadata:<key>              METADATA         required   for each profile `required_metadata_keys`;
                              looked up in MCAP Metadata records and in the supplied metadata
                              JSON. Missing/None/blank = missing. In a truncated file, missing is
                              INCONCLUSIVE.
  trigger:coverage            TRIGGER_TIMEBASE required   only when a trigger is supplied
                              (TRIGGER_CHECK_REQUIRED). pass: provably within the window;
                              fail: provably outside it; inconclusive: comparison unavailable or
                              inconclusive (see timebase.py).

If the recording is unreadable only the `readability` check is produced (nothing else can be
assessed). Status is then derived by status.derive_status.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .cadence import analyze_cadence
from .mcap_reader import ScanResult
from .schemas import (
    ArtifactReadability, CadenceFinding, CheckCategory, CheckOutcome, CheckResult,
    RobotProfile, Trigger, TriggerComparisonResult as TR, ValidationReport,
)
from .status import derive_status
from .timebase import compare_trigger
from .versions import VALIDATION_REPORT_SCHEMA_VERSION, VALIDATION_REPORT_SCHEMA_VERSION_DAY2

# A supplied trigger must be provably covered by the recording for the session to be complete.
TRIGGER_CHECK_REQUIRED = True

CADENCE_FAIL_NOTE = "This is timing evidence only, not proof of message loss."
CADENCE_PASS_NOTE = "This does not show that no messages were lost."


def _metadata_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _find_metadata_key(
    key: str, scan: ScanResult, supplied: Mapping[str, Any] | None
) -> tuple[bool, str | None]:
    """(found, where). Present = key exists with a non-None, non-blank value."""
    for rec in scan.inspection.metadata_records:
        if key in rec.values and not _metadata_missing(rec.values[key]):
            return True, f"MCAP metadata record '{rec.name}'"
    if supplied is not None and key in supplied and not _metadata_missing(supplied[key]):
        return True, "supplied metadata file"
    return False, None


def validate_recording(
    scan: ScanResult,
    profile: RobotProfile,
    *,
    supplied_metadata: Mapping[str, Any] | None = None,
    trigger: Trigger | None = None,
    extra_checks: Sequence[CheckResult] = (),
) -> ValidationReport:
    """`extra_checks` (Day 2: `bag:*` and `capture:*`) are inserted right after `readability`,
    also for an unreadable recording, so they can explain WHY it is unreadable. They take part in
    status derivation exactly like every other check."""
    readability = scan.readability
    checks: list[CheckResult] = []
    cadence_findings: list[CadenceFinding] = []
    trigger_comparison = None
    partial = readability is ArtifactReadability.PARTIALLY_READABLE

    if readability is ArtifactReadability.READABLE:
        checks.append(CheckResult(
            check_id="readability", category=CheckCategory.READABILITY, outcome=CheckOutcome.PASS,
            required=True, message="recording is fully readable (framing, CRCs and record parsing verified)",
            unknowns=["summary-section contents are not cross-validated"],
        ))
    else:
        checks.append(CheckResult(
            check_id="readability", category=CheckCategory.READABILITY, outcome=CheckOutcome.FAIL,
            required=True,
            message=f"recording is {readability.value.replace('_', ' ')}: " + "; ".join(scan.errors),
            observed={"errors": list(scan.errors)},
            unknowns=["content after the first read error cannot be assessed"] if partial else ["recording content"],
        ))

    checks.extend(extra_checks)

    if readability is not ArtifactReadability.UNREADABLE:
        partial_note = ["evaluated only over the recovered portion of a truncated/malformed recording"] if partial else []

        for t in profile.topics:
            n = scan.message_count_for_topic(t.name)
            declared = t.name in scan.topic_names()
            if n > 0:
                checks.append(CheckResult(
                    check_id=f"topic_present:{t.name}", category=CheckCategory.REQUIRED_TOPIC,
                    outcome=CheckOutcome.PASS, required=t.required,
                    message=f"topic {t.name} present with {n} message(s)",
                    observed={"message_count": n}, unknowns=list(partial_note),
                ))
            else:
                what = "channel declared but has no messages" if declared else "topic not found in recording"
                kind = "required" if t.required else "optional"
                if partial:
                    checks.append(CheckResult(
                        check_id=f"topic_present:{t.name}", category=CheckCategory.REQUIRED_TOPIC,
                        outcome=CheckOutcome.INCONCLUSIVE, required=t.required,
                        message=f"{kind} topic {t.name}: {what} in the recovered portion; it may exist in the unreadable part",
                        observed={"message_count": 0, "channel_declared": declared},
                        unknowns=list(partial_note),
                    ))
                else:
                    checks.append(CheckResult(
                        check_id=f"topic_present:{t.name}", category=CheckCategory.REQUIRED_TOPIC,
                        outcome=CheckOutcome.FAIL, required=t.required,
                        message=f"{kind} topic {t.name}: {what}",
                        observed={"message_count": 0, "channel_declared": declared},
                    ))

            threshold = t.effective_max_gap_seconds
            if threshold is None:
                continue
            cid = f"cadence:{t.name}"
            if n == 0:
                checks.append(CheckResult(
                    check_id=cid, category=CheckCategory.CADENCE, outcome=CheckOutcome.NOT_APPLICABLE,
                    required=t.required, message=f"{t.name}: no messages, cadence not evaluated (see topic_present:{t.name})",
                ))
                continue
            a = analyze_cadence(t.name, scan.log_times_for_topic(t.name), threshold)
            obs = {
                "threshold_seconds": threshold, "message_count": a.message_count,
                "gap_count": a.gap_count, "max_observed_gap_seconds": a.max_gap_seconds,
                "findings_recorded": len(a.findings),
            }
            if not a.evaluable:
                checks.append(CheckResult(
                    check_id=cid, category=CheckCategory.CADENCE, outcome=CheckOutcome.INCONCLUSIVE,
                    required=t.required, observed=obs,
                    message=f"{t.name}: only {a.message_count} message(s); cadence cannot be evaluated",
                    unknowns=["cadence needs at least 2 messages"] + partial_note,
                ))
            elif a.gap_count:
                cadence_findings.extend(a.findings)
                checks.append(CheckResult(
                    check_id=cid, category=CheckCategory.CADENCE, outcome=CheckOutcome.FAIL,
                    required=t.required, observed=obs,
                    message=(
                        f"{t.name}: {a.gap_count} observed log_time gap(s) exceeding {threshold:g} s "
                        f"(largest {a.max_gap_seconds:g} s). {CADENCE_FAIL_NOTE}"
                    ),
                    unknowns=["cause of the gap(s) is not determined"] + partial_note,
                ))
            else:
                checks.append(CheckResult(
                    check_id=cid, category=CheckCategory.CADENCE, outcome=CheckOutcome.PASS,
                    required=t.required, observed=obs,
                    message=(
                        f"{t.name}: no log_time gap exceeded {threshold:g} s over {a.message_count} message(s) "
                        f"(largest {a.max_gap_seconds:g} s). {CADENCE_PASS_NOTE}"
                    ),
                    unknowns=["silence before the first / after the last message is not evaluated"] + partial_note,
                ))

        for key in profile.required_metadata_keys:
            found, where = _find_metadata_key(key, scan, supplied_metadata)
            if found:
                checks.append(CheckResult(
                    check_id=f"metadata:{key}", category=CheckCategory.METADATA,
                    outcome=CheckOutcome.PASS, required=True,
                    message=f"required metadata key '{key}' found in {where}",
                    observed={"source": where},
                ))
            elif partial:
                checks.append(CheckResult(
                    check_id=f"metadata:{key}", category=CheckCategory.METADATA,
                    outcome=CheckOutcome.INCONCLUSIVE, required=True,
                    message=f"required metadata key '{key}' not found in the recovered portion; it may be in the unreadable part",
                    unknowns=list(partial_note),
                ))
            else:
                checks.append(CheckResult(
                    check_id=f"metadata:{key}", category=CheckCategory.METADATA,
                    outcome=CheckOutcome.FAIL, required=True,
                    message=f"required metadata key '{key}' is missing (not in MCAP metadata records or supplied metadata)",
                ))

        if trigger is not None:
            trigger_comparison = compare_trigger(
                trigger, profile.log_time_basis, scan.inspection.recording_window, readability
            )
            outcome = {
                TR.WITHIN_RECORDING_WINDOW: CheckOutcome.PASS,
                TR.BEFORE_RECORDING_WINDOW: CheckOutcome.FAIL,
                TR.AFTER_RECORDING_WINDOW: CheckOutcome.FAIL,
                TR.UNAVAILABLE: CheckOutcome.INCONCLUSIVE,
                TR.INCONCLUSIVE: CheckOutcome.INCONCLUSIVE,
            }[trigger_comparison.result]
            comparable = trigger_comparison.result not in (TR.UNAVAILABLE, TR.INCONCLUSIVE)
            checks.append(CheckResult(
                check_id="trigger:coverage", category=CheckCategory.TRIGGER_TIMEBASE,
                outcome=outcome, required=TRIGGER_CHECK_REQUIRED,
                message=f"trigger '{trigger.trigger_id}' comparison {trigger_comparison.result.value}: {trigger_comparison.reason}",
                observed={
                    "trigger_timestamp_basis": trigger.timestamp_basis.value,
                    "profile_log_time_basis": profile.log_time_basis.value,
                    "comparison": trigger_comparison.result.value,
                },
                unknowns=[] if comparable else ["whether the trigger time falls inside the recording is unknown"],
            ))

    status, reasons = derive_status(profile_valid=True, readability=readability, checks=checks)
    return ValidationReport(
        report_schema_version=(
            VALIDATION_REPORT_SCHEMA_VERSION_DAY2 if extra_checks else VALIDATION_REPORT_SCHEMA_VERSION
        ),
        status=status, status_reasons=reasons, artifact_readability=readability,
        checks=checks, cadence_findings=cadence_findings,
        trigger_comparison=trigger_comparison, read_errors=list(scan.errors),
    )
