"""Session status derivation. Pure function, no I/O.

EXACT RULES (evaluated top to bottom; first match wins):

  1. profile_valid is False                              -> failed
  2. artifact_readability == unreadable                  -> failed
  3. artifact_readability == partially_readable          -> partial
  4. no checks were executed                             -> partial
  5. any REQUIRED check has outcome fail                 -> partial
  6. any REQUIRED check has outcome inconclusive         -> partial
  7. otherwise                                           -> complete

Notes:
  * `complete` is only reachable when the artifact is fully readable, at least
    one check ran, and every required check is `pass` or `not_applicable`.
  * Non-required checks (warnings) never block `complete`, but remain in the
    report.
  * A readable file with failed required checks is `partial`, not `failed`:
    usable evidence exists, but requirements were not met.
  * `failed` means the tool could not establish a usable evidence base.
"""

from __future__ import annotations

from collections.abc import Sequence

from .schemas import ArtifactReadability, CheckOutcome, CheckResult, SessionStatus


def derive_status(
    *,
    profile_valid: bool,
    readability: ArtifactReadability,
    checks: Sequence[CheckResult],
) -> tuple[SessionStatus, list[str]]:
    """Return (status, human-readable reasons)."""
    if not profile_valid:
        return SessionStatus.FAILED, ["robot profile is invalid"]
    if readability is ArtifactReadability.UNREADABLE:
        return SessionStatus.FAILED, ["recording is unreadable"]
    if readability is ArtifactReadability.PARTIALLY_READABLE:
        return SessionStatus.PARTIAL, ["recording is only partially readable (truncated or malformed)"]
    if not checks:
        return SessionStatus.PARTIAL, ["no validation checks were executed"]

    failed = [c.check_id for c in checks if c.required and c.outcome is CheckOutcome.FAIL]
    inconclusive = [
        c.check_id for c in checks if c.required and c.outcome is CheckOutcome.INCONCLUSIVE
    ]
    reasons: list[str] = []
    if failed:
        reasons.append(f"required checks failed: {sorted(failed)}")
    if inconclusive:
        reasons.append(f"required checks inconclusive: {sorted(inconclusive)}")
    if reasons:
        return SessionStatus.PARTIAL, reasons

    return SessionStatus.COMPLETE, ["all required checks passed on a fully readable recording"]
