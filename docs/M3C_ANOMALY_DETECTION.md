# M3c — automatic anomaly detection

`physicore-collector evidence detect-anomalies PATH --anomaly-config FILE [--detector ID]... [--json] [--fail-on-findings]`

Built on the accepted Phase 1/2, M3a and M3b tree, which is unchanged (the only accepted file edited is `cli.py`: one added command). **Synthetic evidence only.** No real robot
recording has been used; nothing here is real-world validation, and the decoder licensing review (mcap-ros2-support) is outstanding.

A **finding states what was observed in the recorded stream, where, and under which parameters.** It is not a diagnosis and never states a cause. Severity is an ordered label of
the indicator class, not a failure probability and not a statement of physical impact.

## What it does and does not do

* Runs only detectors **enabled in the configuration**; every Tier B numeric parameter is mandatory and has **no default**. A missing or invalid parameter makes the configuration invalid (exit 3, nothing analysed).
* Reads the evidence once, in the deterministic order `(mcap log_time, segment order, message index)`; decodes a message at most once and not at all when no enabled detector needs it (gap/rate never decode).
* Read-only; the report goes to stdout (readable, or canonical JSON with `--json`). Nothing is written into `PATH`.
* **Not implemented, by decision:** late-message detection (deferred), a streaming loader, LLM or causal analysis, M3d.

## Detectors

| Id | Reports | Clock domain |
|---|---|---|
| `tierA.ros_log` | `rcl_interfaces/Log` / `rosgraph_msgs/Log` messages at WARN, ERROR, FATAL (ROS 2: 30/40/50; ROS 1: 4/8/16) | `mcap_log_time` |
| `tierA.diagnostics` | `DiagnosticArray` / `DiagnosticStatus` elements at WARN, ERROR, STALE | `mcap_log_time` |
| `tierA.goal_status` | ABORTED and CANCELED goals (`action_msgs/GoalStatusArray`, `actionlib_msgs/GoalStatusArray`) | `mcap_log_time` |
| `tierA.battery` | `BatteryState` health flags and configured percentage thresholds | `mcap_log_time` |
| `tierB.publishing_gap` | an interval between consecutive captured messages above `max(ceil(multiplier x lower-median interval), floor)` | `mcap_log_time` |
| `tierB.rate_change` | windows whose message count departs from the lower-median baseline by a configured ratio | `mcap_log_time` |
| `tierB.stamp_order` | backward and duplicate header stamps of configured fields (stamp validity from the accepted M3b `header_stamp_ns`) | `payload_stamp:<topic>:<field>` |
| `tierB.array_length` | a sequence field whose length differs from a configured length or from the recording's mode (explicit opt-in per topic and path) | `mcap_log_time` |
| `tierB.non_finite` | NaN, +Inf, -Inf in float leaves of opted-in topics (exact counts; `include_paths` / `exclude_paths`) | `mcap_log_time` |

Log time and payload stamps are never compared with each other.

### Result statuses (per detector, never collapsed)

`findings_reported`, `no_anomaly_observed`, `insufficient_evidence`, `blind_spot`, `not_applicable`, `not_configured`. "No anomaly observed" means nothing was found in what was
evaluated; it is withheld when the recording is incomplete, when messages were undecodable, or when too little data exists for the rule.

### Identity and grouping

Identical occurrences with a **known** identity (for example logger name, goal id, hardware id) are grouped into one finding with an exact `occurrence_count`. An empty, absent or
malformed identity field is **never merged**: each occurrence is its own finding, and a malformed identity never suppresses a finding that its valid severity field warrants.

### Finding identity

`finding_id` is derived from the detector id and version, a hash of its parameters, **every contributing artifact (path and SHA-256)**, the topic, field-path template, group key and
first observation. Replacing any artifact that holds an occurrence changes the id; replacing an unrelated artifact does not. Baseline-dependent findings (gap, rate, mode
baseline) include every artifact of the topic.

### Counts and retention

Counts are always exact. Only the retained `observations` list is capped (`max_retained_observations`); `observations_truncated` and the exact totals are reported. The number of
findings per detector is capped (`max_findings_per_detector`) and the exact number suppressed is reported in `findings_truncated`.

## Configuration

See `examples/anomaly/anomaly.example.yaml` (marked "example values, not recommendations"). Unknown keys are rejected at every level; rational parameters are strings such as `"0.8"`.

## Exit codes

| Exit | Meaning |
|---:|---|
| 0 | completed, including when findings exist, and when detectors are insufficient / blind / not applicable / not configured |
| 1 | findings with `--fail-on-findings`, **or** the evidence is partial / integrity failed or unverifiable (accepted `inventory` exit code); the report is still produced |
| 2 | the evidence could not be inspected (a report with `blind_spot(evidence_not_inspectable)` is emitted), or a usage error |
| 3 | the anomaly configuration is invalid; the problems are listed and no evidence is analysed |

The report's `exit_code` and `exit_reasons` (`invalid_config`, `evidence_not_inspectable`, `evidence_integrity_or_completeness`, `findings_with_fail_on_findings`) state exactly which applied.

## Per-detector notes

* **ros_log / diagnostics / goal_status / battery.** Constants are read from the recorded schema first; the built-in verified table is used only where the schema carries none (a note says so). A level of an unrecognised value is counted, not reported as a finding.
* **publishing_gap / rate_change.** Descriptive only: a gap or rate change between captured messages cannot be told apart from capture loss or from true silence. Boundary gaps are not evaluated. The baseline comes from the recording itself and may be contaminated by the anomalies it is used to find (flagged when 25 % or more of the intervals are flagged).
* **stamp_order.** A zero stamp is an ordinary value. Invalid stamps (negative, out of range, wrong type) are excluded, counted and never hidden; a JSON payload carries no schema, so the stamp type is `structure_only`. Duplicate runs report `repeats = occurrences - 1`.
* **array_length.** Expected length comes only from a configured `expected_length` or the recording's mode (`min_observations`, `min_share`; equal share passes; a tie is `insufficient_evidence(baseline_tie)`). Fixed-size arrays (`T[N]`) are `not_applicable` (structural). Absent optional fields are not length-0 findings and end a run. Legitimately variable arrays (for example diagnostics `status[]`) will be reported: that is a documented false-positive case.
* **non_finite.** Every leaf of an opted-in message is scanned; there is no early exit. NaN is often an intentional "no measurement" marker: use `exclude_paths`. JSON `null` is not a NaN (counted as `json_null_not_inspected`); numeric strings are not interpreted. JSON `NaN` / `Infinity` tokens make the accepted loader refuse the whole message, so this detector re-parses such messages leniently and says so (`json_nonfinite_tokens_parsed`). Image and point-cloud payloads are out of scope of the accepted decoder and are counted as undecodable.

### Bit patterns (measured, not assumed)

float64 NaN/Inf: bits re-encoded from the decoded Python float (a start-up self-check proves the round trip; if it fails the bits are withheld). float32 +/-Inf: bits fully determined.
**float32 NaN: only class and sign are reported; no payload is claimed**, because the decoder quiets signalling NaNs (pinned by a characterisation test that fails if that changes). JSON tokens carry no bit pattern.

## Limitations (always in the report)

1. Late-message detection is not performed (deferred).
2. Findings describe observations; they are not diagnoses.
3. A gap or rate change cannot be told apart from capture loss or silence.
4. Synthetic-data validation does not establish compatibility with real robot recordings; real ROS recording validation is outstanding.
5. Decoder licensing and redistribution review (mcap-ros2-support) is outstanding; the decoder is an optional extra and is not bundled.
6. ROS documentation verification is outstanding where not independently confirmed; constants and field types were checked against the rosbags typestore.
7. Exact float32 NaN payload bits are not recoverable.
8. `recording_incomplete` uses the whole-recording completeness flag, a conservative approximation for every topic.

## Performance

See the test report (`docs/M3C_TEST_REPORT.md`) for the measured throughput and memory. The timing detectors retain 16 bytes per message of the topic; transient interval lists make the
measured figure higher (reported as measured).


## Numeric signal-rule primitives (additive)

`anomaly.signal_rules` adds deterministic rule functions for robot-profile numeric signals:

- rate-of-change thresholds with explicit units per second;
- stuck-value runs, gated by an explicit caller-supplied expectation that the signal should change;
- discontinuity thresholds;
- oscillation detection using configured direction-change and amplitude limits;
- cross-signal inconsistency only under a declared affine relationship, matching units, a shared clock domain, and an explicit maximum timestamp skew;
- persistence labeling that retains short-lived events instead of silently deleting them.

The functions accept finite, already-normalized `SignalPoint` values with source references. They do not infer units, expected transitions, clock synchronization, or signal relationships. They currently form a tested rule-engine layer; the existing M3c CLI detector registry/configuration is unchanged, so these new rules are not yet selectable through `evidence detect-anomalies`. Pilot-specific config integration and validation against reviewed/labeled recordings remain outstanding.



## Robot-specific numeric signal profiles

The existing M3c detector suite continues to cover ROS logs, diagnostics, action status, battery health, publishing gaps, rate changes,
stamp ordering, array-length changes and non-finite values. For numeric telemetry, `anomaly.signal_profile` now provides a strict
robot-family profile and evaluator around the deterministic signal-rule primitives.

A profile declares a unit for every signal and may explicitly configure rate-of-change, stuck-value, discontinuity and oscillation
thresholds. Stuck detection is rejected unless `change_expected: true` is stated. Cross-signal checks require a declared affine
relationship, exact unit matches, one shared clock domain and a maximum timestamp skew. No unit conversion is inferred by this evaluator:
supply already-normalized samples from the robot registry and retain the original values/provenance upstream.

Expected transition intervals are explicit and clock-domain-scoped. Overlapping events are retained and annotated with the transition
label rather than silently suppressed; this keeps startup/shutdown context visible for engineer review. Persistence/debounce labels
short events without deleting them. Every event retains its source references and observed values.

API: `validate_signal_profile(mapping)`, `load_signal_profile(path)`, and `evaluate_signal_profile(profile, samples)`.
The YAML example is `examples/anomaly/signal_profile.example.yaml`. The profile evaluator is an additive library API; it is not yet
wired into the existing `evidence detect-anomalies` report/CLI or the hypothesis catalogue. Timeline integration is exposed through
the event's clock domain, interval and source refs, but automatic timeline/hypothesis insertion is not claimed. Real-robot labels and
reviewed operating envelopes are still required before threshold quality, false-positive rate or false-negative rate can be claimed.

### Validation status

Synthetic rule-level tests exercise each detector family, explicit units, transition annotation, short-event retention, and strict
configuration rejection. The repository's existing M3c test corpus remains the regression basis for the existing CLI detectors.
No labeled real-robot corpus is available; therefore false-positive/false-negative rates and customer operating thresholds remain
unmeasured. Synthetic test counts are not presented as field accuracy.
