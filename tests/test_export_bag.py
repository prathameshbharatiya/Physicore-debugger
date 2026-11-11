"""Packaging of rosbag2 directories + backward compatibility with Day 1 packages."""

import json
import os
import shutil
from pathlib import Path

import pytest

from physicore_collector import export as export_mod
from physicore_collector.errors import ExportTargetExistsError
from physicore_collector.export import export_package, verify_package
from physicore_collector.schemas import Manifest, SessionStatus
from physicore_collector.session import analyze_session
from tests.bagtools import three_way_split, write_bag

DAY1 = Path(__file__).parent / "compat" / "day1_package"


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_split_bag_export_preserves_every_byte_and_layout(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "rec" / "mybag", three_way_split())
    src_before = tree(bag)
    ctx = analyze_session(bag, synth_profile)
    res = export_package(ctx, tmp_path / "pkg")
    assert res.status is SessionStatus.COMPLETE
    pkg = tmp_path / "pkg"
    for name in ("metadata.yaml", "mybag_0.mcap", "mybag_1.mcap", "mybag_2.mcap"):
        assert (pkg / "recording" / "mybag" / name).read_bytes() == src_before[name]
    assert tree(bag) == src_before  # source untouched
    assert verify_package(pkg) == []
    m = res.manifest
    assert m.manifest_schema_version == "0.2" and m.recording_kind == "rosbag2_directory"
    assert [s.relative_path for s in m.bag.segments] == ["mybag_0.mcap", "mybag_1.mcap", "mybag_2.mcap"]
    # exported package is itself a valid bag directory that re-analyzes identically
    again = analyze_session(pkg / "recording" / "mybag", synth_profile)
    assert again.report.status is SessionStatus.COMPLETE and again.recording_sha256 == ctx.recording_sha256


def test_partial_bag_is_exported_with_true_status_and_explicit_gaps(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b", three_way_split())
    (bag / "b_1.mcap").unlink()
    ctx = analyze_session(bag, synth_profile)
    res = export_package(ctx, tmp_path / "pkg")
    assert res.status is SessionStatus.PARTIAL
    m = res.manifest
    assert {"bag_segments_not_fully_inspected"} <= {g.code for g in m.known_gaps}
    miss = next(s for s in m.bag.segments if s.relative_path == "b_1.mcap")
    assert miss.state.value == "missing" and miss.sha256 is None
    assert not (tmp_path / "pkg" / "recording" / "b" / "b_1.mcap").exists()  # nothing fabricated
    assert verify_package(tmp_path / "pkg") == []


def test_unaccounted_files_are_reported_not_copied(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b")
    (bag / "NOTES.txt").write_text("x")
    res = export_package(analyze_session(bag, synth_profile), tmp_path / "pkg")
    assert not (tmp_path / "pkg" / "recording" / "b" / "NOTES.txt").exists()
    assert [u.name for u in res.manifest.bag.unaccounted] == ["NOTES.txt"]
    assert "bag_unaccounted_entries" in {g.code for g in res.manifest.known_gaps}


def test_malformed_metadata_is_preserved_byte_identically(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b", with_metadata=False)
    (bag / "metadata.yaml").write_text("::: broken [")
    res = export_package(analyze_session(bag, synth_profile), tmp_path / "pkg")
    assert (tmp_path / "pkg" / "recording" / "b" / "metadata.yaml").read_text() == "::: broken ["
    assert res.status is SessionStatus.PARTIAL
    assert "bag_metadata_unusable" in {g.code for g in res.manifest.known_gaps}


def test_unsupported_storage_export_is_failed_but_preserved(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "sql", storage="sqlite3")
    res = export_package(analyze_session(bag, synth_profile), tmp_path / "pkg")
    assert res.status is SessionStatus.FAILED
    assert (tmp_path / "pkg" / "recording" / "sql" / "sql_0.db3").is_file()


def test_export_refuses_existing_target_for_bags(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b")
    ctx = analyze_session(bag, synth_profile)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "keep").write_text("k")
    with pytest.raises(ExportTargetExistsError):
        export_package(ctx, tmp_path / "pkg")
    assert (tmp_path / "pkg" / "keep").read_text() == "k"


def test_segment_changed_between_analysis_and_export_aborts_and_cleans_up(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b", three_way_split())
    ctx = analyze_session(bag, synth_profile)
    seg = bag / "b_1.mcap"
    seg.write_bytes(seg.read_bytes() + b"\x00")  # changes after the analysis-time hash
    from physicore_collector.errors import ChecksumMismatchError
    with pytest.raises(ChecksumMismatchError, match="b_1.mcap"):
        export_package(ctx, tmp_path / "pkg")
    assert not (tmp_path / "pkg").exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".pkg")] == []  # staging removed
    assert seg.read_bytes().endswith(b"\x00")  # source evidence untouched by cleanup


def test_failure_during_copy_removes_staging_but_never_source(tmp_path, synth_profile, monkeypatch):
    bag = write_bag(tmp_path / "b", three_way_split())
    before = tree(bag)
    ctx = analyze_session(bag, synth_profile)
    calls = {"n": 0}
    real = export_mod.copy_and_hash
    def flaky(src, dst, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(28, "No space left on device")
        return real(src, dst, **kw)
    monkeypatch.setattr(export_mod, "copy_and_hash", flaky)
    from physicore_collector.errors import ExportError
    with pytest.raises(ExportError, match="No space left"):
        export_package(ctx, tmp_path / "pkg")
    assert not (tmp_path / "pkg").exists() and not [p for p in tmp_path.iterdir() if p.name.startswith(".pkg")]
    assert tree(bag) == before


# ---------------------------------------------------------------- verification


def test_verify_detects_tampering_missing_extra_and_symlinks(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b", three_way_split())
    export_package(analyze_session(bag, synth_profile), tmp_path / "pkg")
    pkg = tmp_path / "pkg"
    assert verify_package(pkg) == []
    seg = pkg / "recording" / "b" / "b_0.mcap"
    orig = seg.read_bytes()
    seg.write_bytes(orig[:-1] + bytes([orig[-1] ^ 1]))
    assert any("b_0.mcap" in p and "sha256" in p for p in verify_package(pkg))
    seg.write_bytes(orig)
    (pkg / "recording" / "b" / "b_2.mcap").unlink()
    assert any("b_2.mcap: file is missing" in p for p in verify_package(pkg))
    shutil.copy(bag / "b_2.mcap", pkg / "recording" / "b" / "b_2.mcap")
    (pkg / "extra.txt").write_text("x")
    assert any("extra.txt: file is not listed" in p for p in verify_package(pkg))
    (pkg / "extra.txt").unlink()
    (pkg / "recording" / "b" / "b_2.mcap").unlink()
    os.symlink(bag / "b_2.mcap", pkg / "recording" / "b" / "b_2.mcap")
    assert any("symlink" in p for p in verify_package(pkg))


def test_verify_detects_manifest_bag_section_disagreeing_with_artifacts(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b")
    export_package(analyze_session(bag, synth_profile), tmp_path / "pkg")
    mp = tmp_path / "pkg" / "manifest.json"
    d = json.loads(mp.read_text())
    d["bag"]["segments"][0]["sha256"] = "0" * 64
    mp.write_text(json.dumps(d))
    assert any("differs from the bag section" in p for p in verify_package(tmp_path / "pkg"))


def test_verify_rejects_unknown_manifest_version(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b")
    export_package(analyze_session(bag, synth_profile), tmp_path / "pkg")
    mp = tmp_path / "pkg" / "manifest.json"
    d = json.loads(mp.read_text()); d["manifest_schema_version"] = "9.9"
    mp.write_text(json.dumps(d))
    assert any("unsupported manifest_schema_version" in p for p in verify_package(tmp_path / "pkg"))


# ---------------------------------------------------------------- backward compatibility (Day 1)


def test_day1_package_still_verifies_and_loads():
    assert verify_package(DAY1) == []
    m = Manifest.model_validate_json((DAY1 / "manifest.json").read_bytes())
    assert m.manifest_schema_version == "0.1" and m.bag is None and m.recording_kind is None


def test_single_mcap_export_has_exactly_the_day1_shape(fx, synth_profile, tmp_path):
    from datetime import datetime, timezone
    ctx = analyze_session(fx / "valid.mcap", synth_profile, metadata_path=fx / "metadata.json")
    export_package(ctx, tmp_path / "pkg", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    new = json.loads((tmp_path / "pkg" / "manifest.json").read_text())
    old = json.loads((DAY1 / "manifest.json").read_text())
    assert set(new) == set(old)  # no Day 2 keys leak into a single-MCAP manifest
    assert new["manifest_schema_version"] == "0.1"
    assert {a["role"] for a in new["artifacts"]} == {a["role"] for a in old["artifacts"]}
    assert [(a["relative_path"], a["sha256"]) for a in new["artifacts"] if a["role"] != "validation_report"] == \
           [(a["relative_path"], a["sha256"]) for a in old["artifacts"] if a["role"] != "validation_report"]
    rep = json.loads((tmp_path / "pkg" / "validation_report.json").read_text())
    assert rep["report_schema_version"] == "0.1"
    # the validation report and the manifest are byte-identical to what Day 1 wrote (same inputs, same time)
    assert (tmp_path / "pkg" / "validation_report.json").read_bytes() == (DAY1 / "validation_report.json").read_bytes()
    assert (tmp_path / "pkg" / "recording" / "valid.mcap").read_bytes() == (DAY1 / "recording" / "valid.mcap").read_bytes()
