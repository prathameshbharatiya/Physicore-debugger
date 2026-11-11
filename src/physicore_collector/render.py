"""Human-readable text rendering. Pure functions: data in, string out. No I/O, no exit codes."""

from __future__ import annotations

from .export import ExportResult
from .manifest import LOSS_STATEMENT
from .mcap_reader import ScanResult
from .profile import LoadedProfile
from .schemas import ArtifactReadability, BagInfo, CheckOutcome, InspectionResult
from .session import SessionContext

_OUTCOME_LABEL = {
    CheckOutcome.PASS: "PASS",
    CheckOutcome.FAIL: "FAIL",
    CheckOutcome.INCONCLUSIVE: "INCONCLUSIVE",
    CheckOutcome.NOT_APPLICABLE: "N/A",
}


def render_profile(loaded: LoadedProfile) -> str:
    p = loaded.profile
    required = [t for t in p.topics if t.required]
    lines = [
        f"OK: profile '{p.profile_id}' (schema {p.profile_schema_version}) is valid",
        f"  file:            {loaded.path} ({loaded.source_format})",
        f"  sha256:          {loaded.sha256}",
        f"  topics:          {len(p.topics)} ({len(required)} required)",
        f"  log_time_basis:  {p.log_time_basis.value}",
    ]
    if p.required_metadata_keys:
        lines.append(f"  metadata keys:   {', '.join(p.required_metadata_keys)}")
    for t in p.topics:
        gap = t.effective_max_gap_seconds
        rule = f"max gap {gap:g} s" if gap is not None else "no cadence rule"
        lines.append(f"  - {t.name} [{'required' if t.required else 'optional'}] {rule}")
    if p.capture is not None:
        c = p.capture
        topics = ", ".join(c.topics) if c.topics else "(all profile topics)"
        lines.append(f"  capture:         storage {c.storage_id}, topics {topics}, stop timeout {c.stop_timeout_seconds:g} s"
                     + (f", split size {c.max_bag_size_bytes} B" if c.max_bag_size_bytes else "")
                     + (f", split duration {c.max_bag_duration_seconds} s" if c.max_bag_duration_seconds else ""))
    if p.log_time_basis.value == "unknown":
        lines.append(
            "  note: log_time_basis is 'unknown', so trigger timestamps cannot be "
            "compared with recording log_time."
        )
    return "\n".join(lines)


def render_bag(info: BagInfo) -> str:
    lines = [f"rosbag2 bag directory: {info.bag_directory_name}"]
    if info.metadata_valid and info.metadata is not None:
        m = info.metadata
        lines.append(
            f"  metadata.yaml: present, version {m.metadata_version}, storage '{m.storage_identifier}', "
            f"{m.message_count} message(s) claimed, ros_distro {m.ros_distro or 'unknown'}"
        )
    elif info.metadata_present:
        lines.append("  metadata.yaml: present but UNUSABLE: " + "; ".join(info.metadata_errors))
    else:
        lines.append("  metadata.yaml: MISSING (segment list, order and counts are unconfirmed)")
    basis = "from metadata.yaml" if info.segment_order_basis == "metadata" else "INFERRED from observed log_time (not confirmed)"
    lines.append(f"  segments ({len(info.segments)}), order {basis}:")
    for s in info.segments:
        n = "-" if s.observed_message_count is None else str(s.observed_message_count)
        lines.append(f"    - {s.relative_path}  [{s.state.value}]  messages read: {n}  sha256: {s.sha256 or '-'}")
        for e in s.errors if s.state.value != "readable" else []:
            lines.append(f"        {e}")
    for c in info.conflicts:
        lines.append(f"  conflict: {c}")
    for u in info.unaccounted:
        lines.append(f"  not accounted for by metadata.yaml: {u.name} ({u.kind})")
    return "\n".join(lines)


def render_inspection(insp: InspectionResult, bag: BagInfo | None = None) -> str:
    i = insp.integrity
    lines = []
    if bag is not None:
        lines += [render_bag(bag), ""]
    lines += [
        f"Recording: {insp.file_name} ({insp.size_bytes} bytes)",
        f"Readability: {insp.readability.value.upper().replace('_', ' ')}",
    ]
    if insp.mcap_library is not None:
        lines.append(f"MCAP writer: library='{insp.mcap_library}' profile='{insp.mcap_profile}'")
    lines.append(
        "Framing: "
        f"start magic {'ok' if i.start_magic_valid else 'INVALID'}, "
        f"header {'present' if i.header_present else 'MISSING'}, "
        f"data end {'present' if i.data_end_present else 'MISSING'}, "
        f"footer {'present' if i.footer_present else 'MISSING'}, "
        f"end magic {'ok' if i.end_magic_valid else 'NOT CONFIRMED'}"
    )
    if insp.errors:
        lines.append("Problems found:")
        lines += [f"  - {e}" for e in insp.errors]
    lines.append(f"Messages read: {insp.total_message_count}")
    w = insp.recording_window
    if w is not None:
        dur = (w.max_log_time_ns - w.min_log_time_ns) / 1e9
        lines.append(f"log_time min: {w.min_log_time_ns} ns")
        lines.append(f"log_time max: {w.max_log_time_ns} ns   (span {dur:.6f} s; clock basis not asserted by the file)")
    else:
        lines.append("log_time range: none (no messages read)")
    if insp.topics:
        lines.append("Topics / channels:")
        for t in insp.topics:
            lines.append(
                f"  [{t.channel_id}] {t.topic}  schema={t.schema_name or '-'}  "
                f"encoding={t.message_encoding or '-'}  messages={t.message_count}  "
                f"first={t.first_log_time_ns}  last={t.last_log_time_ns}"
            )
    for m in insp.metadata_records:
        lines.append(f"Metadata record '{m.name}': {m.values}")
    if insp.readability is not ArtifactReadability.READABLE:
        lines.append("Note: data after the first problem (if any) was not read and is unknown.")
    lines.append("Not verified: " + " ".join(insp.not_verified))
    return "\n".join(lines)


def render_validation(ctx: SessionContext) -> str:
    r = ctx.report
    lines = [
        f"Session validation: {ctx.recording_path.name}",
        f"  recording sha256: {ctx.recording_sha256}"
        + ("  (digest over the sorted per-file digests of the bag)" if ctx.bag is not None else ""),
        f"  profile:          {ctx.profile.profile.profile_id} (sha256 {ctx.profile.sha256})",
        f"  readability:      {r.artifact_readability.value}",
        f"STATUS: {r.status.value.upper()}",
    ]
    lines += [f"  reason: {x}" for x in r.status_reasons]
    if ctx.bag is not None:
        lines.append(render_bag(ctx.bag.info))
    if ctx.health is not None:
        h = ctx.health.health
        fin = {True: "yes", False: "NO", None: "unknown"}[h.bag_finalization.appears_finalized]
        lines.append(f"Capture: session {h.session_id}, state {h.state.value}, appears finalized: {fin} ({h.bag_finalization.basis})")
    lines.append("Checks:")
    for c in r.checks:
        lvl = "required" if c.required else "warning"
        lines.append(f"  [{_OUTCOME_LABEL[c.outcome]}] ({lvl}) {c.check_id}: {c.message}")
    if r.cadence_findings:
        lines.append("Cadence findings (observed log_time gaps; timing evidence only, NOT proof of message loss):")
        for f in r.cadence_findings:
            lines.append(
                f"  {f.topic}: {f.observed_gap_seconds:.6f} s between log_time "
                f"{f.gap_start_log_time_ns} and {f.gap_end_log_time_ns} (threshold {f.threshold_seconds:g} s)"
            )
    if r.trigger_comparison is not None:
        lines.append(f"Trigger comparison: {r.trigger_comparison.result.value} - {r.trigger_comparison.reason}")
    lines.append(f"Loss detection: NOT PERFORMED. {LOSS_STATEMENT}")
    return "\n".join(lines)


def render_export(result: ExportResult, ctx: SessionContext) -> str:
    m = result.manifest
    lines = [
        f"Package written: {result.package_dir}",
        f"STATUS: {m.session_status.value.upper()}",
    ]
    lines += [f"  reason: {x}" for x in m.validation.status_reasons]
    lines.append("Artifacts (sha256):")
    for a in m.artifacts:
        lines.append(f"  {a.relative_path}  {a.size_bytes} bytes  {a.sha256}")
    lines.append("  manifest.json  (inventory of the above; it does not contain its own checksum)")
    lines.append("Original recording copied byte-for-byte and verified by SHA-256.")
    lines.append(f"Loss detection: NOT PERFORMED. {LOSS_STATEMENT}")
    return "\n".join(lines)
