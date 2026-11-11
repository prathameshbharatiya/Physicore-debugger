import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import physicore_collector.export as ex
from physicore_collector.errors import ChecksumMismatchError, ExportError, ExportTargetExistsError, InputError
from physicore_collector.export import ensure_target_available, export_package, verify_package
from physicore_collector.schemas import ArtifactRole, Manifest, SessionStatus as S
from physicore_collector.session import analyze_session

NOW = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def ctx_for(fx, profile, name="valid.mcap", **kw):
    return analyze_session(fx / name, profile, **kw)


def read_manifest(pkg):
    return Manifest.model_validate_json((pkg / "manifest.json").read_text())


def leftovers(parent):
    return [p.name for p in parent.iterdir() if ".staging-" in p.name]


# ---- happy path ----
def test_export_layout_and_byte_identical_recording(fx, synth_profile, tmp_path):
    ctx = ctx_for(fx, synth_profile, trigger_path=fx / "triggers/trigger_attested_within.json", metadata_path=fx / "metadata.json")
    res = export_package(ctx, tmp_path / "pkg", now=NOW)
    pkg = res.package_dir
    files = sorted(p.relative_to(pkg).as_posix() for p in pkg.rglob("*") if p.is_file())
    assert files == ["manifest.json", "metadata/metadata.json", "profile/synthetic_fixture.profile.yaml",
                     "recording/valid.mcap", "trigger/trigger_attested_within.json", "validation_report.json"]
    # the original recording, byte for byte, and the same SHA-256 as the source
    assert (pkg / "recording/valid.mcap").read_bytes() == (fx / "valid.mcap").read_bytes()
    m = read_manifest(pkg)
    rec = next(a for a in m.artifacts if a.role is ArtifactRole.RECORDING)
    assert rec.sha256 == sha(fx / "valid.mcap") == ctx.recording_sha256
    assert rec.size_bytes == (fx / "valid.mcap").stat().st_size
    assert verify_package(pkg) == []
    assert leftovers(tmp_path) == []
    assert res.status is S.COMPLETE


def test_supplied_files_preserved_exactly(fx, synth_profile, tmp_path):
    ctx = ctx_for(fx, synth_profile, trigger_path=fx / "triggers/trigger_attested_within.json", metadata_path=fx / "metadata.json")
    pkg = export_package(ctx, tmp_path / "pkg", now=NOW).package_dir
    assert (pkg / "metadata/metadata.json").read_bytes() == (fx / "metadata.json").read_bytes()
    assert (pkg / "trigger/trigger_attested_within.json").read_bytes() == (fx / "triggers/trigger_attested_within.json").read_bytes()
    assert (pkg / "profile/synthetic_fixture.profile.yaml").read_bytes() == Path(synth_profile).read_bytes()


def test_manifest_contents(fx, synth_profile, tmp_path):
    ctx = ctx_for(fx, synth_profile, trigger_path=fx / "triggers/trigger_unknown_basis.json", metadata_path=fx / "metadata.json")
    pkg = export_package(ctx, tmp_path / "pkg", now=NOW).package_dir
    m = read_manifest(pkg)
    raw = json.loads((pkg / "manifest.json").read_text())
    assert m.manifest_schema_version == "0.1" and m.tool.name == "physicore-collector" and m.tool.version == "0.1.0"
    assert m.created_at_utc == "2030-01-02T03:04:05Z"
    assert m.profile.profile_id == "synthetic-fixture-v1" and m.profile_sha256 == sha(synth_profile)
    assert m.recording_window.min_log_time_ns == 1_700_000_000_000_000_000
    assert m.recording_window.max_log_time_ns == 1_700_000_002_000_000_000
    assert {t.topic for t in m.topics} == {"/joint_states", "/camera/image", "/diagnostics"}
    assert m.trigger.timestamp_basis.value == "unknown"
    assert m.validation.trigger_comparison.result.value == "unavailable"
    assert m.session_status is S.PARTIAL == m.validation.status
    assert m.metadata["supplied"]["operator"] == "alice"
    assert m.metadata["mcap_records"][0]["name"] == "session"
    assert m.known_gaps and m.loss_detection_coverage.no_loss_claim_permitted is False
    assert raw["loss_detection_coverage"]["no_loss_claim_permitted"] is False


def test_manifest_does_not_contain_its_own_checksum(fx, synth_profile, tmp_path):
    pkg = export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW).package_dir
    m = read_manifest(pkg)
    assert "manifest.json" not in [a.relative_path for a in m.artifacts]
    assert sha(pkg / "manifest.json") not in (pkg / "manifest.json").read_text()


def test_validation_report_artifact_matches_manifest_and_report(fx, synth_profile, tmp_path):
    ctx = ctx_for(fx, synth_profile, "cadence_gap.mcap")
    pkg = export_package(ctx, tmp_path / "pkg", now=NOW).package_dir
    rep = json.loads((pkg / "validation_report.json").read_text())
    assert rep["status"] == "partial" and len(rep["cadence_findings"]) == 1


def test_export_is_deterministic_for_fixed_time(fx, synth_profile, tmp_path):
    a = export_package(ctx_for(fx, synth_profile), tmp_path / "a", now=NOW).package_dir
    b = export_package(ctx_for(fx, synth_profile), tmp_path / "b", now=NOW).package_dir
    for rel in ["manifest.json", "validation_report.json", "recording/valid.mcap"]:
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


@pytest.mark.parametrize("name,status", [("truncated.mcap", S.PARTIAL), ("not_mcap.mcap", S.FAILED), ("cadence_gap.mcap", S.PARTIAL)])
def test_non_complete_sessions_export_with_true_status_and_exact_bytes(fx, synth_profile, tmp_path, name, status):
    res = export_package(ctx_for(fx, synth_profile, name), tmp_path / "pkg", now=NOW)
    assert res.status is status and read_manifest(res.package_dir).session_status is status
    assert (res.package_dir / "recording" / name).read_bytes() == (fx / name).read_bytes()
    assert verify_package(res.package_dir) == []


def test_failed_package_has_no_window_and_notes_incompleteness(fx, synth_profile, tmp_path):
    m = read_manifest(export_package(ctx_for(fx, synth_profile, "not_mcap.mcap"), tmp_path / "p", now=NOW).package_dir)
    assert m.recording_window is None and "recording_incomplete" in {g.code for g in m.known_gaps}


def test_default_timestamp_is_utc_iso(fx, synth_profile, tmp_path):
    m = read_manifest(export_package(ctx_for(fx, synth_profile), tmp_path / "p").package_dir)
    datetime.strptime(m.created_at_utc, "%Y-%m-%dT%H:%M:%SZ")


# ---- overwrite refusal ----
def test_refuses_existing_directory_and_leaves_it_untouched(fx, synth_profile, tmp_path):
    target = tmp_path / "pkg"
    target.mkdir()
    (target / "precious.txt").write_text("keep me")
    with pytest.raises(ExportTargetExistsError, match="never overwrites"):
        export_package(ctx_for(fx, synth_profile), target, now=NOW)
    assert [p.name for p in target.iterdir()] == ["precious.txt"] and (target / "precious.txt").read_text() == "keep me"
    assert leftovers(tmp_path) == []


def test_refuses_existing_empty_directory(fx, synth_profile, tmp_path):
    (tmp_path / "pkg").mkdir()
    with pytest.raises(ExportTargetExistsError):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)


def test_refuses_existing_file(fx, synth_profile, tmp_path):
    (tmp_path / "pkg").write_text("x")
    with pytest.raises(ExportTargetExistsError):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert (tmp_path / "pkg").read_text() == "x"


def test_refuses_dangling_symlink(fx, synth_profile, tmp_path):
    os.symlink(tmp_path / "nowhere", tmp_path / "pkg")
    with pytest.raises(ExportTargetExistsError):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert os.path.islink(tmp_path / "pkg") and not (tmp_path / "nowhere").exists()


def test_second_export_to_same_target_is_refused(fx, synth_profile, tmp_path):
    export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    before = sha(tmp_path / "pkg/manifest.json")
    with pytest.raises(ExportTargetExistsError):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert sha(tmp_path / "pkg/manifest.json") == before


def test_target_appearing_during_export_is_not_overwritten_and_staging_cleaned(fx, synth_profile, tmp_path, monkeypatch):
    target = tmp_path / "pkg"
    real_build = ex._build_in_staging

    def build_then_race(*a, **k):
        m = real_build(*a, **k)
        target.mkdir()
        (target / "other-process.txt").write_text("theirs")
        return m

    monkeypatch.setattr(ex, "_build_in_staging", build_then_race)
    with pytest.raises(ExportTargetExistsError, match="appeared while exporting"):
        export_package(ctx_for(fx, synth_profile), target, now=NOW)
    assert (target / "other-process.txt").read_text() == "theirs" and leftovers(tmp_path) == []


def test_rename_collision_with_nonempty_dir_is_mapped_to_target_exists(fx, synth_profile, tmp_path, monkeypatch):
    real_rename = os.rename

    def sneaky(src, dst):
        os.mkdir(dst)
        Path(dst, "f").write_text("x")
        return real_rename(src, dst)

    monkeypatch.setattr(ex.os, "rename", sneaky)
    # lexists check passes (target absent) -> rename hits ENOTEMPTY -> mapped
    monkeypatch.setattr(ex.os.path, "lexists", lambda p: False if Path(p).name == "pkg" else os.path.exists(p))
    with pytest.raises(ExportTargetExistsError):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert Path(tmp_path / "pkg/f").read_text() == "x" and leftovers(tmp_path) == []


def test_missing_parent_directory_is_input_error(fx, synth_profile, tmp_path):
    with pytest.raises(InputError, match="Parent directory"):
        ensure_target_available(tmp_path / "no_such_dir" / "pkg")
    assert not (tmp_path / "no_such_dir").exists()  # export never creates parents


# ---- failure injection: no partial package, staging removed ----
def _fail_after_n_writes(monkeypatch, n):
    calls = {"i": 0}
    real = ex._write_new

    def flaky(path, data):
        calls["i"] += 1
        if calls["i"] > n:
            raise OSError(28, "No space left on device")
        return real(path, data)

    monkeypatch.setattr(ex, "_write_new", flaky)


@pytest.mark.parametrize("n", [0, 1, 2])
def test_failure_midway_leaves_no_target_and_no_staging(fx, synth_profile, tmp_path, monkeypatch, n):
    ctx = ctx_for(fx, synth_profile, metadata_path=fx / "metadata.json")
    _fail_after_n_writes(monkeypatch, n)
    with pytest.raises(ExportError, match="No space left on device") as ei:
        export_package(ctx, tmp_path / "pkg", now=NOW)
    assert isinstance(ei.value.__cause__, OSError)  # original failure chained
    assert not (tmp_path / "pkg").exists() and leftovers(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_failure_during_recording_copy_cleans_up(fx, synth_profile, tmp_path, monkeypatch):
    def broken_copy(src, dst, **k):
        Path(dst).write_bytes(b"partial")
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(ex, "copy_and_hash", broken_copy)
    with pytest.raises(ExportError, match="Input/output error"):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_keyboard_interrupt_cleans_up_too(fx, synth_profile, tmp_path, monkeypatch):
    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(ex, "_write_new", interrupt)
    with pytest.raises(KeyboardInterrupt):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_rename_failure_is_reported_and_cleaned(fx, synth_profile, tmp_path, monkeypatch):
    def deny(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ex.os, "rename", deny)
    with pytest.raises(ExportError, match="Could not publish"):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_cleanup_failure_is_reported_not_swallowed(fx, synth_profile, tmp_path, monkeypatch):
    _fail_after_n_writes(monkeypatch, 1)

    def cannot_remove(path, *a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(ex.shutil, "rmtree", cannot_remove)
    with pytest.raises(ExportError, match="staging directory .* could not be removed") as ei:
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert isinstance(ei.value.__cause__, OSError)  # original failure is chained
    assert not (tmp_path / "pkg").exists()


# ---- checksum verification ----
def test_recording_changed_after_analysis_aborts_export(fx, synth_profile, tmp_path):
    src = tmp_path / "rec.mcap"
    src.write_bytes((fx / "valid.mcap").read_bytes())
    ctx = analyze_session(src, synth_profile)
    src.write_bytes(src.read_bytes() + b"\x00")  # file changes while we work
    with pytest.raises(ChecksumMismatchError, match="mismatch"):
        export_package(ctx, tmp_path / "pkg", now=NOW)
    assert not (tmp_path / "pkg").exists() and leftovers(tmp_path) == []


def test_staged_copy_hash_mismatch_aborts(fx, synth_profile, tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "sha256_file", lambda p, **k: "0" * 64)
    with pytest.raises(ChecksumMismatchError):
        export_package(ctx_for(fx, synth_profile), tmp_path / "pkg", now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_profile_changed_during_export_aborts(fx, synth_profile, tmp_path):
    prof = tmp_path / "p.yaml"
    prof.write_bytes(Path(synth_profile).read_bytes())
    ctx = analyze_session(fx / "valid.mcap", prof)
    prof.write_bytes(prof.read_bytes() + b"# changed\n")
    with pytest.raises(ChecksumMismatchError, match="Profile file changed"):
        export_package(ctx, tmp_path / "pkg", now=NOW)
    assert not (tmp_path / "pkg").exists()


def test_source_file_is_never_modified_by_export(fx, synth_profile, tmp_path):
    src = tmp_path / "rec.mcap"
    src.write_bytes((fx / "valid.mcap").read_bytes())
    before = (sha(src), src.stat().st_mtime_ns)
    export_package(analyze_session(src, synth_profile), tmp_path / "pkg", now=NOW)
    assert (sha(src), src.stat().st_mtime_ns) == before


# ---- verify_package detects tampering ----
def make_pkg(fx, synth_profile, tmp_path):
    return export_package(ctx_for(fx, synth_profile, metadata_path=fx / "metadata.json"), tmp_path / "pkg", now=NOW).package_dir


def test_verify_detects_modified_recording(fx, synth_profile, tmp_path):
    pkg = make_pkg(fx, synth_profile, tmp_path)
    f = pkg / "recording/valid.mcap"
    b = bytearray(f.read_bytes())
    b[100] ^= 1
    f.write_bytes(bytes(b))
    assert any("recording/valid.mcap: sha256" in p for p in verify_package(pkg))


def test_verify_detects_missing_extra_and_resized(fx, synth_profile, tmp_path):
    pkg = make_pkg(fx, synth_profile, tmp_path)
    (pkg / "metadata/metadata.json").unlink()
    (pkg / "stray.txt").write_text("x")
    (pkg / "validation_report.json").write_text("{}")
    problems = " | ".join(verify_package(pkg))
    assert "metadata/metadata.json: file is missing" in problems
    assert "stray.txt: file is not listed" in problems
    assert "validation_report.json: size" in problems


def test_verify_detects_missing_or_invalid_manifest(fx, synth_profile, tmp_path):
    pkg = make_pkg(fx, synth_profile, tmp_path)
    (pkg / "manifest.json").write_text("{not json")
    assert "manifest.json is invalid" in verify_package(pkg)[0]
    (pkg / "manifest.json").unlink()
    assert verify_package(pkg) == ["manifest.json is missing"]


def test_verify_detects_tampered_profile_digest(fx, synth_profile, tmp_path):
    pkg = make_pkg(fx, synth_profile, tmp_path)
    m = json.loads((pkg / "manifest.json").read_text())
    m["profile_sha256"] = "1" * 64
    (pkg / "manifest.json").write_text(json.dumps(m))
    assert any("profile_sha256" in p for p in verify_package(pkg))


def test_package_directory_is_world_readable_not_0700(fx, synth_profile, tmp_path):
    pkg = make_pkg(fx, synth_profile, tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert (pkg.stat().st_mode & 0o777) == (plain.stat().st_mode & 0o777)  # same as a normal mkdir (umask-governed)


def test_empty_directory_appearing_during_export_is_not_silently_replaced(fx, synth_profile, tmp_path, monkeypatch):
    """POSIX rename() would replace an EMPTY directory; the pre-publish re-check must stop that."""
    target = tmp_path / "pkg"
    real_build = ex._build_in_staging

    def build_then_race(*a, **k):
        m = real_build(*a, **k)
        target.mkdir()  # someone else creates an empty directory at the target
        return m

    monkeypatch.setattr(ex, "_build_in_staging", build_then_race)
    with pytest.raises(ExportTargetExistsError, match="appeared while exporting"):
        export_package(ctx_for(fx, synth_profile), target, now=NOW)
    assert target.is_dir() and list(target.iterdir()) == []  # untouched: no package written into it
    assert leftovers(tmp_path) == []
