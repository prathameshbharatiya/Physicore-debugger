# M3d Step 2 — onset-table verification record (DESIGN §4.1 against the fields the detectors actually emit)

Method: for each row, the real M3c detector (or the real M2 investigator) is run over a synthetic recording by `tests/localization/test_sym.py`; the expected interval is computed from the declarative recording spec, not from detector or M3d code. "Result" = what the executed test established.

| Source | Fields actually emitted (verified in source and by test) | Implemented onset | Matches DESIGN §4.1? |
|---|---|---|---|
| `log_severity`, `diagnostic_status`, `goal_status`, `power_status` (SYM-01..04) | `first_observed_at` = first occurrence log time; `last_observed_at` = last occurrence (omitted for one occurrence) | point at first; extent [first, last] | yes |
| `non_finite_value`, `array_length_change` (SYM-07, 08) | same first/last over the run | point at first; extent [first, last] | yes |
| `publishing_gap` (SYM-05) | `first_observed_at` / `last_observed_at` = the two bounding captured messages (`times=(a, b)`) | `[earlier, later]` | yes |
| `rate_change` (SYM-06) | `first_observed_at` = first flagged window start; `last_observed_at` = START of the last flagged window (not its end) when the run has >1 window; `observed_value` carries `window_ns`, `first_window_start_ns`, `last_window_start_ns` | onset `[first_window_start, first_window_start + window_ns]`; extent `[first_window_start, last_window_start + window_ns]` | **refinement** (see 1) |
| `timestamp_regression`, `timestamp_duplicate` (SYM-09, 09b, 09c, 10) | `first_observed_at` is the PAYLOAD stamp (domain `payload_stamp:<topic>:<field>`); receipt times exist only in the retained `observations` (regression: [previous, offending]; duplicate: [first, first repeat, ...]) | log lane: point at receipt of the offending message / first repeat (2nd retained observation); payload lane: point at the stamp value; if fewer than 2 observations were retained there is **no log-lane claim** | yes (D11), with the retention rule (see 2) |
| M2 `range`/`tolerance`/`equals` (observation kind) (SYM-11) | `time.kind = observation`, `time.ns` = the divergent sample | point | yes |
| M2 `transition` (SYM-11) | `time.kind = deadline_expiry`, `time.ns` = entry time + deadline; source role `transition_entry` | `[entry observation time, deadline]` | **correction** (see 3) |
| M2 `event_response` (SYM-12) | `time.kind = deadline_expiry`; roles `trigger_event` (+ `late_response`) | `[trigger time, deadline]`; the late response never defines the start | yes |

## Items for review (nothing was silently adapted)
1. **Rate onset.** DESIGN said "window span; extent equals onset". The detector's finding covers a *run* of windows. M3d uses the first flagged window as the onset and keeps the whole run as extent. Rationale: the symptom (a window whose count is at/below the ratio of baseline) first holds in the first flagged window; the whole-run alternative would only widen the interval (more `unordered`, never a wrong order). The window end is used as a closed bound, one nanosecond wider than the half-open window the detector uses (conservative).
2. **Stamp-order retention rule.** The detector retains at most `max_retained_observations` references. When fewer than two are retained the offending receipt is unknown, so no log-lane placement is produced (the payload lane remains). The symptom is also flagged through the finding's own `retained_observations_truncated` note; the lane's defence is handled by Step 5 blockers.
3. **M2 transition.** DESIGN §4.1 said "point at the divergent sample". The M2 `transition` finding that the example produces has `time.kind = deadline_expiry`, so the onset is the deadline window `[entry, deadline]`, same as event_response.
4. **Identities.** The design's single `identity` is a map `identities: {kind: value}` because diagnostics and batteries carry two identity fields. Only known, untruncated text is kept; unknown or truncated text is withheld (exact matching would otherwise be unsafe).
5. **Vocabulary additions (additive):** onset basis `payload_stamp_value`. M2 symptoms carry severity `not_applicable` (M2 has no severity).
6. **M2 `topic`** is the topic of the finding's first source reference (M2's finding has no topic field of its own).
No row of the table failed; items 1–3 are refinements of the table's own semantics, recorded here for review.
