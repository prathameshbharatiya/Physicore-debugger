"""Incident preparation (library level): discovery, ordering, the four separate statements, safety,
retry/idempotency, failure handling, memory, offline. Synthetic bags only; no ROS 2 (see
DAY4_IMPLEMENTATION_REPORT.md for what that does and does not show)."""

import errno
import hashlib
import json
import os
import shutil
import socket
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from mcap.writer import CompressionType, Writer

from physicore_collector import export as export_mod
from physicore_collector import incident as inc
from physicore_collector.capture_health import CaptureHealth, load_health
from physicore_collector.errors import (
    IncidentConflictError, IncidentError, IncidentRefusedError, InputError, InsufficientSpaceError, NoRecordingError,
)
from physicore_collector.export import export_package, verify_package
from physicore_collector.profile import load_profile
from physicore_collector.schemas import SessionStatus
from physicore_collector.session import analyze_session
from physicore_collector.sessionlock import FileLock
from physicore_collector.supervisor import CaptureSupervisor, write_health_atomic
from tests import make_fixtures as mf
from tests.bagtools import three_way_split, write_bag
from tests.recorder_doubles import FakeAdapter, FakeClock
from tests.test_supervisor import PROFILE_11

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def snap(root: Path) -> dict:
    """Name -> (size, mtime_ns, sha256) of every file under root: proves a source is untouched."""
    root = Path(root)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root) if root.is_dir() else p.name): (p.stat().st_size, p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest()) for p in files}


def prep(source, profile, out, **kw):
    kw.setdefault("now", NOW)
    return inc.prepare_incident(source, profile, out, **kw)


def make_session(tmp_path, behaviour="clean", sid="s1"):
    prof = tmp_path / "p.yaml"
    prof.write_text(PROFILE_11)
    out = tmp_path / "caps"
    out.mkdir(exist_ok=True)
    clock = FakeClock()
    a = FakeAdapter(behaviour)
    a.clock = clock
    res = CaptureSupervisor(a, clock=clock, poll_interval=0.1).run(load_profile(prof), out, session_id=sid, duration_seconds=1)
    return res.session_dir


@pytest.fixture
def bag(tmp_path):
    return write_bag(tmp_path / "src" / "bag1")


# ------------------------------------------------------------------ discovery & shape


def test_standalone_mcap_prepares_a_verified_self_contained_incident(fx, synth_profile, tmp_path):
    src = fx / "valid.mcap"
    before = snap(src)
    r = prep(src, synth_profile, tmp_path / "inc")
    d = r.incident_dir
    assert r.state == "prepared" and sorted(os.listdir(d)) == ["INCIDENT_SUMMARY.md", "incident.json", "package"]
    assert verify_package(d / "package") == [] and inc.verify_incident(d) == []
    rec = r.record
    assert rec.outcome == "success" and rec.session_status is SessionStatus.COMPLETE
    assert rec.preparation.package_mode == "self_contained_copy" and rec.source.recording_kind == "mcap_file"
    assert rec.segment_order_basis == "single_file" and [s.relative_path for s in rec.segments] == ["valid.mcap"]
    assert (d / "package" / "recording" / "valid.mcap").read_bytes() == src.read_bytes()  # a real copy
    assert snap(src) == before  # source untouched
    assert not [p for p in tmp_path.iterdir() if "staging" in p.name]


def test_single_mcap_package_is_exactly_what_export_produces(fx, synth_profile, tmp_path):
    """Reuse, not reimplementation: the package manifest equals a direct export byte for byte (schema 0.1)."""
    r = prep(fx / "valid.mcap", synth_profile, tmp_path / "inc")
    export_package(analyze_session(fx / "valid.mcap", synth_profile), tmp_path / "direct", now=NOW)
    assert (r.incident_dir / "package" / "manifest.json").read_bytes() == (tmp_path / "direct" / "manifest.json").read_bytes()
    assert r.record.package.manifest_schema_version == "0.1"


def test_bag_directory_incident_has_metadata_segments_and_manifest_0_2(bag, synth_profile, tmp_path):
    r = prep(bag, synth_profile, tmp_path / "inc")
    pkg = r.incident_dir / "package" / "recording" / "bag1"
    assert (pkg / "metadata.yaml").is_file() and (pkg / "bag1_0.mcap").is_file()
    assert r.record.package.manifest_schema_version == "0.2" and r.record.source.recording_kind == "rosbag2_directory"
    assert r.record.segment_order_basis == "metadata"


def test_multi_segment_bag_keeps_deterministic_order_and_is_reproducible(synth_profile, tmp_path):
    b = write_bag(tmp_path / "s" / "bagm", three_way_split())
    r1 = prep(b, synth_profile, tmp_path / "i1")
    r2 = prep(b, synth_profile, tmp_path / "i2")
    names = [s.relative_path for s in r1.record.segments]
    assert names == ["bagm_0.mcap", "bagm_1.mcap", "bagm_2.mcap"] and [s.order for s in r1.record.segments] == [0, 1, 2]
    assert r1.record.segments == r2.record.segments and r1.record.source_fingerprint == r2.record.source_fingerprint
    assert r1.record.session_status is SessionStatus.COMPLETE


def test_segment_order_follows_metadata_not_file_names(synth_profile, tmp_path):
    def reverse(md):
        md["relative_file_paths"].reverse()
        md["files"].reverse()
    b = write_bag(tmp_path / "s" / "bagr", three_way_split(), metadata_edit=reverse)
    r = prep(b, synth_profile, tmp_path / "i")
    assert [s.relative_path for s in r.record.segments] == ["bagr_2.mcap", "bagr_1.mcap", "bagr_0.mcap"]
    assert r.record.segment_order_basis == "metadata"


def test_without_metadata_order_is_inferred_from_log_time_and_labelled_so(synth_profile, tmp_path):
    b = write_bag(tmp_path / "s" / "nometa", three_way_split(), with_metadata=False)
    r = prep(b, synth_profile, tmp_path / "i")
    assert r.record.segment_order_basis == "inferred_from_observed_log_time"
    assert [s.relative_path for s in r.record.segments] == ["nometa_0.mcap", "nometa_1.mcap", "nometa_2.mcap"]
    assert r.record.outcome == "degraded" and r.record.session_status is SessionStatus.PARTIAL
    assert "bag_metadata_unusable" in {g.code for g in r.record.known_gaps}


def test_pointing_at_one_listed_segment_prepares_the_whole_bag(synth_profile, tmp_path):
    b = write_bag(tmp_path / "s" / "bagp", three_way_split())
    r = prep(b / "bagp_1.mcap", synth_profile, tmp_path / "i")
    assert r.record.source.discovery == "segment_file_promoted_to_bag" and len(r.record.segments) == 3
    assert any("whole bag directory" in n for n in r.record.source.notes)


def test_unrelated_neighbours_of_a_lone_file_are_named_not_silently_included(fx, synth_profile, tmp_path):
    d = tmp_path / "loose"
    d.mkdir()
    shutil.copy(fx / "valid.mcap", d / "a.mcap")
    shutil.copy(fx / "valid.mcap", d / "b.mcap")
    r = prep(d / "a.mcap", synth_profile, tmp_path / "i")
    assert len(r.record.segments) == 1 and any("NOT included" in n and "b.mcap" in n for n in r.record.source.notes)


def test_session_directory_uses_its_own_profile_and_sidecar_and_records_an_attempt(tmp_path):
    sess = make_session(tmp_path)
    before = snap(sess)
    r = inc.prepare_incident(sess, None, None, now=NOW)
    assert r.incident_dir == sess / "incident" and r.record.source.discovery == "capture_session_directory"
    assert r.record.capture_health.state == "finalized" and r.record.artifact_integrity.capture_end_digests == "match"
    assert (r.incident_dir / "package" / "capture" / "capture_health.json").is_file()
    after = snap(sess)
    for k, v in before.items():  # every pre-existing file (bag, sidecar, profile) is byte- and mtime-identical
        assert after[k] == v
    att = json.loads((sess / "incident_attempt.json").read_text())
    assert att["state"] == "prepared"


def test_bag_directory_of_a_session_finds_sidecar_and_profile_and_defaults_to_the_session(tmp_path):
    sess = make_session(tmp_path)
    r = inc.prepare_incident(sess / "bag", None, None, now=NOW)
    assert r.incident_dir == sess / "incident" and r.record.source.discovery == "bag_directory_of_capture_session"
    assert r.record.capture_health.state == "finalized"
    r2 = prep(sess / "bag", None, tmp_path / "elsewhere")  # same evidence reached another way: same fingerprint
    assert r2.record.source_fingerprint == r.record.source_fingerprint


def test_out_is_required_unless_a_capture_session_is_known(bag, synth_profile):
    with pytest.raises(InputError, match="--out is required"):
        inc.prepare_incident(bag, synth_profile, None)


def test_profile_is_required_when_none_can_be_found(bag, tmp_path):
    with pytest.raises(InputError, match="No profile"):
        prep(bag, None, tmp_path / "i")


# ------------------------------------------------------------------ the four statements stay separate


def test_valid_checksums_do_not_make_a_degraded_recording_healthy(fx, synth_profile, tmp_path):
    r = prep(fx / "cadence_gap.mcap", synth_profile, tmp_path / "i")
    rec = r.record
    assert rec.artifact_integrity.state == "verified"  # bytes are intact ...
    assert rec.outcome == "degraded" and rec.evidence_coverage.observed_cadence_gap_count > 0  # ... but gaps were observed
    assert rec.evidence_coverage.state == "expected_signals_observed"
    assert any("no message was lost" in x for x in rec.artifact_integrity.not_established)


def test_missing_capture_health_is_unknown_never_healthy(fx, synth_profile, tmp_path):
    rec = prep(fx / "valid.mcap", synth_profile, tmp_path / "i").record
    ch = rec.capture_health
    assert ch.state == "unknown" and ch.basis == "not_supplied" and ch.recorder_reported_loss is None
    assert rec.artifact_integrity.capture_end_digests == "not_available"
    assert rec.outcome == "success"  # nothing adverse is KNOWN; the unknown stays visible
    text = (tmp_path / "i" / "INCIDENT_SUMMARY.md").read_text()
    assert "Capture health: UNKNOWN" in text and "no message was lost" in text


def test_missing_required_topic_is_reported_as_missing_coverage(fx, synth_profile, tmp_path):
    rec = prep(fx / "missing_topic.mcap", synth_profile, tmp_path / "i").record
    assert rec.evidence_coverage.state == "expected_signals_missing" and rec.outcome == "degraded"
    assert any(t.observed == "absent" and t.required for t in rec.evidence_coverage.expected_topics)


def test_truncated_recording_is_degraded_and_coverage_undetermined(fx, synth_profile, tmp_path):
    rec = prep(fx / "truncated.mcap", synth_profile, tmp_path / "i").record
    assert rec.outcome == "degraded" and rec.session_status is SessionStatus.PARTIAL
    assert rec.evidence_coverage.state in ("undetermined", "expected_signals_missing")
    assert rec.artifact_integrity.state == "verified"


@pytest.mark.parametrize("name", ["corrupted.mcap", "not_mcap.mcap"])
def test_unreadable_input_is_preserved_and_labelled_not_hidden(fx, synth_profile, tmp_path, name):
    r = prep(fx / name, synth_profile, tmp_path / "i")
    assert r.record.outcome == "degraded" and r.record.session_status in (SessionStatus.PARTIAL, SessionStatus.FAILED)
    assert (r.incident_dir / "package" / "recording" / name).read_bytes() == (fx / name).read_bytes()
    assert inc.verify_incident(r.incident_dir) == []


def test_interrupted_capture_is_degraded_with_interrupted_health(tmp_path):
    sess = make_session(tmp_path, "exit_early_with_output")
    rec = inc.prepare_incident(sess, None, None, now=NOW).record
    assert rec.capture_health.state == "interrupted" and rec.outcome == "degraded"
    assert any("capture health is INTERRUPTED" in x for x in rec.outcome_reasons)


def test_sidecar_saying_running_without_a_supervisor_is_interrupted_not_running(tmp_path):
    sess = make_session(tmp_path)
    h = load_health(sess / "capture_health.json").health
    data = h.model_dump(mode="json")
    data["state"] = "running"
    data["bag_finalization"] = {"appears_finalized": None, "basis": "test"}
    write_health_atomic(sess / "capture_health.json", CaptureHealth.model_validate(data))
    rec = inc.prepare_incident(sess, None, None, now=NOW).record
    assert rec.capture_health.state == "interrupted" and rec.outcome == "degraded"


def test_recorder_reported_loss_is_carried_and_degrades(tmp_path):
    sess = make_session(tmp_path)
    data = load_health(sess / "capture_health.json").health.model_dump(mode="json")
    data["recorder"]["reported_loss_lines"] = ["Total lost: 12"]
    write_health_atomic(sess / "capture_health.json", CaptureHealth.model_validate(data))
    rec = inc.prepare_incident(sess, None, None, now=NOW).record
    assert rec.capture_health.recorder_reported_loss is True and rec.outcome == "degraded"


def test_bag_changed_since_capture_end_is_an_integrity_mismatch(tmp_path):
    sess = make_session(tmp_path)
    data = load_health(sess / "capture_health.json").health.model_dump(mode="json")
    data["output"]["files"][0]["sha256"] = "0" * 64  # the digest recorded at capture end no longer matches
    write_health_atomic(sess / "capture_health.json", CaptureHealth.model_validate(data))
    rec = inc.prepare_incident(sess, None, None, now=NOW).record
    assert rec.artifact_integrity.state == "mismatch" and rec.artifact_integrity.capture_end_digests == "mismatch"
    assert rec.outcome == "degraded"


def test_missing_segment_and_inconsistent_metadata_are_degraded_not_dropped(synth_profile, tmp_path):
    b = write_bag(tmp_path / "s" / "gone", three_way_split())
    (b / "gone_1.mcap").unlink()
    r = prep(b, synth_profile, tmp_path / "i1")
    states = {s.relative_path: s.state for s in r.record.segments}
    assert states["gone_1.mcap"] == "missing" and r.record.outcome == "degraded"
    assert not (r.incident_dir / "package" / "recording" / "gone" / "gone_1.mcap").exists()

    def bad_count(md):
        md["files"][0]["message_count"] += 5
    b2 = write_bag(tmp_path / "s" / "cnt", three_way_split(), metadata_edit=bad_count)
    r2 = prep(b2, synth_profile, tmp_path / "i2")
    assert r2.record.outcome == "degraded" and r2.record.session_status is SessionStatus.PARTIAL


def test_derive_outcome_matrix():
    from physicore_collector.incident import (
        CaptureHealthStatus, EvidenceCoverage, IntegrityStatus, derive_outcome,
    )
    ig = IntegrityStatus(state="verified", package_verification="passed", verified_artifact_count=1, manifest_sha256="a" * 64,
                         capture_end_digests="match", compared=[], not_established=[])
    cov = EvidenceCoverage(state="expected_signals_observed", profile_id="p", expected_topics=[], required_metadata=[],
                           observed_cadence_gap_count=0, cadence_checked_topics=[], topics_without_cadence_rules=[],
                           topics_recorded_but_not_in_profile=[], not_established=[])

    def ch(state, loss=None):
        return CaptureHealthStatus(state=state, basis="capture_health_record", recorder_reported_loss=loss, not_established=[])
    ok = SessionStatus.COMPLETE
    assert derive_outcome(ok, [], ig, ch("finalized", False), cov)[0] == "success"
    assert derive_outcome(ok, [], ig, ch("unknown"), cov)[0] == "success"
    for bad in ("interrupted", "failed", "degraded"):
        assert derive_outcome(ok, [], ig, ch(bad), cov)[0] == "degraded"
    assert derive_outcome(ok, [], ig, ch("finalized", True), cov)[0] == "degraded"
    assert derive_outcome(SessionStatus.PARTIAL, ["x"], ig, ch("finalized"), cov)[0] == "degraded"
    assert derive_outcome(ok, [], ig.model_copy(update={"state": "mismatch"}), ch("finalized"), cov)[0] == "degraded"


# ------------------------------------------------------------------ refusal & conflicts


def test_active_capture_is_refused_and_nothing_is_written(tmp_path):
    sess = make_session(tmp_path)
    data = load_health(sess / "capture_health.json").health.model_dump(mode="json")
    data["state"] = "running"
    data["bag_finalization"] = {"appears_finalized": None, "basis": "test"}
    write_health_atomic(sess / "capture_health.json", CaptureHealth.model_validate(data))
    lock = FileLock(sess / "supervisor.lock")
    lock.acquire()  # a live supervisor
    try:
        with pytest.raises(IncidentRefusedError, match="still active"):
            inc.prepare_incident(sess, None, None)
    finally:
        lock.release()
    assert not (sess / "incident").exists() and not (sess / "incident_attempt.json").exists()


def test_existing_package_is_never_overwritten_and_is_conflict(bag, synth_profile, tmp_path):
    out = tmp_path / "inc"
    r = prep(bag, synth_profile, out)
    before = snap(out)
    other_profile = tmp_path / "other.yaml"
    other_profile.write_text(Path(synth_profile).read_text() + "\n# changed\n")
    with pytest.raises(IncidentConflictError, match="different incident"):
        prep(bag, other_profile, out)
    assert snap(out) == before and r.record.incident_id


@pytest.mark.parametrize("kind", ["file", "dir", "symlink"])
def test_non_incident_target_is_never_touched(bag, synth_profile, tmp_path, kind):
    out = tmp_path / "t"
    if kind == "file":
        out.write_text("keep")
    elif kind == "dir":
        out.mkdir(); (out / "keep.txt").write_text("keep")
    else:
        out.symlink_to(tmp_path / "nowhere")
    with pytest.raises(IncidentConflictError):
        prep(bag, synth_profile, out)
    if kind == "file":
        assert out.read_text() == "keep"
    if kind == "dir":
        assert os.listdir(out) == ["keep.txt"]


def test_tampered_existing_incident_for_same_input_is_a_conflict_not_success(bag, synth_profile, tmp_path):
    out = tmp_path / "inc"
    prep(bag, synth_profile, out)
    seg = next((out / "package" / "recording").rglob("*.mcap"))
    seg.write_bytes(seg.read_bytes() + b"x")
    with pytest.raises(IncidentConflictError, match="fails verification"):
        prep(bag, synth_profile, out)


def test_output_inside_the_recording_is_refused(bag, synth_profile):
    with pytest.raises(InputError, match="inside the recording"):
        prep(bag, synth_profile, bag / "incident")
    assert sorted(os.listdir(bag)) == ["bag1_0.mcap", "metadata.yaml"]


def test_directory_without_recordings_is_a_clear_failure(synth_profile, tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(NoRecordingError):
        prep(tmp_path / "empty", synth_profile, tmp_path / "i")
    assert not (tmp_path / "i").exists()


def test_concurrent_preparation_of_the_same_target_is_refused(bag, synth_profile, tmp_path):
    out = tmp_path / "inc"
    lock = FileLock(tmp_path / ".inc.prepare.lock")
    lock.acquire()
    try:
        with pytest.raises(IncidentError) as e:
            prep(bag, synth_profile, out)
        assert e.value.code == "preparation_in_progress"
    finally:
        lock.release()
    assert not out.exists()
    prep(bag, synth_profile, out)  # and works once the other one is done


# ------------------------------------------------------------------ retry, idempotency, failures


def test_repeating_on_unchanged_source_is_idempotent_and_writes_nothing(bag, synth_profile, tmp_path):
    out = tmp_path / "inc"
    first = prep(bag, synth_profile, out)
    before = snap(out)
    again = prep(bag, synth_profile, out)
    assert again.state == "already_prepared" and again.record == first.record and snap(out) == before


def test_checksum_mismatch_during_copy_publishes_nothing_and_leaves_source_intact(bag, synth_profile, tmp_path, monkeypatch):
    real = export_mod.sha256_file
    monkeypatch.setattr(export_mod, "sha256_file", lambda p, **k: "f" * 64 if "staging" in str(p) else real(p, **k))
    before = snap(bag)
    with pytest.raises(Exception) as e:
        prep(bag, synth_profile, tmp_path / "inc")
    assert inc.failure_info(e.value)[0] == "checksum_mismatch"
    assert not (tmp_path / "inc").exists() and not [p for p in tmp_path.iterdir() if "staging" in p.name]
    assert snap(bag) == before


def test_insufficient_space_fails_before_copying_anything(bag, synth_profile, tmp_path):
    with pytest.raises(InsufficientSpaceError, match="Nothing was copied") as e:
        prep(bag, synth_profile, tmp_path / "inc", free_space=lambda p: 1000)
    assert e.value.code == "insufficient_space"
    assert not (tmp_path / "inc").exists() and not [p for p in tmp_path.iterdir() if "staging" in p.name]


def test_space_reserve_comes_from_the_profile_critical_threshold(bag, tmp_path):
    prof = tmp_path / "p.yaml"
    prof.write_text(PROFILE_11.replace('"1.1"', '"1.2"').replace(
        "capture:\n", "capture:\n  disk_critical_free_bytes: 5000000\n  disk_warn_free_bytes: 9000000\n"))
    need_plain = 15_000 + inc.FIXED_OVERHEAD_BYTES
    with pytest.raises(InsufficientSpaceError, match="5000000 reserve"):
        prep(bag, prof, tmp_path / "inc", free_space=lambda p: need_plain + 1_000_000)  # enough without, not with the reserve
    prep(bag, prof, tmp_path / "inc", free_space=lambda p: 10**12)


def test_dry_run_reports_the_plan_and_writes_nothing(bag, synth_profile, tmp_path):
    before_parent = sorted(os.listdir(tmp_path))
    r = prep(bag, synth_profile, tmp_path / "inc", dry_run=True, free_space=lambda p: 123)
    assert r.state == "dry_run" and r.plan.copy_bytes > 0 and r.plan.free_bytes == 123 and r.plan.required_bytes > 123
    assert sorted(os.listdir(tmp_path)) == before_parent


def test_enospc_while_copying_is_reported_cleanly_and_cleaned_up(bag, synth_profile, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(export_mod, "copy_and_hash", boom)
    before = snap(bag)
    with pytest.raises(Exception) as e:
        prep(bag, synth_profile, tmp_path / "inc")
    assert inc.failure_info(e.value)[0] == "insufficient_space"
    assert not (tmp_path / "inc").exists() and not [p for p in tmp_path.iterdir() if "staging" in p.name]
    assert snap(bag) == before


def test_interrupt_midway_cleans_staging_and_retry_succeeds(bag, synth_profile, tmp_path, monkeypatch):
    real = export_mod.copy_and_hash
    monkeypatch.setattr(export_mod, "copy_and_hash", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        prep(bag, synth_profile, tmp_path / "inc")
    assert not (tmp_path / "inc").exists() and not [p for p in tmp_path.iterdir() if "staging" in p.name]
    monkeypatch.setattr(export_mod, "copy_and_hash", real)
    assert prep(bag, synth_profile, tmp_path / "inc").state == "prepared"


def test_leftover_staging_of_a_killed_run_is_removed_by_the_next_run(bag, synth_profile, tmp_path):
    stale = tmp_path / ".inc.incident-staging-1-deadbeef"
    (stale / "package").mkdir(parents=True)
    (stale / "package" / "half.bin").write_bytes(b"x" * 100)
    r = prep(bag, synth_profile, tmp_path / "inc")
    assert not stale.exists() and any("stale staging" in n for n in r.notes)


def test_failed_default_attempt_is_recorded_listed_and_retry_clears_it(tmp_path, monkeypatch):
    sess = make_session(tmp_path)
    monkeypatch.setattr(export_mod, "copy_and_hash", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, "I/O error")))
    out = inc.auto_prepare_after_capture(sess)
    assert out.state == "failed" and out.code == "io_error"
    att = json.loads((sess / "incident_attempt.json").read_text())
    assert att["state"] == "failed" and att["code"] == "io_error" and f"incident prepare {sess}" in att["retry_command"]
    assert not (sess / "incident").exists()
    (row,) = inc.list_incidents(tmp_path / "caps")
    assert row.state == "failed" and "Retry:" in row.detail
    monkeypatch.undo()
    assert inc.auto_prepare_after_capture(sess).state == "prepared"
    assert json.loads((sess / "incident_attempt.json").read_text())["state"] == "prepared"
    assert inc.list_incidents(tmp_path / "caps")[0].state == "previously_prepared"


def test_auto_prepare_never_raises_whatever_happens(tmp_path, monkeypatch):
    sess = make_session(tmp_path)
    for exc, code in ((RuntimeError("bug"), "internal_error"), (KeyboardInterrupt(), "interrupted")):
        def boom(*a, _e=exc, **k):
            raise _e
        monkeypatch.setattr(inc, "prepare_incident", boom)
        out = inc.auto_prepare_after_capture(sess)
        assert out.state == "failed" and out.code == code
    assert inc.auto_prepare_after_capture(tmp_path / "nonexistent").state == "failed"


def test_auto_prepare_skips_when_nothing_was_recorded(tmp_path):
    sess = make_session(tmp_path, "start_fail")
    out = inc.auto_prepare_after_capture(sess)
    assert out.state == "skipped" and not (sess / "incident").exists() and not (sess / "incident_attempt.json").exists()


def test_auto_prepare_runs_only_after_locks_are_released(tmp_path, monkeypatch):
    """Preparation must never hold up a stop or the next capture: by the time it runs, the supervisor locks are free."""
    from physicore_collector.sessionlock import lock_is_held_elsewhere
    sess = make_session(tmp_path)
    seen = {}
    real = inc.prepare_incident

    def spy(*a, **k):
        seen["session_lock_held"] = lock_is_held_elsewhere(sess / "supervisor.lock")
        seen["active_lock_held"] = lock_is_held_elsewhere(sess.parent / ".active-capture.lock")
        return real(*a, **k)
    monkeypatch.setattr(inc, "prepare_incident", spy)
    assert inc.auto_prepare_after_capture(sess).state == "prepared"
    assert seen == {"session_lock_held": False, "active_lock_held": False}


# ------------------------------------------------------------------ listing, loading, verification


def test_list_and_load_and_verify_roundtrip(bag, synth_profile, tmp_path):
    root = tmp_path / "incidents"
    root.mkdir()
    prep(bag, synth_profile, root / "one")
    (row,) = inc.list_incidents(root)
    assert (row.name, row.state, row.outcome) == ("one", "previously_prepared", "success")  # stored result, never "verified"
    rec, d = inc.load_incident(root / "one" / "incident.json")
    assert d == root / "one" and rec.incident_schema_version == "1.0"
    assert inc.verify_incident(d) == []


def test_verify_detects_tampering_and_unexpected_entries(bag, synth_profile, tmp_path):
    d = prep(bag, synth_profile, tmp_path / "i").incident_dir
    (d / "stray.txt").write_text("x")
    assert any("stray.txt" in p for p in inc.verify_incident(d))
    (d / "stray.txt").unlink()
    m = d / "package" / "manifest.json"
    m.write_bytes(m.read_bytes().replace(b"complete", b"partial", 1))
    assert inc.verify_incident(d)


def test_unsupported_or_invalid_incident_files_are_rejected(bag, synth_profile, tmp_path):
    d = prep(bag, synth_profile, tmp_path / "i").incident_dir
    data = json.loads((d / "incident.json").read_text())
    (d / "incident.json").write_text(json.dumps({**data, "incident_schema_version": "9.9"}))
    with pytest.raises(IncidentError, match="Unsupported"):
        inc.load_incident(d)
    (d / "incident.json").write_text(json.dumps({**data, "surprise": 1}))
    with pytest.raises(IncidentError, match="not a valid incident"):
        inc.load_incident(d)
    with pytest.raises(IncidentError, match="No incident found"):
        inc.load_incident(tmp_path)


def test_list_marks_sessions_without_incident(tmp_path):
    make_session(tmp_path)
    (row,) = inc.list_incidents(tmp_path / "caps")
    assert row.state == "not_prepared" and "incident prepare" in row.detail


# ------------------------------------------------------------------ memory & offline


def _big_mcap(path: Path, mib: int) -> None:
    payload = b"\xa5" * (256 * 1024)
    with open(path, "wb") as f:
        w = Writer(f, chunk_size=1024 * 1024, compression=CompressionType.NONE)
        w.start(profile="", library="test")
        w.add_metadata("session", {"operator": "a", "session_label": "big"})
        sid = w.register_schema(name="t/Big", encoding="jsonschema", data=b'{"type":"object"}')
        ch = w.register_channel(topic="/big", message_encoding="json", schema_id=sid)
        for i in range(mib * 4):
            t = mf.BASE_NS + i * 10_000_000
            w.add_message(channel_id=ch, log_time=t, publish_time=t, sequence=i, data=payload)
        w.finish()


def test_large_recording_is_streamed_not_loaded_into_memory(tmp_path):
    prof = tmp_path / "p.yaml"
    prof.write_text(yaml.safe_dump({"profile_schema_version": "1.0", "profile_id": "big", "log_time_basis": "system_time",
                                    "required_metadata_keys": ["operator"], "topics": [{"name": "/big"}]}))
    big = tmp_path / "big.mcap"
    _big_mcap(big, 64)
    size = big.stat().st_size
    assert size > 64 * 1024 * 1024
    tracemalloc.start()
    try:
        r = prep(big, prof, tmp_path / "inc")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert r.state == "prepared" and (r.incident_dir / "package" / "recording" / "big.mcap").stat().st_size == size
    assert peak < size / 4, f"peak traced memory {peak} B for a {size} B recording: payload was not streamed"


def test_preparation_needs_no_network(bag, synth_profile, tmp_path, monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    r = prep(bag, synth_profile, tmp_path / "inc")
    assert r.state == "prepared" and inc.verify_incident(r.incident_dir) == []


# ------------------------------------------------------------------ compatibility with frozen older artifacts

COMPAT = Path(__file__).parent / "compat"


def test_frozen_day2_session_with_a_1_0_sidecar_can_be_prepared(tmp_path):
    """A real Day 2 session (capture-health schema 1.0, produced by the untouched Day 2 code) still prepares."""
    src = tmp_path / "day2"
    shutil.copytree(COMPAT / "day2_session", src)
    r = prep(src / "bag", COMPAT / "day2_profile.yaml", tmp_path / "inc", health=src / "capture_health.json")
    assert r.record.capture_health.basis == "capture_health_record" and r.record.capture_health.state in ("finalized", "failed")
    assert inc.verify_incident(r.incident_dir) == [] and verify_package(r.incident_dir / "package") == []
    assert json.loads((r.incident_dir / "package" / "capture" / "capture_health.json").read_text())["capture_health_schema_version"] == "1.0"


def test_frozen_day1_recording_prepares_with_the_day1_manifest_shape(synth_profile, tmp_path):
    r = prep(COMPAT / "day1_package" / "recording" / "valid.mcap", synth_profile, tmp_path / "inc")
    m = json.loads((r.incident_dir / "package" / "manifest.json").read_text())
    assert m["manifest_schema_version"] == "0.1" and not {"bag", "recording_kind", "capture_health"} & set(m)
