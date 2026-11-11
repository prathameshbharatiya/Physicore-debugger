"""Trigger-vs-recording time comparison, gated on explicitly established compatibility.

A trigger timestamp may be compared with MCAP `log_time` ONLY when ALL hold:
  1. trigger.timestamp_basis is known (not `unknown`);
  2. the profile declares a known `log_time_basis` for the recording;
  3. the two basis labels are equal; AND
  4. the trigger carries an explicit `basis_compatibility` attestation with
     `compatible_with_log_time: true` and a stated `established_by`.
Equal labels alone are never sufficient: two `sim_time` clocks from different
simulators are different clocks.

Decision order (first match wins):
  trigger basis unknown                       -> UNAVAILABLE
  profile log_time_basis unknown              -> UNAVAILABLE
  attestation says NOT compatible             -> UNAVAILABLE
  labels differ                               -> INCONCLUSIVE (even if attested: conflict is not resolved automatically)
  no attestation                              -> INCONCLUSIVE
  recording has no messages (no window)       -> UNAVAILABLE
  otherwise compare inclusively with the window:
      ts < min                                -> BEFORE_RECORDING_WINDOW
      ts > max                                -> AFTER_RECORDING_WINDOW (INCONCLUSIVE if the recording is
                                                 truncated/malformed: the trigger may lie in the unread part)
      else                                    -> WITHIN_RECORDING_WINDOW
`established_by` is human-supplied text. This tool records it but cannot verify it.
"""

from __future__ import annotations

from .schemas import (
    ArtifactReadability, RecordingWindow, TimeBase, Trigger, TriggerComparison,
    TriggerComparisonResult as R,
)


def compare_trigger(
    trigger: Trigger,
    profile_log_time_basis: TimeBase,
    window: RecordingWindow | None,
    readability: ArtifactReadability,
) -> TriggerComparison:
    tb = trigger.timestamp_basis
    if tb is TimeBase.UNKNOWN:
        return TriggerComparison(result=R.UNAVAILABLE, reason="trigger timestamp_basis is 'unknown'")
    if profile_log_time_basis is TimeBase.UNKNOWN:
        return TriggerComparison(
            result=R.UNAVAILABLE,
            reason="the profile does not declare what the recording's log_time represents (log_time_basis is 'unknown')",
        )
    att = trigger.basis_compatibility
    if att is not None and not att.compatible_with_log_time:
        return TriggerComparison(
            result=R.UNAVAILABLE,
            reason=f"trigger is explicitly declared NOT compatible with log_time (established_by: {att.established_by})",
        )
    if tb is not profile_log_time_basis:
        return TriggerComparison(
            result=R.INCONCLUSIVE,
            reason=(
                f"trigger basis '{tb.value}' differs from the profile's log_time_basis "
                f"'{profile_log_time_basis.value}'; compatibility is not established"
            ),
        )
    if att is None:
        return TriggerComparison(
            result=R.INCONCLUSIVE,
            reason=(
                f"both clocks are labelled '{tb.value}', but matching labels do not prove the same clock; "
                "no explicit basis_compatibility attestation was provided"
            ),
        )
    if window is None:
        return TriggerComparison(result=R.UNAVAILABLE, reason="the recording contains no messages, so it has no time window")

    ts = trigger.timestamp_ns
    note = f" (compatibility established_by: {att.established_by})"
    if ts < window.min_log_time_ns:
        return TriggerComparison(result=R.BEFORE_RECORDING_WINDOW, reason="trigger precedes the first recorded message" + note)
    if ts > window.max_log_time_ns:
        if readability is not ArtifactReadability.READABLE:
            return TriggerComparison(
                result=R.INCONCLUSIVE,
                reason="trigger is after the last RECOVERED message, but the recording is truncated/malformed so it may lie in the unread part" + note,
            )
        return TriggerComparison(result=R.AFTER_RECORDING_WINDOW, reason="trigger follows the last recorded message" + note)
    return TriggerComparison(result=R.WITHIN_RECORDING_WINDOW, reason="trigger lies within the recording's log_time window" + note)
