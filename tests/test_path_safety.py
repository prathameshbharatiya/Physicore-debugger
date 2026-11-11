"""Path safety of manifest entries and of package verification (Collector level, shared by `package verify`,
`incident show --verify` and the evidence inventory).

Two things are covered:
  1. a manifest path must be safe on POSIX AND Windows (no traversal through `/` or `\\`, no drive/UNC/absolute forms);
  2. verification must never read a package file through a symlinked PARENT directory, not only a symlinked final file.

Windows behaviour is checked with the pure-path `ntpath` / `PureWindowsPath` rules, which need no Windows host. What is NOT
shown here: behaviour on a real Windows filesystem (reserved device names, NTFS streams, 8.3 names)."""

import builtins
import io
import json
import ntpath
import os
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from pydantic import ValidationError

from physicore_collector.errors import ExportError
from physicore_collector.export import export_package, first_symlink_component, verify_package
from physicore_collector.schemas import ArtifactEntry, ArtifactRole, Manifest, relative_path_problem
from physicore_collector.session import analyze_session
from tests.bagtools import three_way_split, write_bag
from tests.test_incident import NOW

SHA = "0" * 64
COMPAT = Path(__file__).parent / "compat"


def entry(path: str) -> ArtifactEntry:
    return ArtifactEntry(relative_path=path, role=ArtifactRole.RECORDING, size_bytes=1, sha256=SHA)


# ------------------------------------------------------------------ manifest path validation

TRAVERSAL = [
    "../x", "a/../../x", "a/../b", "..", "a/..",                       # forward-slash traversal
    "..\\x", "a\\..\\..\\x", "a/..\\..\\x", "..\\..\\Windows\\win.ini", "a\\b",  # backslash traversal / separator mix
    "C:\\x", "C:/x", "C:x", "c:", "x:y/z", "x/C:/y", "recording/D:\\y",   # drive-qualified
    "\\\\server\\share\\x", "//server/share/x", "\\x", "/abs", "/",       # UNC / rooted / absolute
    "a//b", "a/", "/a", "./a", "a/./b", ".",                              # empty and current-directory components
    "a/.. /b", "a/... /b", "a/. /b", " ../x",                            # Windows strips trailing dots/spaces: these mean '..'
    "a\x00b", "a\nb", "a\tb", "a\x7fb", "",                               # control characters, NUL, empty
]


@pytest.mark.parametrize("path", TRAVERSAL)
def test_unsafe_manifest_paths_are_rejected(path):
    assert relative_path_problem(path) is not None
    with pytest.raises(ValidationError):
        entry(path) if path else ArtifactEntry(relative_path=path, role=ArtifactRole.RECORDING, size_bytes=1, sha256=SHA)


VALID = [
    "recording.mcap", "recording/valid.mcap", "recording/bag/bag_0.mcap", "recording/rosbag2_2026_10_28-12_00_00/x_0.mcap",
    "profile/synthetic_fixture.profile.yaml", "capture/capture_health.json", "validation_report.json", "metadata/metadata.json",
    "trigger/trg.json", "a b/c d.mcap", ".hidden/file", "dir/.hidden", "ünï/côde.mcap", "a.b.c/d..e", "dots...in/name", "rosbag2_12:00:00/x_0.mcap",  # a colon that is not a drive prefix
]


@pytest.mark.parametrize("path", VALID)
def test_valid_manifest_paths_are_preserved(path):
    assert relative_path_problem(path) is None
    assert entry(path).relative_path == path


def test_every_accepted_path_stays_inside_the_root_on_posix_and_windows():
    """For each accepted path, joining it to a root and normalising on EITHER platform's rules stays below the root."""
    for path in VALID:
        win = ntpath.normpath(ntpath.join("C:\\pkg", path))
        assert win.lower().startswith("c:\\pkg\\"), (path, win)
        assert not PureWindowsPath(path).drive and not PureWindowsPath(path).root and ".." not in PureWindowsPath(path).parts
        posix = os.path.normpath(os.path.join("/pkg", path))
        assert posix.startswith("/pkg/") and ".." not in PurePosixPath(path).parts


@pytest.mark.parametrize("path", ["..\\x", "a\\..\\..\\x", "a/..\\..\\x", "..\\..\\Windows\\win.ini", "C:\\x", "C:/x", "\\\\server\\share\\x",
                                  "//server/share/x", "/abs", "a/../../x"])
def test_these_paths_really_escape_a_windows_root_and_are_all_rejected(path):
    """Documents WHY they are rejected: under Windows path rules each one leaves `C:\\pkg`. (Several were accepted by the old check,
    which only looked at '/'-separated '..' and a leading '/', '\\' or drive letter.)"""
    escaped = ntpath.normpath(ntpath.join("C:\\pkg", path))
    assert not escaped.lower().startswith("c:\\pkg\\")
    assert relative_path_problem(path) is not None


def test_manifest_with_an_unsafe_artifact_path_is_refused_as_a_whole():
    good = json.loads((COMPAT / "day1_package" / "manifest.json").read_text())
    for bad in ("..\\..\\evil", "a/..\\b", "C:\\x", "a//b", "/abs", "../x"):
        data = json.loads(json.dumps(good))
        data["artifacts"][0]["relative_path"] = bad
        with pytest.raises(ValidationError):
            Manifest.model_validate(data)


def test_existing_manifests_still_load_and_verify():
    """Day 1 and Day 2 era artifacts committed under tests/compat keep working under the stricter rule."""
    m = Manifest.model_validate_json((COMPAT / "day1_package" / "manifest.json").read_text())
    assert m.artifacts and all(relative_path_problem(a.relative_path) is None for a in m.artifacts)
    assert verify_package(COMPAT / "day1_package") == []


def test_exported_manifests_contain_only_safe_paths(fx, synth_profile, tmp_path):
    bag = write_bag(tmp_path / "s" / "bagq", three_way_split())
    for src, name in ((fx / "valid.mcap", "p1"), (bag, "p2")):
        res = export_package(analyze_session(src, synth_profile), tmp_path / name, now=NOW)
        assert all(relative_path_problem(a.relative_path) is None for a in res.manifest.artifacts)
        assert verify_package(tmp_path / name) == []


def test_export_of_a_source_name_that_cannot_be_a_safe_path_is_a_clean_error(fx, synth_profile, tmp_path):
    odd = tmp_path / "weird\\name.mcap"  # a legal POSIX file name; a path separator on Windows
    shutil.copy(fx / "valid.mcap", odd)
    with pytest.raises(ExportError, match="Cannot package"):
        export_package(analyze_session(odd, synth_profile), tmp_path / "out", now=NOW)
    assert not (tmp_path / "out").exists()
    assert not [p for p in tmp_path.iterdir() if "staging" in p.name]  # nothing half-written is left behind


# ------------------------------------------------------------------ symlink components

@pytest.fixture
def package(fx, synth_profile, tmp_path):
    bag = write_bag(tmp_path / "s" / "bagz", three_way_split())
    export_package(analyze_session(bag, synth_profile), tmp_path / "pkg", now=NOW)
    assert verify_package(tmp_path / "pkg") == []
    return tmp_path / "pkg"


@pytest.fixture
def opened(monkeypatch):
    """Real paths of everything opened or listed while it is active."""
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


def test_first_symlink_component_finds_parents_not_only_the_final_file(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "f.txt").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    (tmp_path / "real" / "flink").symlink_to(tmp_path / "real" / "f.txt")
    assert first_symlink_component(tmp_path, "real/f.txt") is None
    assert first_symlink_component(tmp_path, "link/f.txt") == "link"
    assert first_symlink_component(tmp_path, "real/flink") == "real/flink"
    assert first_symlink_component(tmp_path, "nothing/here") is None  # a missing component ends the search
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c").symlink_to(tmp_path / "real")
    assert first_symlink_component(tmp_path, "a/b/c/f.txt") == "a/b/c"


@pytest.mark.parametrize("where", ["recording", "recording/bagz"])
def test_verify_package_does_not_read_through_a_symlinked_parent(package, tmp_path, opened, where):
    outside = tmp_path / "outside_area"
    outside.mkdir()
    shutil.move(str(package / where), str(outside / "moved"))  # the bytes outside are IDENTICAL: only the path is wrong
    (package / where).symlink_to(outside / "moved")
    opened.clear()
    problems = verify_package(package)
    assert problems and any("passes through the symlink" in p and "it was not read" in p for p in problems)
    through = [p for p in problems if "passes through the symlink" in p]
    assert len(through) == 4  # all four artifacts below the link (3 segments + metadata.yaml) are refused individually
    assert not [p for p in opened if "/outside_area" in p], "a file or directory outside the package was opened"


def test_verify_package_final_symlink_message_is_unchanged(package, tmp_path):
    seg = package / "recording" / "bagz" / "bagz_0.mcap"
    outside = tmp_path / "o.mcap"
    outside.write_bytes(seg.read_bytes())
    seg.unlink()
    seg.symlink_to(outside)
    assert any("bagz_0.mcap: is a symlink (packages contain regular files only)" in p for p in verify_package(package))


def test_verify_package_does_not_read_a_symlinked_manifest(package, tmp_path, opened):
    outside = tmp_path / "outside_area"
    outside.mkdir()
    shutil.move(str(package / "manifest.json"), str(outside / "manifest.json"))
    (package / "manifest.json").symlink_to(outside / "manifest.json")
    opened.clear()
    assert verify_package(package) == ["manifest.json is a symlink (packages contain regular files only); it was not read"]
    assert not [p for p in opened if "/outside_area" in p]


def test_verify_package_unlisted_symlinked_directory_is_reported_and_never_entered(package, tmp_path, opened):
    outside = tmp_path / "outside_area"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside")
    (package / "extra").symlink_to(outside)
    opened.clear()
    problems = verify_package(package)
    assert any(p.startswith("extra: file is not listed in the manifest") for p in problems)
    assert not [p for p in opened if "/outside_area" in p]
    assert not any("secret.txt" in p for p in problems)


def test_a_clean_package_is_unaffected_by_the_symlink_checks(package):
    assert verify_package(package) == []


# ------------------------------------------------------------------ incomplete scans must not look clean

def _make_unreadable(monkeypatch, target: Path):
    """Simulate `target` (a directory) failing to list, as a permission error would. Works as root, where chmod would not."""
    real_scandir = os.scandir
    tgt = os.path.realpath(target)

    def scandir(path="."):
        if os.path.realpath(path) == tgt:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)


def test_unreadable_nested_directory_makes_verification_fail_not_pass(package, monkeypatch):
    nested = package / "extra" / "deeper"
    nested.mkdir(parents=True)
    (nested / "unlisted.txt").write_text("hidden from the scan")
    _make_unreadable(monkeypatch, nested)
    problems = verify_package(package)
    assert problems, "an incomplete scan was reported as clean"
    assert any(p.startswith("extra/deeper: directory could not be read") and "scan for unlisted files is incomplete" in p
               and "Permission denied" in p for p in problems)


def test_unreadable_package_root_is_an_explicit_problem(package, monkeypatch):
    real_scandir = os.scandir
    root = os.path.realpath(package)

    def scandir(path="."):
        # the manifest/artifact reads are unaffected; only the directory LISTING of the root fails
        if os.path.realpath(path) == root:
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    problems = verify_package(package)
    assert any(p.startswith(".: directory could not be read") and "incomplete" in p for p in problems)


def test_unreadable_directory_does_not_hide_other_findings(package, monkeypatch):
    (package / "stray.txt").write_text("unlisted")
    nested = package / "locked"
    nested.mkdir()
    _make_unreadable(monkeypatch, nested)
    problems = verify_package(package)
    assert any(p.startswith("stray.txt: file is not listed") for p in problems)
    assert any(p.startswith("locked: directory could not be read") for p in problems)


def test_a_readable_package_still_verifies_clean_with_the_error_handler(package):
    assert verify_package(package) == []


def test_unreadable_directory_scan_is_read_only(package, monkeypatch):
    from tests.test_inventory import forbid_writes, tree_state
    (package / "locked").mkdir()
    before = tree_state(package)
    _make_unreadable(monkeypatch, package / "locked")
    forbid_writes(monkeypatch)
    assert verify_package(package)
    assert tree_state(package) == before


def test_real_unreadable_directory_as_unprivileged_user(package):
    """Real chmod 000 is only meaningful when not root; skipped as root (root ignores permissions)."""
    if os.geteuid() == 0:
        pytest.skip("root bypasses file permissions; the simulated cases above cover the logic")
    d = package / "locked"
    d.mkdir()
    (d / "x").write_text("x")
    d.chmod(0)
    try:
        assert any("directory could not be read" in p for p in verify_package(package))
    finally:
        d.chmod(0o700)
