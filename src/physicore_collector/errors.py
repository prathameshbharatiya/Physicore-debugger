"""Typed errors. Every error carries a human message and an optional hint.

Convention: exceptions mean "the tool could not do its job" (bad input,
unreadable file, unsafe export target). A *validation failure* (e.g. a
required topic is missing) is NOT an exception; it is a normal result that
is recorded in the validation report.

CLI exit codes: 0 = ok, 1 = validation failed, 2 = usage/input/runtime error.
"""

from __future__ import annotations


class PhysiCoreError(Exception):
    """Base class for all expected, user-facing errors."""

    exit_code = 2

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\nHint: {self.hint}"
        return self.message


class InputError(PhysiCoreError):
    """A user-supplied path or option is missing or unusable."""


class ProfileError(PhysiCoreError):
    """Base class for robot profile problems."""


class ProfileNotFoundError(ProfileError):
    """The profile file does not exist or is not a regular file."""


class ProfileParseError(ProfileError):
    """The profile file is not valid YAML/JSON."""


class ProfileValidationError(ProfileError):
    """The profile parsed but violates the profile schema.

    `issues` holds one "field.path: problem" string per violation so callers
    and tests can inspect them without parsing the message.
    """

    def __init__(
        self, message: str, *, hint: str | None = None, issues: list[str] | None = None
    ) -> None:
        super().__init__(message, hint=hint)
        self.issues: list[str] = list(issues or [])


class McapReadError(PhysiCoreError):
    """The recording could not be opened or read as MCAP."""


class MetadataError(PhysiCoreError):
    """A supplied metadata file is missing, unparsable, or not a JSON object."""


class TriggerError(PhysiCoreError):
    """A trigger file is missing, unparsable, or violates the trigger schema."""


class ExportError(PhysiCoreError):
    """Base class for export problems."""


class ExportTargetExistsError(ExportError):
    """The export target already exists. Export never overwrites."""


class ChecksumMismatchError(ExportError):
    """A copied artifact's SHA-256 does not match the source. Export aborted."""


class HealthError(PhysiCoreError):
    """A supplied capture-health sidecar is missing, unparsable, or violates its schema."""


class CaptureError(PhysiCoreError):
    """Base class for problems starting or running a capture."""


class CaptureConfigError(CaptureError):
    """The profile/arguments cannot be turned into a safe recording. Nothing was started."""


class CaptureSessionExistsError(CaptureError):
    """The capture session directory already exists. Capture never reuses or overwrites one."""


class RecorderStartError(CaptureError):
    """The recorder process could not be started (e.g. `ros2` not found)."""


class IncidentError(PhysiCoreError):
    """Base class for incident-preparation problems. `code` is a stable machine-readable reason
    (recorded in `incident_attempt.json` and printed by `incident prepare --json`)."""

    code = "incident_error"

    def __init__(self, message: str, *, hint: str | None = None, code: str | None = None) -> None:
        super().__init__(message, hint=hint)
        if code is not None:
            self.code = code


class IncidentConflictError(IncidentError):
    """The target already exists and is not the same, verified incident. Never overwritten."""

    code = "target_exists"


class IncidentRefusedError(IncidentError):
    """The capture is still active, so its evidence is not final. Nothing was prepared."""

    code = "capture_active"


class InsufficientSpaceError(IncidentError):
    """Not enough free space for the self-contained copy. Checked before anything is copied."""

    code = "insufficient_space"


class NoRecordingError(IncidentError):
    """No readable recording file could be found to package."""

    code = "no_recording_files"


class IncidentDeferredError(IncidentError):
    """Preparation was NOT done because doing it now could compete with a recording (or with a recorder that may
    still be writing). Not a failure of the evidence: nothing was published, the source is untouched, and the
    same command succeeds once the condition clears. Codes start with `deferred_`."""

    code = "deferred_capture_active"
