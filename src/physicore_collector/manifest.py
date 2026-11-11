"""Evidence manifest: observed evidence vs. explicit unknowns.

The manifest never says "no data was lost". `loss_detection_coverage` states what was NOT
analysed, and `known_gaps` always lists at least the limits of the evidence.
The manifest does not contain its own checksum (it cannot); it inventories every other
artifact in the package.
"""

from __future__ import annotations

from .capture_health import CLEAN_STATES
from .schemas import (
    ArtifactEntry, ArtifactReadability, CheckOutcome, KnownGap, LossDetectionCoverage, Manifest,
    SegmentState, TimeBase, TriggerComparisonResult,
)
from .session import SessionContext
from .versions import MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION_DAY2

LOSS_STATEMENT = (
    "No sequence-counter or DDS-level loss detection was performed. Where configured, cadence "
    "checks observe spacing between recorded log_time values only. The absence of detected gaps "
    "is NOT evidence that no data was lost."
)


def _cadence_checked_topics(ctx: SessionContext) -> list[str]:
    return sorted(
        c.check_id.split(":", 1)[1]
        for c in ctx.report.checks
        if c.check_id.startswith("cadence:") and c.outcome in (CheckOutcome.PASS, CheckOutcome.FAIL)
    )


def build_loss_coverage(ctx: SessionContext) -> LossDetectionCoverage:
    ruled = {t.name for t in ctx.profile.profile.topics if t.effective_max_gap_seconds is not None}
    all_topics = {t.name for t in ctx.profile.profile.topics} | ctx.scan.topic_names()
    return LossDetectionCoverage(
        cadence_checked_topics=_cadence_checked_topics(ctx),
        topics_without_cadence_rules=sorted(all_topics - ruled),
        statement=LOSS_STATEMENT,
    )


def build_known_gaps(ctx: SessionContext) -> list[KnownGap]:
    prof = ctx.profile.profile
    gaps = [
        KnownGap(
            code="no_sequence_or_dds_loss_analysis",
            description="Message sequence counters and DDS-level delivery were not analysed; losses between publisher and recorder cannot be detected from this artifact.",
        ),
        KnownGap(
            code="cadence_is_timing_evidence_only",
            description="Cadence findings describe spacing in recorded log_time. They are not proof of message loss, and passing cadence checks does not show that no messages were lost.",
        ),
        KnownGap(
            code="cadence_edges_not_evaluated",
            description="Silence before the first and after the last message of each topic is not evaluated.",
        ),
        KnownGap(
            code="summary_section_not_verified",
            description="; ".join(ctx.scan.inspection.not_verified),
        ),
    ]
    if prof.log_time_basis is TimeBase.UNKNOWN:
        gaps.append(KnownGap(
            code="log_time_basis_unknown",
            description="The profile does not declare what the recording's log_time represents, so it cannot be related to external clocks.",
        ))
    else:
        gaps.append(KnownGap(
            code="log_time_basis_declared_not_verified",
            description=f"The profile declares log_time_basis '{prof.log_time_basis.value}'; this tool cannot verify that declaration from the file.",
        ))

    ruled = {t.name for t in prof.topics if t.effective_max_gap_seconds is not None}
    all_topics = {t.name for t in prof.topics} | ctx.scan.topic_names()
    unruled = sorted(all_topics - ruled)
    if unruled:
        gaps.append(KnownGap(code="topics_without_cadence_rules", description=f"No cadence rule exists for: {unruled}."))
    unevaluated = sorted(
        c.check_id.split(":", 1)[1] for c in ctx.report.checks
        if c.check_id.startswith("cadence:") and c.outcome in (CheckOutcome.INCONCLUSIVE, CheckOutcome.NOT_APPLICABLE)
    )
    if unevaluated:
        gaps.append(KnownGap(code="cadence_check_not_completed", description=f"Cadence could not be evaluated for: {unevaluated}."))

    rd = ctx.report.artifact_readability
    if rd is not ArtifactReadability.READABLE:
        first = ctx.report.read_errors[0] if ctx.report.read_errors else "unknown error"
        gaps.append(KnownGap(
            code="recording_incomplete",
            description=f"The recording is {rd.value.replace('_', ' ')}; content after the first read error is unknown. First error: {first}",
        ))
    tc = ctx.report.trigger_comparison
    if tc is not None and tc.result in (TriggerComparisonResult.UNAVAILABLE, TriggerComparisonResult.INCONCLUSIVE):
        gaps.append(KnownGap(
            code="trigger_comparison_not_established",
            description=f"Trigger vs recording time comparison is {tc.result.value}: {tc.reason}",
        ))
    gaps.extend(_bag_and_capture_gaps(ctx))
    return gaps


def _bag_and_capture_gaps(ctx: SessionContext) -> list[KnownGap]:
    """Day 2 gaps. Only produced for rosbag2 directories / capture-health sidecars, so the gap
    list of a single-MCAP package is exactly what Day 1 produced."""
    gaps: list[KnownGap] = []
    bag = ctx.bag
    if bag is not None:
        info = bag.info
        gaps.append(KnownGap(
            code="bag_metadata_is_recorder_assertion",
            description="metadata.yaml is written by the recorder when it closes. Agreement with the files shows consistency, "
                        "not completeness, and nothing about messages that were never recorded.",
        ))
        gaps.append(KnownGap(
            code="bag_metadata_clock_not_compared",
            description="metadata.yaml starting_time/duration are on the recorder's clock and were not compared with MCAP log_time.",
        ))
        if not info.metadata_valid:
            gaps.append(KnownGap(
                code="bag_metadata_unusable",
                description=("metadata.yaml is missing" if not info.metadata_present else "metadata.yaml is unusable: " + "; ".join(info.metadata_errors))
                            + ". The segment list, segment order and expected message counts are unconfirmed; segments were "
                              "discovered from *.mcap files on disk and ordered by observed log_time.",
            ))
        bad = [f"{s.relative_path} ({s.state.value})" for s in info.segments if s.state is not SegmentState.READABLE]
        if bad:
            gaps.append(KnownGap(
                code="bag_segments_not_fully_inspected",
                description="Segments that are missing, unreadable, unsupported or only partly readable: " + ", ".join(bad)
                            + ". Their content is unknown and nothing was fabricated for them.",
            ))
        if info.conflicts:
            gaps.append(KnownGap(code="bag_metadata_conflicts", description="; ".join(info.conflicts)))
        if info.unaccounted:
            gaps.append(KnownGap(
                code="bag_unaccounted_entries",
                description="Entries in the bag directory that metadata.yaml does not account for were neither inspected nor exported: "
                            + ", ".join(u.name for u in info.unaccounted),
            ))
        if ctx.health is None:
            gaps.append(KnownGap(
                code="capture_health_unavailable",
                description="No capture-health record was supplied: how the recording was started and stopped, and whether it was "
                            "interrupted, is unknown.",
            ))
    h = ctx.health
    if h is not None:
        gaps.append(KnownGap(
            code="capture_clock_not_comparable",
            description="Capture start/stop times are the supervisor host's system clock; they are not established to be "
                        "comparable with MCAP log_time.",
        ))
        gaps.append(KnownGap(
            code="recorder_statistics_unavailable",
            description="rosbag2 exposes no machine-readable recorder statistics or sensor-health data and writes no drop counters to "
                        "metadata.yaml; it only prints a human-readable warning when its cache drops messages. If no such warning "
                        "was seen, that does not show that nothing was lost.",
        ))
        if h.health.recorder.reported_loss_lines:
            gaps.append(KnownGap(
                code="recorder_reported_message_loss",
                description="The recorder's own console output reported dropped or unflushed messages (verbatim lines are in the "
                            "capture-health record). The recording is known to be missing data; the extent is not established.",
            ))
        if h.health.state not in CLEAN_STATES:
            gaps.append(KnownGap(
                code="capture_not_cleanly_ended",
                description=f"Capture ended in state '{h.health.state.value}'. The recording may be incomplete or interrupted; "
                            "its true extent is unknown.",
            ))
    return gaps


def build_manifest(
    ctx: SessionContext, artifacts: list[ArtifactEntry], *, created_at_utc: str | None = None
) -> Manifest:
    insp = ctx.scan.inspection
    day2 = ctx.bag is not None or ctx.health is not None
    return Manifest(
        manifest_schema_version=MANIFEST_SCHEMA_VERSION_DAY2 if day2 else MANIFEST_SCHEMA_VERSION,
        recording_kind=ctx.recording_kind if ctx.bag is not None else None,
        bag=ctx.bag.info if ctx.bag is not None else None,
        capture_health=ctx.health.health.summary(ctx.health.sha256) if ctx.health is not None else None,
        created_at_utc=created_at_utc,
        session_status=ctx.report.status,
        profile=ctx.profile.profile,
        profile_sha256=ctx.profile.sha256,
        artifacts=artifacts,
        recording_window=insp.recording_window,
        topics=insp.topics,
        metadata={
            "supplied": ctx.metadata.data if ctx.metadata else None,
            "mcap_records": [r.model_dump(mode="json") for r in insp.metadata_records],
        },
        trigger=ctx.trigger.trigger if ctx.trigger else None,
        validation=ctx.report,
        known_gaps=build_known_gaps(ctx),
        loss_detection_coverage=build_loss_coverage(ctx),
    )
