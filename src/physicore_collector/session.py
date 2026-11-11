"""Session analysis: load every input once, scan, validate. No printing, no export.

Both `session validate` and `export` start from `analyze_session`, so they always agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bag import BagAnalysis, scan_bag
from .capture_health import LoadedHealth, build_capture_checks, load_health
from .checksums import sha256_file
from .errors import InputError
from .inputs import LoadedMetadata, LoadedTrigger, load_metadata, load_trigger
from .mcap_reader import ScanResult, scan_recording
from .profile import LoadedProfile, load_profile
from .schemas import ToolInfo, ValidationReport
from .validation import validate_recording


CAPTURE_HEALTH_NAME = "capture_health.json"
CAPTURE_BAG_DIR = "bag"


def resolve_recording_input(
    recording: str | Path, health: str | Path | None = None
) -> tuple[Path, Path | None]:
    """Accept a capture *session directory* (as written by `capture run`) as a shortcut for its
    `bag/` directory plus `capture_health.json`. Anything else is returned unchanged. An explicit
    `health` always wins. A directory with its own metadata.yaml is a bag, never a session."""
    rec = Path(recording)
    if rec.is_dir() and not (rec / "metadata.yaml").exists():
        sidecar, bag = rec / CAPTURE_HEALTH_NAME, rec / CAPTURE_BAG_DIR
        if sidecar.is_file():
            if not bag.is_dir():
                raise InputError(
                    f"'{rec}' looks like a capture session but has no '{CAPTURE_BAG_DIR}/' directory: "
                    "the recorder produced no output (or it was removed).",
                    hint="Run `physicore-collector capture status` on the session for details.",
                )
            return bag, Path(health) if health is not None else sidecar
    return rec, Path(health) if health is not None else None


@dataclass(frozen=True)
class SessionContext:
    recording_path: Path  # an .mcap file, or a rosbag2 bag directory (Day 2)
    # SHA-256 of the recording as it was when analysis began. For a bag directory this is
    # BagAnalysis.digest (a digest over the sorted per-file digests); per-file digests are in `bag`.
    recording_sha256: str
    scan: ScanResult
    profile: LoadedProfile
    metadata: LoadedMetadata | None
    trigger: LoadedTrigger | None
    report: ValidationReport
    bag: BagAnalysis | None = None  # set iff the recording is a rosbag2 directory
    health: LoadedHealth | None = None  # capture-health sidecar, if supplied

    @property
    def recording_kind(self) -> str:
        return "rosbag2_directory" if self.bag is not None else "mcap_file"


def analyze_session(
    recording: str | Path,
    profile_path: str | Path,
    *,
    trigger_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
    health_path: str | Path | None = None,
) -> SessionContext:
    """Raises a PhysiCoreError for inaccessible/invalid *inputs*. A bad *recording* is not
    an exception: it yields a report with status partial or failed."""
    profile = load_profile(profile_path)
    trigger = load_trigger(trigger_path) if trigger_path is not None else None
    metadata = load_metadata(metadata_path) if metadata_path is not None else None

    health = load_health(health_path) if health_path is not None else None

    rec = Path(recording)
    if not rec.exists():
        raise InputError(
            f"Recording not found: '{rec}'", hint="Check the path to the .mcap file or rosbag2 bag directory."
        )
    bag: BagAnalysis | None = None
    extra: list = []
    if rec.is_dir():
        scan, bag = scan_bag(rec)
        digest = bag.digest
        extra.extend(bag.checks)
    elif rec.is_file():
        if health is not None:
            raise InputError(
                "A capture-health file can only accompany a rosbag2 bag directory, not a single .mcap file.",
                hint="Pass the bag directory (or the capture session directory) instead.",
            )
        try:
            digest = sha256_file(rec)
        except OSError as e:
            raise InputError(f"Cannot read recording '{rec}': {e.strerror or e}") from e
        scan = scan_recording(rec)
    else:
        raise InputError(f"Recording path is neither a regular file nor a directory: '{rec}'")
    if health is not None:
        extra.extend(build_capture_checks(health, bag))

    report = validate_recording(
        scan,
        profile.profile,
        supplied_metadata=metadata.data if metadata else None,
        trigger=trigger.trigger if trigger else None,
        extra_checks=extra,
    )
    return SessionContext(rec, digest, scan, profile, metadata, trigger, report, bag, health)


def session_json(ctx: SessionContext) -> dict:
    """Machine-readable summary used by `session validate --json`."""
    from .manifest import build_known_gaps, build_loss_coverage

    extra: dict = {}
    if ctx.bag is not None:
        extra["recording_kind"] = ctx.recording_kind
        extra["bag"] = ctx.bag.info.model_dump(mode="json")
    if ctx.health is not None:
        extra["capture_health"] = ctx.health.health.summary(ctx.health.sha256).model_dump(mode="json")
    return {
        **extra,
        "tool": ToolInfo().model_dump(mode="json"),
        "recording": {
            "file_name": ctx.recording_path.name,
            "size_bytes": ctx.scan.inspection.size_bytes,
            "sha256": ctx.recording_sha256,
        },
        "profile": {"profile_id": ctx.profile.profile.profile_id, "sha256": ctx.profile.sha256},
        "inspection": ctx.scan.inspection.model_dump(mode="json"),
        "validation": ctx.report.model_dump(mode="json"),
        "known_gaps": [g.model_dump(mode="json") for g in build_known_gaps(ctx)],
        "loss_detection_coverage": build_loss_coverage(ctx).model_dump(mode="json"),
    }
