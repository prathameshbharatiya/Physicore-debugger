"""Phase 1: the read-only, versioned evidence inventory (physicore_collector.inventory + `evidence inventory`).

Everything here is OFFLINE inspection of synthetic files. Where a capture session is used it is produced by the
scripted FakeAdapter (tests/recorder_doubles.py): it is NOT a recording made by a live ROS 2 / rosbag2 process, so none of
these tests shows that live capture works (see tests/ros2_integration for the skipped real-ROS tests).
"""

import builtins
import fcntl
import hashlib
import io
import json
import os
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app
from physicore_collector.capture_health import CaptureHealth, load_health
from physicore_collector.export import export_package
from physicore_collector.inventory import (
    INVENTORY_SCHEMA_VERSION, EvidenceInventory, classify_input, exit_code, inspect_evidence, inventory_json, render_inventory,
)
from physicore_collector.schemas import SessionStatus, relative_path_problem
from physicore_collector.session import analyze_session
from physicore_collector.supervisor import write_health_atomic
from tests import make_fixtures as mf
from tests.bagtools import three_way_split, write_bag
from tests.test_incident import NOW, make_session, prep

runner = CliRunner()


# ------------------------------------------------------------------ helpers

def tree_state(root: Path) -> dict:
    """Every entry under root (files, dirs, symlinks, empty dirs): type, size, mtime_ns, sha256. Proves nothing changed."""
    root = Path(root)
    out: dict = {}
    entries = [root] if (root.is_file() or root.is_symlink()) else [root, *sorted(root.rglob("*"))]
    for p in entries:
        rel = "." if p == root else p.relative_to(root).as_posix()
        if p.is_symlink():
            out[rel] = ("symlink", os.readlink(p))
        elif p.is_dir():
            out[rel] = ("dir", p.stat().st_mtime_ns)
        else:
            out[rel] = ("file", p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def codes(items) -> set:
    return {i.code for i in items}


def art(inv: EvidenceInventory, rel: str):
    return next(a for a in inv.artifacts if a.relative_path == rel)


def gap_kinds(inv: EvidenceInventory) -> set:
    return {g.kind for g in inv.gaps}


@pytest.fixture
def split_incident(synth_profile, tmp_path):
    b = write_bag(tmp_path / "src" / "bagm", three_way_split())
    return prep(b, synth_profile, tmp_path / "inc").incident_dir


@pytest.fixture
def mcap_incident(fx, synth_profile, tmp_path):
    return prep(fx / "valid.mcap", synth_profile, tmp_path / "inc1").incident_dir


def forbid_writes(monkeypatch):
    """Any attempt to open for writing, create, rename, remove, re-stamp, re-own or LOCK a path raises: the inspection must not need any.
    A function (not only a fixture) so a scenario can be built first and writes forbidden afterwards."""
    real_open, real_os_open = builtins.open, os.open

    def guarded_open(file, mode="r", *a, **k):
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            raise AssertionError(f"write attempted: open({file!r}, {mode!r})")
        return real_open(file, mode, *a, **k)

    def guarded_os_open(path, flags, *a, **k):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError(f"write attempted: os.open({path!r})")
        return real_os_open(path, flags, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_open)  # pathlib's read_bytes/write_text go through io.open, not builtins.open
    monkeypatch.setattr(os, "open", guarded_os_open)
    for name in ("mkdir", "makedirs", "rename", "replace", "remove", "unlink", "rmdir", "utime", "chmod", "symlink", "link", "truncate",
                 "chown", "lchown", "mkfifo", "mknod", "removedirs", "renames", "fchmod", "fchown", "ftruncate"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(f"os.{_n} attempted")))
    for name in ("copyfile", "copy", "copy2", "copytree", "move", "rmtree"):
        monkeypatch.setattr(shutil, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(f"shutil.{_n} attempted")))
    for name in ("flock", "lockf"):  # taking a lock is a modification of shared state too
        monkeypatch.setattr(fcntl, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(f"fcntl.{_n} (lock) attempted")))


@pytest.fixture
def write_guard(monkeypatch):
    """See `forbid_writes`."""
    forbid_writes(monkeypatch)


# ------------------------------------------------------------------ hostile / unusual layouts (shared by several tests)

HOSTILE = [
    "symlinked_parent", "symlinked_package_dir", "symlinked_manifest", "unsafe_manifest_path", "unsafe_segment_path_slash",
    "unsafe_segment_path_backslash", "session_symlinked_incident", "session_symlinked_incident_json", "session_symlinked_bag",
    "session_symlinked_health", "package_health_symlinked_parent", "unlisted_symlinked_dir", "backslash_file_name_in_bag",
]
# the error code each hostile layout must produce (None = a package-integrity finding rather than an error)
HOSTILE_ERROR = {
    "symlinked_parent": "unsafe_path", "symlinked_package_dir": "unsafe_path", "symlinked_manifest": "unsafe_path",
    "unsafe_manifest_path": "manifest_invalid", "unsafe_segment_path_slash": "unsafe_path", "unsafe_segment_path_backslash": "unsafe_path",
    "session_symlinked_incident": "unsafe_path", "session_symlinked_incident_json": "unsafe_path", "session_symlinked_bag": "unsafe_path",
    "session_symlinked_health": "unsafe_path", "package_health_symlinked_parent": "unsafe_path", "unlisted_symlinked_dir": None,
    "backslash_file_name_in_bag": "unsafe_path",
}


def build_hostile(kind: str, incident: Path, fx: Path, tmp_path: Path) -> Path:
    """Build one hostile layout and return the path to inspect. Everything a symlink points at lives in tmp_path/outside_area,
    so 'did anything outside the evidence root get opened or listed?' is a prefix test. `incident` is a fresh valid incident."""
    outside = tmp_path / "outside_area"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("must never be read")

    def relink(link: Path, name: str) -> None:
        shutil.move(str(link), str(outside / name))
        link.symlink_to(outside / name)

    if kind == "symlinked_parent":
        relink(incident / "package" / "recording", "recording")
        return incident
    if kind == "symlinked_package_dir":
        relink(incident / "package", "package")
        return incident
    if kind == "symlinked_manifest":
        relink(incident / "package" / "manifest.json", "manifest.json")
        return incident / "package"
    if kind == "unsafe_manifest_path":
        mp = incident / "package" / "manifest.json"
        data = json.loads(mp.read_text())
        data["artifacts"][0]["relative_path"] = "..\\..\\evil"
        mp.write_text(json.dumps(data))
        return incident / "package"
    if kind.startswith("unsafe_segment_path"):
        (outside / "escape.mcap").write_bytes((fx / "valid.mcap").read_bytes())
        target = "../../outside_area/escape.mcap" if kind.endswith("slash") else "..\\..\\outside_area\\escape.mcap"

        def edit(md):
            md["relative_file_paths"][0] = target
            md["files"][0]["path"] = target
        return write_bag(tmp_path / "rawu" / "bagu", three_way_split(), metadata_edit=edit)
    if kind == "backslash_file_name_in_bag":
        bag = write_bag(tmp_path / "rawb" / "bagb", three_way_split())
        (bag / "we\\ird.mcap").write_bytes((fx / "valid.mcap").read_bytes())
        return bag
    if kind == "unlisted_symlinked_dir":
        (incident / "package" / "extra").symlink_to(outside)
        return incident
    # capture-session layouts
    (tmp_path / "sess").mkdir(exist_ok=True)
    sess = make_session(tmp_path / "sess")
    if kind.startswith("session_symlinked_incident") or kind == "package_health_symlinked_parent":
        prep(sess, None, None)
    if kind == "session_symlinked_incident":
        relink(sess / "incident", "incident")
    elif kind == "session_symlinked_incident_json":
        relink(sess / "incident" / "incident.json", "incident.json")
    elif kind == "session_symlinked_bag":
        relink(sess / "bag", "bag")
    elif kind == "session_symlinked_health":
        relink(sess / "capture_health.json", "capture_health.json")
    elif kind == "package_health_symlinked_parent":
        relink(sess / "incident" / "package" / "capture", "capture")
    else:
        raise AssertionError(kind)
    return sess


@pytest.fixture
def read_spy(monkeypatch):
    """Real paths of every file opened and every directory listed while active."""
    seen: list[str] = []
    real_open, real_scandir, real_listdir = io.open, os.scandir, os.listdir

    def spy_open(file, *a, **k):
        if isinstance(file, (str, bytes, os.PathLike)):
            seen.append(os.path.realpath(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr(os, "scandir", lambda p=".": (seen.append(os.path.realpath(p)), real_scandir(p))[1])
    monkeypatch.setattr(os, "listdir", lambda p=".": (seen.append(os.path.realpath(p)), real_listdir(p))[1])
    return seen


# ------------------------------------------------------------------ valid inputs

def test_valid_split_bag_incident_inventory(split_incident):
    inv = inspect_evidence(split_incident)
    assert inv.inventory_schema_version == INVENTORY_SCHEMA_VERSION == "1.0"
    assert inv.provenance.input.kind == "incident" and inv.provenance.read_only is True
    assert inv.inspection.state == "complete" and inv.errors == []
    assert inv.integrity.state == "verified_now" and inv.integrity.checked_now and inv.provenance.integrity_checked_now
    assert inv.session_status is SessionStatus.COMPLETE and exit_code(inv) == 0
    # split MCAP / bag directory: segments kept in metadata order, counts straight from the existing scanner
    assert inv.recording.kind == "rosbag2_directory" and inv.recording.segment_order_basis == "metadata"
    assert [s.relative_path for s in inv.recording.segments] == ["bagm_0.mcap", "bagm_1.mcap", "bagm_2.mcap"]
    assert [s.observed_message_count for s in inv.recording.segments] == [42, 41, 40] and inv.recording.total_message_count == 123
    assert all(s.state == "readable" for s in inv.recording.segments)
    assert [s.artifact_ref for s in inv.recording.segments] == [f"artifact:package/recording/bagm/bagm_{i}.mcap" for i in range(3)]
    assert {t.topic: t.message_count for t in inv.topics} == {"/joint_states": 100, "/camera/image": 20, "/diagnostics": 3}
    # every artifact is addressable by a safe relative reference and verified, except the two unhashed-by-design wrappers
    assert all(a.ref_id == f"artifact:{a.relative_path}" and not a.relative_path.startswith(("/", "..")) for a in inv.artifacts)
    manifest_listed = [a for a in inv.artifacts if a.sha256_basis == "recorded_in_manifest"]
    assert len(manifest_listed) == 6 and all(a.integrity == "verified" for a in manifest_listed)
    assert art(inv, "incident.json").integrity == "not_applicable" and art(inv, "package/manifest.json").integrity == "verified"
    assert inv.incident is not None and inv.incident.outcome in ("success", "degraded")
    assert {"known_gap"} <= gap_kinds(inv) and any(g.code == "capture_health_unavailable" for g in inv.gaps)


def test_inventory_round_trips_through_its_own_schema(split_incident):
    inv = inspect_evidence(split_incident)
    again = EvidenceInventory.model_validate_json(inventory_json(inv))
    assert again == inv


def test_single_mcap_incident_and_export_package(mcap_incident, fx, synth_profile, tmp_path):
    inv = inspect_evidence(mcap_incident)
    assert inv.recording.kind == "mcap_file" and len(inv.recording.segments) == 1 and inv.recording.total_message_count == 123
    assert inv.recording.segments[0].artifact_ref == "artifact:package/recording/valid.mcap"
    assert inv.integrity.manifest_schema_version == "0.1"
    export_package(analyze_session(fx / "valid.mcap", synth_profile), tmp_path / "pkg", now=NOW)
    pk = inspect_evidence(tmp_path / "pkg")
    assert pk.provenance.input.kind == "package" and pk.integrity.state == "verified_now" and pk.incident is None
    assert art(pk, "manifest.json").integrity == "not_applicable" and art(pk, "recording/valid.mcap").integrity == "verified"


def test_capture_session_incident_carries_capture_health_and_never_claims_live_capture(tmp_path):
    sess = make_session(tmp_path)
    prep(sess, None, None)  # incident goes to <session>/incident
    inv = inspect_evidence(sess)  # a capture session directory that holds an incident
    assert inv.provenance.input.kind == "incident" and inv.integrity.state == "verified_now"
    ch = inv.capture_health
    assert ch.present and ch.source == "package_artifact" and ch.state == "stopped_clean" and ch.appears_finalized is True
    assert ch.recorder_loss_warning_seen is False and ch.no_loss_claim_permitted is False and ch.recorder_statistics_available is False
    assert any("messages dropped between publisher and recorder" in u for u in ch.unknown)
    assert inv.provenance.acquisition.mode == "offline_inspection" and inv.provenance.acquisition.live_ros2_capture_verified is False
    assert inv.provenance.acquisition.capture_health_record_present is True


def test_interrupted_capture_and_recorder_loss_are_visible_not_hidden(tmp_path):
    sess = make_session(tmp_path, "exit_early_with_output")
    prep(sess, None, None)
    inv = inspect_evidence(sess)
    # two separate statements, both kept: the sidecar's own state, and the incident's stored (mapped) capture-health state
    assert inv.capture_health.state == "exited_unexpectedly" and inv.capture_health.appears_finalized is not True
    assert inv.incident is not None and inv.incident.stored_capture_health.state == "interrupted"
    assert exit_code(inv) == 1 and any(g.code == "capture_not_cleanly_ended" and g.severity == "warning" for g in inv.gaps)
    (tmp_path / "two").mkdir()
    s2 = make_session(tmp_path / "two", "clean")
    data = load_health(s2 / "capture_health.json").health.model_dump(mode="json")
    data["recorder"]["reported_loss_lines"] = ["Total lost: 12"]
    write_health_atomic(s2 / "capture_health.json", CaptureHealth.model_validate(data))
    prep(s2, None, None)
    inv2 = inspect_evidence(s2)
    assert inv2.capture_health.recorder_loss_warning_seen and inv2.capture_health.recorder_loss_lines == ["Total lost: 12"]
    assert any(g.code == "recorder_reported_message_loss" for g in inv2.gaps)
    assert inv2.capture_health.no_loss_claim_permitted is False


# ------------------------------------------------------------------ raw recordings (inspected, not validated)

def test_raw_split_bag_directory_and_loose_mcap_files(tmp_path):
    bag = write_bag(tmp_path / "raw" / "bagr", three_way_split())
    inv = inspect_evidence(bag)
    assert inv.provenance.input.kind == "raw_bag" and inv.session_status is None
    assert inv.integrity.state == "not_applicable" and not inv.integrity.checked_now and not inv.provenance.integrity_checked_now
    assert [s.observed_message_count for s in inv.recording.segments] == [42, 41, 40] and inv.recording.bag_directory == "."
    assert [s.artifact_ref for s in inv.recording.segments][0] == "artifact:bagr_0.mcap"
    assert all(a.sha256 and a.sha256_basis == "computed_now" and a.integrity == "not_applicable" for a in inv.artifacts)
    assert "not_validated" in codes(inv.warnings) and exit_code(inv) == 0
    # a directory of loose segments, no metadata.yaml: order inferred and said so
    nometa = write_bag(tmp_path / "raw" / "nometa", three_way_split(), with_metadata=False)
    inv2 = inspect_evidence(nometa)
    assert inv2.recording.segment_order_basis == "inferred_from_observed_log_time" and inv2.recording.metadata_present is False
    assert [s.relative_path for s in inv2.recording.segments] == ["nometa_0.mcap", "nometa_1.mcap", "nometa_2.mcap"]
    gaps2 = {(g.kind, g.code) for g in inv2.gaps}
    assert ("check_failed", "bag:metadata") in gaps2 and ("check_inconclusive", "bag:segments") in gaps2  # the Collector's own checks, surfaced
    assert inv2.recording.readability.value == "partially_readable" and exit_code(inv2) == 1


def test_raw_capture_session_without_incident_uses_its_sidecar(tmp_path):
    sess = make_session(tmp_path)
    inv = inspect_evidence(sess)
    assert inv.provenance.input.kind == "raw_bag" and inv.capture_health.source == "supplied_sidecar"
    assert inv.recording.bag_directory == "bag" and inv.recording.segments[0].artifact_ref == "artifact:bag/bag_0.mcap"
    assert {d.id for d in inv.timebase.clock_domains} >= {"mcap_log_time", "rosbag2_metadata_time", "capture_supervisor_wall_clock"}


def test_raw_single_mcap_flags_that_siblings_were_not_included(fx):
    inv = inspect_evidence(fx / "valid.mcap")
    assert inv.provenance.input.kind == "raw_mcap" and inv.recording.total_message_count == 123
    assert "sibling_files_not_included" in codes(inv.warnings) and any(g.code == "capture_health_unavailable" for g in inv.gaps)


# ------------------------------------------------------------------ missing / unsupported / malformed

def test_missing_artifact_is_reported_per_artifact_and_overall(split_incident):
    (split_incident / "package" / "recording" / "bagm" / "bagm_1.mcap").unlink()
    inv = inspect_evidence(split_incident)
    assert inv.integrity.state == "tampered" and exit_code(inv) == 1
    a = art(inv, "package/recording/bagm/bagm_1.mcap")
    assert a.integrity == "missing" and a.sha256 and a.sha256_basis == "recorded_in_manifest"  # the RECORDED digest, not an invented one
    assert art(inv, "package/recording/bagm/bagm_0.mcap").integrity == "verified"
    assert any(g.kind == "integrity" and g.severity == "error" and g.artifact_ref == a.ref_id for g in inv.gaps)
    assert inv.inspection.state == "complete"  # the inventory itself finished; the EVIDENCE is bad


def test_stored_results_are_labelled_and_flagged_when_integrity_fails(split_incident, tmp_path):
    ok = inspect_evidence(split_incident)
    assert ok.provenance.content_basis == "stored_manifest" and "stored_results_may_not_describe_current_bytes" not in codes(ok.warnings)
    assert "stored manifest results" in render_inventory(ok)
    (split_incident / "package" / "recording" / "bagm" / "bagm_0.mcap").write_bytes(b"junk")
    bad = inspect_evidence(split_incident)
    assert bad.integrity.state == "tampered" and bad.provenance.content_basis == "stored_manifest"
    assert "stored_results_may_not_describe_current_bytes" in codes(bad.warnings)  # a stale "complete / 123 messages" must not pass as current
    assert bad.session_status is SessionStatus.COMPLETE  # still the STORED value, now labelled as such
    raw = inspect_evidence(write_bag(tmp_path / "rawc" / "bagc", three_way_split()))
    assert raw.provenance.content_basis == "scanned_now" and "stored_results_may_not_describe_current_bytes" not in codes(raw.warnings)


def test_checksum_failure_flags_only_the_modified_artifact(split_incident):
    seg = split_incident / "package" / "recording" / "bagm" / "bagm_2.mcap"
    raw = bytearray(seg.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    seg.write_bytes(bytes(raw))
    inv = inspect_evidence(split_incident)
    assert inv.integrity.state == "tampered" and exit_code(inv) == 1
    assert art(inv, "package/recording/bagm/bagm_2.mcap").integrity == "mismatch"
    assert {a.relative_path for a in inv.artifacts if a.integrity == "mismatch"} == {"package/recording/bagm/bagm_2.mcap"}
    assert any("sha256" in p for p in inv.integrity.problems)


def test_unlisted_file_in_package_is_listed_and_flagged(split_incident):
    (split_incident / "package" / "extra.bin").write_bytes(b"not in the manifest")
    (split_incident / "stray.txt").write_text("not allowed in an incident directory")
    inv = inspect_evidence(split_incident)
    assert inv.integrity.state == "tampered"
    assert art(inv, "package/extra.bin").integrity == "unlisted" and not art(inv, "package/extra.bin").supported
    assert art(inv, "stray.txt").integrity == "unlisted"
    assert {"unlisted_artifact", "unsupported_artifact"} <= gap_kinds(inv)


def test_unsupported_storage_is_reported_not_guessed(tmp_path):
    bag = write_bag(tmp_path / "db3bag", storage="db3")
    inv = inspect_evidence(bag)
    assert inv.inspection.state == "complete" and exit_code(inv) == 1
    assert inv.recording.segments and inv.recording.segments[0].state == "unsupported"
    assert any(g.kind == "segment_not_readable" and g.code == "segment_unsupported" for g in inv.gaps)
    assert inv.recording.total_message_count is None and inv.topics == []  # nothing was parsed, so nothing is claimed
    db3 = art(inv, "db3bag_0.db3")
    assert db3.kind == "recording_unsupported" and db3.supported is False
    assert any(g.kind == "unsupported_artifact" and g.artifact_ref == db3.ref_id for g in inv.gaps)


def test_missing_segment_listed_by_metadata_stays_visible(tmp_path):
    bag = write_bag(tmp_path / "gone", three_way_split())
    (bag / "gone_1.mcap").unlink()
    inv = inspect_evidence(bag)
    seg = {s.relative_path: s for s in inv.recording.segments}
    assert seg["gone_1.mcap"].state == "missing" and seg["gone_1.mcap"].observed_message_count is None
    assert inv.recording.total_message_count is None  # no total is invented while a segment's count is unknown
    assert exit_code(inv) == 1 and any(g.kind == "segment_not_readable" for g in inv.gaps)


def test_truncated_recording_is_partial_and_counts_are_not_invented(fx, synth_profile, tmp_path):
    inv = inspect_evidence(fx / "truncated.mcap")
    assert inv.recording.readability.value == "partially_readable" and exit_code(inv) == 1
    assert any(g.code == "recording_not_fully_readable" for g in inv.gaps)
    full = inspect_evidence(fx / "valid.mcap")
    assert inv.recording.total_message_count < full.recording.total_message_count
    incident = prep(fx / "truncated.mcap", synth_profile, tmp_path / "inc").incident_dir
    inc = inspect_evidence(incident)
    assert inc.integrity.state == "verified_now" and inc.session_status is SessionStatus.PARTIAL and exit_code(inc) == 1
    assert any(g.code == "recording_incomplete" and g.severity == "warning" for g in inc.gaps)


def test_malformed_manifest_in_package_is_a_structured_error(mcap_incident):
    pkg = mcap_incident / "package"
    (pkg / "manifest.json").write_text("{ this is not json")
    inv = inspect_evidence(pkg)
    assert inv.inspection.state == "failed" and exit_code(inv) == 2
    assert "manifest_invalid" in codes(inv.errors) and inv.integrity.state == "unverifiable"
    assert inv.recording.kind is None and inv.topics == [] and inv.session_status is None  # nothing fabricated
    (pkg / "manifest.json").write_text(json.dumps({"manifest_schema_version": "9.9"}))
    assert "manifest_invalid" in codes(inspect_evidence(pkg).errors)


def test_malformed_manifest_inside_a_valid_incident_is_partial(mcap_incident):
    (mcap_incident / "package" / "manifest.json").write_text("[]")
    inv = inspect_evidence(mcap_incident)
    assert inv.inspection.state == "partial" and "manifest_invalid" in codes(inv.errors) and exit_code(inv) == 1
    assert inv.integrity.state == "unverifiable" and inv.incident is not None  # the STORED record is still reported, as stored
    assert inv.provenance.integrity_checked_now is True


@pytest.mark.parametrize("content,code", [
    ("{broken", "incident_unreadable"),
    (json.dumps({"incident_schema_version": "99.0"}), "incident_unsupported"),
    (json.dumps({"incident_schema_version": "1.0"}), "incident_invalid"),
])
def test_malformed_incident_record_falls_back_to_the_package_with_a_structured_error(mcap_incident, content, code):
    (mcap_incident / "incident.json").write_text(content)
    inv = inspect_evidence(mcap_incident)
    assert code in codes(inv.errors) and "incident_record_unusable" in codes(inv.warnings)
    assert inv.incident is None and inv.inspection.state == "partial"
    assert inv.integrity.state == "verified_now"  # the package itself is still intact and still checked


def test_incident_without_package_and_unsupported_inputs_fail_cleanly(mcap_incident, tmp_path):
    shutil.rmtree(mcap_incident / "package")
    inv = inspect_evidence(mcap_incident)
    assert "package_missing" in codes(inv.errors) and inv.integrity.state == "unverifiable" and inv.inspection.state in ("partial", "failed")
    nope = inspect_evidence(tmp_path / "does-not-exist")
    assert nope.inspection.state == "failed" and "input_not_found" in codes(nope.errors) and exit_code(nope) == 2
    (tmp_path / "empty").mkdir()
    (tmp_path / "note.txt").write_text("hello")
    for p in (tmp_path / "empty", tmp_path / "note.txt"):
        r = inspect_evidence(p)
        assert r.provenance.input.kind == "unsupported" and "unsupported_input" in codes(r.errors) and exit_code(r) == 2
        assert r.artifacts == [] and r.recording.segments == []


def test_symlink_input_and_symlinked_artifact_are_not_followed(split_incident, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(split_incident)
    assert "input_is_symlink" in codes(inspect_evidence(link).errors)
    seg = split_incident / "package" / "recording" / "bagm" / "bagm_0.mcap"
    outside = tmp_path / "outside.mcap"
    outside.write_bytes(seg.read_bytes())
    seg.unlink()
    seg.symlink_to(outside)
    inv = inspect_evidence(split_incident)
    assert art(inv, "package/recording/bagm/bagm_0.mcap").integrity == "unsafe" and inv.integrity.state == "tampered"


def test_classify_input(split_incident, fx, tmp_path):
    assert classify_input(split_incident) == classify_input(split_incident / "incident.json") == "incident"
    assert classify_input(split_incident / "package") == "package"
    assert classify_input(fx / "valid.mcap") == "raw_mcap" and classify_input(fx) == "raw_bag"
    assert classify_input(tmp_path / "nope") == "unsupported"


# ------------------------------------------------------------------ timebase

def test_unknown_log_time_basis_is_reported_and_never_assumed(fx, example_profile, tmp_path):
    inc = prep(fx / "valid.mcap", example_profile, tmp_path / "inc").incident_dir
    inv = inspect_evidence(inc)
    d = {x.id: x for x in inv.timebase.clock_domains}
    assert inv.timebase.log_time_basis == "unknown" and d["mcap_log_time"].basis == "unknown"
    assert any("unknown" in c for c in inv.timebase.caveats) and any(g.code == "log_time_basis_unknown" for g in inv.gaps)
    assert d["mcap_log_time"].start_ns == mf.BASE_NS  # window is the parsed one, not shifted or rounded


@pytest.mark.parametrize("trigger,state", [
    ("trigger_attested_within.json", "within_recording_window"),
    ("trigger_labels_match_unattested.json", "inconclusive"),
    ("trigger_declared_incompatible.json", "unavailable"),
    ("trigger_unknown_basis.json", "unavailable"),
])
def test_trigger_clock_is_a_separate_domain_with_the_collectors_own_verdict(fx, synth_profile, tmp_path, trigger, state):
    incident = prep(fx / "valid.mcap", synth_profile, tmp_path / "inc", trigger=fx / "triggers" / trigger).incident_dir
    inv = inspect_evidence(incident)
    assert "trigger" in {d.id for d in inv.timebase.clock_domains}
    al = next(a for a in inv.timebase.alignments if a.b == "trigger")
    assert al.state == state and al.reason  # copied from the existing comparison, not re-derived
    assert inv.trigger_comparison is not None and inv.trigger_comparison.result.value == state
    if state != "within_recording_window":
        assert any(g.code == "trigger_comparison_not_established" for g in inv.gaps)


def test_clock_domains_are_never_merged_or_aligned_by_the_inventory(tmp_path):
    sess = make_session(tmp_path)
    prep(sess, None, None)
    inv = inspect_evidence(sess)
    states = {(a.a, a.b): a.state for a in inv.timebase.alignments}
    assert states[("mcap_log_time", "capture_supervisor_wall_clock")] == "not_established"
    assert states[("mcap_log_time", "rosbag2_metadata_time")] == "not_compared"
    assert not any("established" == s or s == "compatible" for s in states.values())


# ------------------------------------------------------------------ read-only, determinism, hygiene

@pytest.mark.parametrize("kind", ["incident", "package", "raw_bag", "raw_mcap", "session_with_incident", "broken", *HOSTILE])
def test_inspection_is_read_only(kind, split_incident, fx, tmp_path):
    target = {
        "incident": split_incident, "package": split_incident / "package", "raw_mcap": fx / "valid.mcap",
        "raw_bag": write_bag(tmp_path / "rb" / "bagrb", three_way_split()),
    }.get(kind)
    if kind in HOSTILE:
        target = build_hostile(kind, split_incident, fx, tmp_path)
    if kind == "session_with_incident":
        (tmp_path / "s").mkdir()
        target = make_session(tmp_path / "s")
        prep(target, None, None)
    if kind == "broken":
        (split_incident / "package" / "recording" / "bagm" / "bagm_0.mcap").write_bytes(b"junk")
        target = split_incident
    before = tree_state(target)
    outside = tmp_path / "outside_area"
    outside_before = tree_state(outside) if outside.exists() else None  # what symlinks point at must not change either
    parent_before = sorted(p.name for p in target.parent.iterdir())
    inspect_evidence(target)
    assert tree_state(target) == before
    if outside_before is not None:
        assert tree_state(outside) == outside_before
    assert sorted(p.name for p in target.parent.iterdir()) == parent_before  # no lock/temp/staging file next to it either


@pytest.fixture
def raw_bag_dir(tmp_path):
    return write_bag(tmp_path / "rawguard" / "bagg", three_way_split())


@pytest.mark.parametrize("which", ["incident", "package", "raw_bag", "raw_mcap"])
def test_inspection_never_attempts_a_write(which, split_incident, fx, raw_bag_dir, write_guard):
    """`write_guard` is requested last on purpose: every fixture above is built before writes are forbidden."""
    target = {"incident": split_incident, "package": split_incident / "package", "raw_bag": raw_bag_dir, "raw_mcap": fx / "valid.mcap"}[which]
    inv = inspect_evidence(target)
    assert inv.inspection.state == "complete" and inv.errors == []


def test_write_guard_actually_detects_writes(tmp_path, write_guard):
    with pytest.raises(AssertionError, match="write attempted"):
        open(tmp_path / "x", "wb")
    with pytest.raises(AssertionError, match="write attempted"):
        (tmp_path / "y").write_text("via pathlib")
    with pytest.raises(AssertionError, match="os.rename attempted"):
        os.rename(tmp_path / "a", tmp_path / "b")


def test_json_is_deterministic_and_location_independent(split_incident, tmp_path):
    a = inventory_json(inspect_evidence(split_incident))
    assert a == inventory_json(inspect_evidence(split_incident))
    moved = tmp_path / "elsewhere" / "inc"
    shutil.copytree(split_incident, moved)
    b = inspect_evidence(moved)
    da, db = json.loads(a), json.loads(inventory_json(b))
    for d in (da, db):
        d["provenance"]["input"].pop("path")
    assert da == db  # only the path as given differs between two copies of the same bytes


def test_no_host_paths_wallclock_or_invented_loss_counts_in_json(split_incident, tmp_path):
    inv = inspect_evidence(split_incident)
    text = inventory_json(inv)
    data = json.loads(text)
    data["provenance"]["input"].pop("path")
    flat = json.dumps(data)
    assert str(tmp_path) not in flat and "/tmp/" not in flat
    assert "inspected_at" not in flat and "inventory_created" not in flat
    assert "dropped_message" not in flat and "lost_message_count" not in flat


def test_report_is_concise_and_states_its_limits(split_incident):
    text = render_inventory(inspect_evidence(split_incident))
    assert "live ROS 2 capture is NOT verified" in text and "VERIFIED NOW" in text and "clock domains are not merged" in text
    assert "no loss claim is permitted" not in text  # no capture health supplied: the loss line only appears with a sidecar
    assert len(text.splitlines()) < 60


# ------------------------------------------------------------------ CLI contract

def test_cli_json_and_exit_codes_and_existing_commands_still_present(split_incident, tmp_path):
    ok = runner.invoke(app, ["evidence", "inventory", str(split_incident), "--json"])
    assert ok.exit_code == 0
    data = json.loads(ok.stdout)
    assert data["inventory_schema_version"] == "1.0" and data["integrity"]["state"] == "verified_now"
    human = runner.invoke(app, ["evidence", "inventory", str(split_incident)])
    assert human.exit_code == 0 and "EVIDENCE INVENTORY" in human.stdout
    (split_incident / "package" / "recording" / "bagm" / "bagm_0.mcap").write_bytes(b"x")
    bad = runner.invoke(app, ["evidence", "inventory", str(split_incident), "--json"])
    assert bad.exit_code == 1 and json.loads(bad.stdout)["integrity"]["state"] == "tampered"
    missing = runner.invoke(app, ["evidence", "inventory", str(tmp_path / "nope"), "--json"])
    assert missing.exit_code == 2 and json.loads(missing.stdout)["errors"][0]["code"] == "input_not_found"
    top = runner.invoke(app, ["--help"])
    for cmd in ("profile", "session", "capture", "package", "incident", "evidence"):
        assert cmd in top.stdout
    assert runner.invoke(app, ["incident", "show", "--help"]).exit_code == 0


# ------------------------------------------------------------------ hostile layouts: refused, not read, not referenced

@pytest.mark.parametrize("kind", HOSTILE)
def test_hostile_layouts_are_refused_with_a_structured_error_and_nothing_outside_is_touched(kind, split_incident, fx, tmp_path, read_spy):
    target = build_hostile(kind, split_incident, fx, tmp_path)
    read_spy.clear()
    inv = inspect_evidence(target)
    outside = [p for p in read_spy if "/outside_area" in p]
    assert not outside, f"opened or listed outside the evidence root: {outside}"
    want = HOSTILE_ERROR[kind]
    if want is not None:
        assert want in codes(inv.errors), (kind, [(e.code, e.message) for e in inv.errors])
    assert exit_code(inv) != 0
    if want is not None:
        assert inv.inspection.state in ("partial", "failed")
    else:  # an integrity finding: the inventory itself completed, but the package is not what its manifest describes
        assert inv.integrity.state == "tampered" and any(a.integrity == "unlisted" for a in inv.artifacts)
    # no reference the inventory emits can be followed out of the evidence root, and no host path leaks into the JSON
    for a in inv.artifacts:
        assert relative_path_problem(a.relative_path) is None and a.ref_id == f"artifact:{a.relative_path}"
    for s in inv.recording.segments:
        assert s.artifact_ref is None or relative_path_problem(s.artifact_ref[len("artifact:"):]) is None
    data = json.loads(inventory_json(inv))
    data["provenance"]["input"].pop("path")
    assert str(tmp_path) not in json.dumps(data) and "secret.txt" not in json.dumps(data)


def test_symlinked_parent_marks_every_artifact_below_it_unsafe_and_keeps_the_rest_verified(split_incident, fx, tmp_path):
    inv = inspect_evidence(build_hostile("symlinked_parent", split_incident, fx, tmp_path))
    unsafe = {a.relative_path for a in inv.artifacts if a.integrity == "unsafe"}
    assert unsafe == {f"package/recording/bagm/{n}" for n in ("bagm_0.mcap", "bagm_1.mcap", "bagm_2.mcap", "metadata.yaml")}
    assert art(inv, "package/profile/synthetic_fixture.profile.yaml").integrity == "verified"
    assert inv.integrity.state == "tampered" and len([e for e in inv.errors if e.code == "unsafe_path"]) == 4
    assert all(a.sha256_basis == "recorded_in_manifest" for a in inv.artifacts if a.integrity == "unsafe")  # the recorded digest, never a recomputed one


def test_package_health_reached_through_a_symlinked_parent_is_not_loaded(split_incident, fx, tmp_path, read_spy):
    target = build_hostile("package_health_symlinked_parent", split_incident, fx, tmp_path)
    read_spy.clear()  # discard the scenario set-up itself
    inv = inspect_evidence(target)
    assert inv.capture_health.source == "manifest_summary_only" and "capture_health_unreadable" in codes(inv.errors)
    assert not [p for p in read_spy if "/outside_area" in p]


def test_unsafe_segment_names_are_shown_but_never_referenced_or_read(split_incident, fx, tmp_path, read_spy):
    for kind in ("unsafe_segment_path_slash", "unsafe_segment_path_backslash"):
        (tmp_path / kind).mkdir()
        target = build_hostile(kind, split_incident, fx, tmp_path / kind)
        read_spy.clear()  # discard the scenario set-up itself
        inv = inspect_evidence(target)
        bad = [s for s in inv.recording.segments if s.state == "unsafe_path"]
        assert len(bad) == 1 and bad[0].artifact_ref is None and "outside_area" in bad[0].relative_path  # displayed as data, no pointer
        assert not any("escape.mcap" in a.relative_path for a in inv.artifacts)
        assert not [p for p in read_spy if p.endswith("escape.mcap")]


@pytest.mark.parametrize("bad", ["../x", "..\\x", "a/..\\b", "C:\\x", "a//b", "/abs", "x/./y"])
def test_a_hand_edited_manifest_with_an_unsafe_path_is_a_structured_failure(split_incident, bad):
    mp = split_incident / "package" / "manifest.json"
    data = json.loads(mp.read_text())
    data["artifacts"][0]["relative_path"] = bad
    mp.write_text(json.dumps(data))
    inv = inspect_evidence(split_incident / "package")
    assert inv.inspection.state == "failed" and "manifest_invalid" in codes(inv.errors) and exit_code(inv) == 2
    assert inv.integrity.state == "unverifiable" and inv.integrity.checked_now is False  # nothing was re-hashed against an unusable manifest
    assert all(relative_path_problem(a.relative_path) is None for a in inv.artifacts)


def test_artifact_ref_model_itself_refuses_an_unsafe_path():
    from physicore_collector.inventory import ArtifactRef
    with pytest.raises(Exception, match="unsafe artifact reference"):
        ArtifactRef(ref_id="artifact:../x", relative_path="../x", kind="x", supported=False, sha256_basis="not_available", integrity="unlisted")


@pytest.mark.parametrize("kind", HOSTILE)
def test_hostile_layouts_never_attempt_a_write_rename_or_lock(kind, split_incident, fx, tmp_path, monkeypatch):
    target = build_hostile(kind, split_incident, fx, tmp_path)  # built BEFORE writes are forbidden
    forbid_writes(monkeypatch)
    inv = inspect_evidence(target)
    assert isinstance(inv, EvidenceInventory)  # no AssertionError from the guard (it would surface as a test failure)


@pytest.mark.parametrize("kind", ["incident", "package", "raw_bag", "raw_mcap"])
def test_inspection_takes_no_lock(kind, split_incident, fx, raw_bag_dir, monkeypatch):
    target = {"incident": split_incident, "package": split_incident / "package", "raw_bag": raw_bag_dir, "raw_mcap": fx / "valid.mcap"}[kind]
    calls = []
    monkeypatch.setattr(fcntl, "flock", lambda *a, **k: calls.append("flock"))
    monkeypatch.setattr(fcntl, "lockf", lambda *a, **k: calls.append("lockf"))
    inspect_evidence(target)
    assert calls == []


def test_a_symlink_inside_a_raw_bag_that_leaves_the_bag_is_still_refused(tmp_path, read_spy):
    outside = tmp_path / "outside_area"
    outside.mkdir()
    bag = write_bag(tmp_path / "rawl" / "bagl", three_way_split())
    shutil.move(str(bag / "bagl_1.mcap"), str(outside / "bagl_1.mcap"))
    (bag / "bagl_1.mcap").symlink_to(outside / "bagl_1.mcap")
    read_spy.clear()
    inv = inspect_evidence(bag)
    seg = {s.relative_path: s for s in inv.recording.segments}
    assert seg["bagl_1.mcap"].state == "unsafe_path" and seg["bagl_1.mcap"].observed_message_count is None
    assert not [p for p in read_spy if p.endswith("bagl_1.mcap") and "/outside_area" in p]


# ------------------------------------------------------------------ inaccessible and unusual inputs: results, not tracebacks

def _deny(monkeypatch, target_suffix: str, fn_name: str):
    """Make `os.<fn_name>` raise PermissionError for paths ending with `target_suffix` (real chmod is meaningless as root)."""
    real = getattr(os, fn_name)

    def fake(path, *a, **k):
        if str(path).endswith(target_suffix):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(os, fn_name, fake)


def test_permission_denied_on_the_input_is_a_structured_result(tmp_path, monkeypatch):
    d = tmp_path / "locked"
    d.mkdir()
    _deny(monkeypatch, "locked", "lstat")
    inv = inspect_evidence(d)
    assert inv.inspection.state == "failed" and "permission_denied" in codes(inv.errors) and exit_code(inv) == 2
    assert classify_input(d) == "unsupported"  # the public classifier never raises either


def test_permission_denied_while_classifying_a_directory_is_a_structured_result(tmp_path, monkeypatch):
    d = tmp_path / "plain_dir"
    d.mkdir()
    (d / "note.txt").write_text("x")  # no manifest, no metadata: classification has to LIST the directory
    real = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p=".": (_ for _ in ()).throw(PermissionError(13, "Permission denied", str(p))) if str(p).endswith("plain_dir") else real(p))
    inv = inspect_evidence(d)
    assert inv.inspection.state == "failed" and "permission_denied" in codes(inv.errors)
    assert classify_input(d) == "unsupported"


def test_unlistable_subdirectory_is_reported_not_silently_skipped(split_incident, monkeypatch):
    real_walk = os.walk

    def walk(top, topdown=True, onerror=None, followlinks=False):
        for item in real_walk(top, topdown, onerror, followlinks):
            yield item
        if onerror and str(top).endswith("package"):
            onerror(PermissionError(13, "Permission denied", str(Path(top) / "recording")))

    monkeypatch.setattr(os, "walk", walk)
    inv = inspect_evidence(split_incident)
    errs = [e for e in inv.errors if e.code == "directory_unreadable"]
    assert errs and "package/recording" in errs[0].message and inv.inspection.state == "partial" and exit_code(inv) == 1


def test_integrity_check_that_cannot_complete_is_unverifiable_not_a_traceback(split_incident, monkeypatch):
    import physicore_collector.inventory as inv_mod

    def boom(_pkg):
        raise PermissionError(13, "Permission denied", "recording/x.mcap")

    monkeypatch.setattr(inv_mod, "verify_package", boom)
    inv = inspect_evidence(split_incident / "package")
    assert inv.integrity.state == "unverifiable" and inv.integrity.checked_now is False
    assert "integrity_check_failed" in codes(inv.errors) and inv.inspection.state == "partial" and exit_code(inv) == 1


def test_unreadable_unlisted_file_is_a_structured_error(split_incident, monkeypatch):
    (split_incident / "package" / "extra.bin").write_bytes(b"x")
    import physicore_collector.inventory as inv_mod
    real = inv_mod.sha256_file
    monkeypatch.setattr(inv_mod, "sha256_file", lambda p, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied")) if str(p).endswith("extra.bin") else real(p, **k))
    inv = inspect_evidence(split_incident)
    assert any(e.code == "artifact_unreadable" and e.artifact_ref == "artifact:package/extra.bin" for e in inv.errors)
    assert art(inv, "package/extra.bin").sha256 is None and art(inv, "package/extra.bin").sha256_basis == "not_available"


def test_unreadable_single_mcap_and_unlistable_parent(fx, tmp_path, monkeypatch):
    import physicore_collector.inventory as inv_mod
    f = tmp_path / "one.mcap"
    shutil.copy(fx / "valid.mcap", f)
    real = inv_mod.sha256_file
    monkeypatch.setattr(inv_mod, "sha256_file", lambda p, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied")) if str(p).endswith("one.mcap") else real(p, **k))
    inv = inspect_evidence(f)
    assert inv.inspection.state == "failed" and "permission_denied" in codes(inv.errors) and exit_code(inv) == 2
    monkeypatch.setattr(inv_mod, "sha256_file", real)
    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(PermissionError(13, "Permission denied")) if self == tmp_path else real_iterdir(self))
    inv2 = inspect_evidence(f)  # the file is readable; only its directory cannot be listed
    assert inv2.recording.total_message_count == 123 and "siblings_not_listed" in codes(inv2.warnings)


def test_a_file_that_vanishes_mid_inspection_is_a_structured_result(split_incident, monkeypatch):
    import physicore_collector.inventory as inv_mod

    def gone(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "recording/x.mcap")

    monkeypatch.setattr(inv_mod, "verify_package", gone)
    inv = inspect_evidence(split_incident / "package")
    assert "integrity_check_failed" in codes(inv.errors) and inv.inspection.state == "partial"


def test_odd_input_paths_are_results_not_exceptions(tmp_path):
    cases = {
        "a\x00b": "input_invalid_path",
        "x" * 5000: "path_too_long",
        str(tmp_path / ("y" * 300)): "input_not_found",
    }
    for p, code in cases.items():
        inv = inspect_evidence(p)
        assert inv.inspection.state == "failed" and exit_code(inv) == 2, p[:20]
        assert code in codes(inv.errors) or "path_too_long" in codes(inv.errors), (p[:20], [e.code for e in inv.errors])
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    assert "input_is_symlink" in codes(inspect_evidence(loop).errors)
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "nowhere")
    assert "input_is_symlink" in codes(inspect_evidence(dangling).errors)
    fifo = tmp_path / "fifo.mcap"
    os.mkfifo(fifo)
    r = inspect_evidence(fifo)  # a FIFO named like an mcap must be classified without being opened (opening would block)
    assert r.inspection.state == "failed" and exit_code(r) == 2
    assert inspect_evidence(None.__class__.__name__).inspection.state == "failed"  # str(NoneType): just a nonexistent name


def test_cli_reports_inaccessible_input_as_json_with_exit_code_2(tmp_path, monkeypatch):
    d = tmp_path / "locked"
    d.mkdir()
    _deny(monkeypatch, "locked", "lstat")
    res = runner.invoke(app, ["evidence", "inventory", str(d), "--json"])
    assert res.exit_code == 2 and res.exception is None or isinstance(res.exception, SystemExit)
    data = json.loads(res.stdout)
    assert data["inspection"]["state"] == "failed" and data["errors"][0]["code"] == "permission_denied"
    human = runner.invoke(app, ["evidence", "inventory", str(d)])
    assert human.exit_code == 2 and "permission_denied" in human.stdout and "Traceback" not in human.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions; the same cases are simulated above and were also run for real as user 'nobody' (see report)")
def test_real_permission_errors_as_an_unprivileged_user(split_incident, tmp_path):
    locked = tmp_path / "bagk"
    shutil.copytree(split_incident / "package" / "recording" / "bagm", locked)
    os.chmod(locked / "bagm_1.mcap", 0)
    try:
        inv = inspect_evidence(locked)
        assert {s.relative_path: s.state for s in inv.recording.segments}["bagm_1.mcap"] == "unreadable" and exit_code(inv) == 1
        os.chmod(locked, 0)
        assert "permission_denied" in codes(inspect_evidence(locked).errors)
    finally:
        os.chmod(locked, 0o755)
        os.chmod(locked / "bagm_1.mcap", 0o644)
