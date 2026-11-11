"""Safe, portable evidence package export.

Package layout (all paths relative to the package directory):

    recording/<original file name>      byte-identical copy of the input MCAP
      OR (Day 2, rosbag2 directory)
    recording/<bag directory name>/metadata.yaml and every listed segment, byte-identical, with
                                        the bag's own relative layout (so `ros2 bag info` works on it)
    capture/capture_health.json         (Day 2, only if a capture-health sidecar was supplied)
    profile/<original file name>        byte-identical copy of the profile file
    metadata/<original file name>       byte-identical copy of --metadata   (only if supplied)
    trigger/<original file name>        byte-identical copy of --trigger    (only if supplied)
    validation_report.json
    manifest.json                       inventories everything above; has no self-checksum

Safety guarantees, each covered by tests:
  * An existing target (file, directory, or dangling symlink) is NEVER overwritten and there
    is no force option. Checked up front and again immediately before publishing.
  * Everything is written into a hidden staging directory next to the target and published
    with a single rename, so a package either appears complete or not at all.
  * The recording is hashed before analysis, hashed while copying, and the staged copy is
    hashed again; all three must match or export aborts (this also catches a recording that
    changes while exporting).
  * The finished staging directory is re-verified against the manifest before publishing.
  * On ANY failure (including Ctrl-C) the staging directory is removed. If removal itself
    fails the error says so and names the directory; it is never silently left behind.

Known limit: "target must not exist" is check-then-rename. A concurrent process creating an
EMPTY directory at the target in the instant before the rename could be replaced by it
(POSIX rename semantics). Files and non-empty directories are never replaced.
A hard kill (SIGKILL/power loss) can leave a `.<name>.staging-*` directory; it is safe to delete.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .checksums import copy_and_hash, sha256_file
from .errors import ChecksumMismatchError, ExportError, ExportTargetExistsError, InputError
from .manifest import build_manifest
from .schemas import ArtifactEntry, ArtifactRole, Manifest, SessionStatus
from .session import SessionContext

MANIFEST_NAME = "manifest.json"
REPORT_NAME = "validation_report.json"


@dataclass(frozen=True)
class ExportResult:
    package_dir: Path
    manifest: Manifest
    status: SessionStatus


def ensure_target_available(out: str | Path) -> Path:
    """Fail fast if `out` already exists in any form, or its parent is unusable."""
    p = Path(out)
    if os.path.lexists(p):
        raise ExportTargetExistsError(
            f"Export target already exists: '{p}'. Export never overwrites.",
            hint="Choose a new --out directory name (or remove the old package yourself).",
        )
    if not p.parent.is_dir():
        raise InputError(
            f"Parent directory of the export target does not exist: '{p.parent}'",
            hint="Create the parent directory first.",
        )
    return p


def _write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _entry(rel: str, role: ArtifactRole, size: int, digest: str) -> ArtifactEntry:
    try:
        return ArtifactEntry(relative_path=rel, role=role, size_bytes=size, sha256=digest)
    except ValidationError as e:  # a source file/directory name that cannot be a safe package path (e.g. contains a backslash)
        raise ExportError(
            f"Cannot package '{rel}': {e.errors(include_url=False)[0]['msg']}",
            hint="Rename the file or directory so its name has no backslash, control character, drive prefix or '..'-like part.",
        ) from e


def first_symlink_component(root: str | Path, rel: str) -> str | None:
    """The first component of `rel` (a `/`-separated path below `root`) that is a symlink, as a `/`-joined prefix of `rel`, or
    None. Unlike `Path.is_symlink()` this looks at EVERY component, so a symlinked parent directory is found, not only a symlinked
    final file. Nothing is followed. A component that does not exist ends the search (nothing further can be a symlink).
    Raises OSError for a component that cannot be examined (e.g. permission denied); callers decide how to report that.
    Known limit: this is a check, not a lock; a concurrent process could swap a component between the check and a later open."""
    cur = Path(root)
    parts = PurePosixPath(rel).parts
    for i, part in enumerate(parts):
        cur = cur / part
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                return "/".join(parts[: i + 1])
        except (FileNotFoundError, NotADirectoryError):
            return None
    return None


def _entries_not_following_symlinks(root: Path, unreadable: list[str] | None = None) -> list[Path]:
    """Every file and symlink below `root` (symlinked directories are listed, never entered), sorted. Replaces `rglob`, whose
    treatment of symlinked directories differs between supported Python versions. `os.walk` swallows directory errors unless told
    otherwise, so each one is recorded in `unreadable` (a description per directory) and the caller must treat the scan as incomplete."""
    out: list[Path] = []

    def onerror(e: OSError) -> None:
        where = Path(os.path.relpath(e.filename, root)).as_posix() if e.filename else "."
        if unreadable is not None:
            unreadable.append(f"{where}: directory could not be read ({e.strerror or type(e).__name__})")

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=onerror):
        d = Path(dirpath)
        for n in list(dirnames):
            if (d / n).is_symlink():
                out.append(d / n)
                dirnames.remove(n)
        out.extend(d / n for n in filenames)
    return sorted(out)


def _utc_stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def export_package(
    ctx: SessionContext, out: str | Path, *, now: datetime | None = None
) -> ExportResult:
    target = ensure_target_available(out)
    staging = target.parent / f".{target.name}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    os.mkdir(staging)  # exclusive; honours the umask (unlike mkdtemp's 0700)
    try:
        manifest = _build_in_staging(ctx, staging, now or datetime.now(timezone.utc))
        problems = verify_package(staging)
        if problems:
            raise ChecksumMismatchError(
                "Staged package failed verification: " + "; ".join(problems)
            )
        if os.path.lexists(target):  # re-check right before publishing
            raise ExportTargetExistsError(
                f"Export target appeared while exporting: '{target}'. Nothing was overwritten."
            )
        try:
            os.rename(staging, target)
        except OSError as e:
            if e.errno in (errno.EEXIST, errno.ENOTEMPTY, errno.ENOTDIR, errno.EISDIR):
                raise ExportTargetExistsError(
                    f"Export target appeared while exporting: '{target}'. Nothing was overwritten."
                ) from e
            raise ExportError(f"Could not publish package to '{target}': {e.strerror or e}") from e
    except BaseException as original:
        try:
            shutil.rmtree(staging)
        except FileNotFoundError:
            pass  # nothing to clean (e.g. failure happened before/at rename)
        except OSError as cleanup_error:
            raise ExportError(
                f"Export failed ({original}) AND the staging directory '{staging}' could not be "
                f"removed ({cleanup_error}). It is incomplete; delete it manually."
            ) from original
        if isinstance(original, OSError) and not isinstance(original, ExportError):
            # environmental failure (disk full, I/O error, permissions): a clean error, exit code 2
            raise ExportError(
                f"Export failed while writing the package: {original.strerror or original}. "
                "No package was created and the staging directory was removed."
            ) from original
        raise
    return ExportResult(target, manifest, manifest.session_status)


def _copy_verified(src: Path, dst: Path, analysis_sha: str, what: str) -> tuple[str, int]:
    """Copy while hashing, hash the staged copy again; all three digests must agree."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    copied_sha, size = copy_and_hash(src, dst)
    staged_sha = sha256_file(dst)
    if not (analysis_sha == copied_sha == staged_sha):
        raise ChecksumMismatchError(
            f"{what} checksum mismatch during export (the file may have changed while "
            f"exporting): at analysis {analysis_sha}, while copying {copied_sha}, "
            f"staged copy {staged_sha}. No package was created."
        )
    return copied_sha, size


def _manifest_bytes(manifest: Manifest) -> bytes:
    """A single-MCAP manifest is serialized WITHOUT the Day 2 keys, so it is byte-for-byte the
    Day 1 shape and Day 1 tooling (which forbids unknown fields) can still read it."""
    day2 = ("recording_kind", "bag", "capture_health")
    exclude = set() if manifest.manifest_schema_version != "0.1" else set(day2)
    return (manifest.model_dump_json(indent=2, exclude=exclude) + "\n").encode()


def _build_in_staging(ctx: SessionContext, staging: Path, now: datetime) -> Manifest:
    artifacts: list[ArtifactEntry] = []

    # recording: copy while hashing, then hash the staged copy independently
    rec_dir = staging / "recording"
    rec_dir.mkdir()
    if ctx.bag is not None:
        # rosbag2 directory: every inventoried file keeps its relative path under the bag's name.
        # Only files metadata.yaml accounts for (plus metadata.yaml itself) are copied; anything
        # else is reported in the manifest, never silently dropped and never silently included.
        base = f"recording/{ctx.bag.info.bag_directory_name}"
        for bf in sorted(ctx.bag.files, key=lambda f: f.relative_path):
            rel = f"{base}/{bf.relative_path}"
            sha, size = _copy_verified(bf.path, staging / rel, bf.sha256, f"Bag file '{bf.relative_path}'")
            artifacts.append(_entry(rel, bf.role, size, sha))
    else:
        rec_rel = f"recording/{ctx.recording_path.name}"
        copied_sha, size = _copy_verified(ctx.recording_path, staging / rec_rel, ctx.recording_sha256, "Recording")
        artifacts.append(_entry(rec_rel, ArtifactRole.RECORDING, size, copied_sha))

    # profile (original bytes; sha is the same digest recorded as profile_sha256)
    (staging / "profile").mkdir()
    prof_rel = f"profile/{ctx.profile.path.name}"
    prof_sha, prof_size = copy_and_hash(ctx.profile.path, staging / prof_rel)
    if prof_sha != ctx.profile.sha256:
        raise ChecksumMismatchError("Profile file changed while exporting. No package was created.")
    artifacts.append(_entry(prof_rel, ArtifactRole.PROFILE, prof_size, prof_sha))

    for loaded, folder, role in (
        (ctx.metadata, "metadata", ArtifactRole.METADATA),
        (ctx.trigger, "trigger", ArtifactRole.TRIGGER),
    ):
        if loaded is None:
            continue
        (staging / folder).mkdir()
        rel = f"{folder}/{loaded.path.name}"
        _write_new(staging / rel, loaded.raw)  # exact bytes that were parsed
        artifacts.append(_entry(rel, role, len(loaded.raw), loaded.sha256))

    if ctx.health is not None:
        (staging / "capture").mkdir()
        rel = "capture/capture_health.json"
        _write_new(staging / rel, ctx.health.raw)  # exact bytes that were parsed
        artifacts.append(_entry(rel, ArtifactRole.CAPTURE_HEALTH, len(ctx.health.raw), ctx.health.sha256))

    report_bytes = (ctx.report.model_dump_json(indent=2) + "\n").encode()
    _write_new(staging / REPORT_NAME, report_bytes)
    artifacts.append(_entry(REPORT_NAME, ArtifactRole.VALIDATION_REPORT, len(report_bytes),
                            hashlib.sha256(report_bytes).hexdigest()))

    manifest = build_manifest(ctx, artifacts, created_at_utc=_utc_stamp(now))
    _write_new(staging / MANIFEST_NAME, _manifest_bytes(manifest))
    return manifest


def verify_package(package_dir: str | Path) -> list[str]:
    """Re-check a package against its manifest. Returns a list of problems (empty = OK)."""
    root = Path(package_dir)
    mpath = root / MANIFEST_NAME
    if mpath.is_symlink():
        return [f"{MANIFEST_NAME} is a symlink (packages contain regular files only); it was not read"]
    if not mpath.is_file():
        return [f"{MANIFEST_NAME} is missing"]
    try:
        manifest = Manifest.model_validate_json(mpath.read_bytes())
    except (ValidationError, ValueError) as e:
        return [f"{MANIFEST_NAME} is invalid: {e}"]

    problems: list[str] = []
    listed = set()
    for a in manifest.artifacts:
        listed.add(a.relative_path)
        f = root / a.relative_path
        link = first_symlink_component(root, a.relative_path)  # every component, so a symlinked PARENT is caught too
        if link is not None:
            what = "is a symlink" if link == a.relative_path else f"passes through the symlink '{link}'"
            problems.append(f"{a.relative_path}: {what} (packages contain regular files only); it was not read")
            continue
        if not f.is_file():
            problems.append(f"{a.relative_path}: file is missing")
            continue
        if f.stat().st_size != a.size_bytes:
            problems.append(f"{a.relative_path}: size {f.stat().st_size} != manifest {a.size_bytes}")
        digest = sha256_file(f)
        if digest != a.sha256:
            problems.append(f"{a.relative_path}: sha256 {digest} != manifest {a.sha256}")
        if a.role is ArtifactRole.PROFILE and a.sha256 != manifest.profile_sha256:
            problems.append(f"{a.relative_path}: does not match manifest profile_sha256")
    unreadable: list[str] = []
    for f in _entries_not_following_symlinks(root, unreadable):
        if f.is_file() or f.is_symlink():
            rel = f.relative_to(root).as_posix()
            if rel != MANIFEST_NAME and rel not in listed:
                problems.append(f"{rel}: file is not listed in the manifest")
    # An incomplete scan for unlisted files must never look clean.
    problems.extend(f"{u}; the scan for unlisted files is incomplete" for u in unreadable)
    problems.extend(_bag_coherence(manifest))
    return problems


def _bag_coherence(manifest: Manifest) -> list[str]:
    """The manifest's own bag section must agree with its artifact inventory."""
    if manifest.bag is None:
        return []
    out: list[str] = []
    by_path = {a.relative_path: a for a in manifest.artifacts}
    base = f"recording/{manifest.bag.bag_directory_name}"
    for seg in manifest.bag.segments:
        if seg.sha256 is None:
            continue  # nothing was copied for it (missing/unsafe); the manifest says so via `state`
        a = by_path.get(f"{base}/{seg.relative_path}")
        if a is None:
            out.append(f"bag segment '{seg.relative_path}' is in the manifest's bag section but is not an artifact")
        elif a.sha256 != seg.sha256:
            out.append(f"bag segment '{seg.relative_path}': artifact sha256 differs from the bag section")
    return out
