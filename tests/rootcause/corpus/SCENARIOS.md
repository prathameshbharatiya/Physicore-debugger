# M4 ground-truth corpus: scenario specification (frozen)

Written and committed before any hypothesis-catalogue entry exists. The machine-readable source is `spec.json`; its hash is pinned by `tests/rootcause/test_spec.py`. Changing it is a plan change and needs approval.

Every recording is synthetic (JSON-payload MCAP written by the existing test builders). Nothing here is real-robot data, and results on it are not diagnostic accuracy on real robots.

| Id | Family | Injected fault | True class | Correct answer |
|---|---|---|---|---|
| F01 | source_silent | `/lidar/scan` silent for 2 to 3 s then resumes; `/diagnostics` ERROR 0.3 s later | upstream_source_silent | mechanism |
| F02 | sensor_emits_nan | `/imu/data` carries NaN; `/diagnostics` ERROR 0.2 s later | invalid_sensor_data_propagated | mechanism |
| F03 | command_not_followed | STOP issued, `/robot_state` never STOPPED within 300 ms (needs an M2 contract) | command_not_followed | mechanism |
| F04 | array_length_mismatch | `/scan` ranges 359 instead of 360 | configuration_mismatch | mechanism |
| F05 | timestamp_jump | `/odom` header stamp jumps back 5 s | timestamp_or_clock_fault | mechanism |
| F06 | two_unrelated_faults | `/lidar/scan` silent for an interval and later `/imu/data` NaN, no edge | upstream_source_silent and invalid_sensor_data_propagated | mechanism, two separate sets, never linked |
| F07 | red_herring | F01 (bounded silence) plus an unrelated WARN burst on `/rosout` | upstream_source_silent | mechanism; `/rosout` never linked to the fault |
| F08 | reversed_dependency | declared `/sensor_a -> /sensor_b`, recorded order is b then a | not decidable | unresolved |
| F09 | no_fault | healthy recording | none | no_failure |
| F10 | hardware_degradation_no_telemetry | `/odom` rate falls to about 40 percent, no hardware telemetry | hardware_degradation (not observable) | unresolved |

Splits: development seeds 1, 2, 3 (no namespace); hold-out seeds 101, 102 (topics prefixed `/ns1`, `/ns2`). The hold-out set is not looked at while the catalogue and the generator of hypotheses are built; the development runner refuses it unless asked explicitly.

Per family the specification also freezes: acceptable leaders, forbidden links, what the recording contains and lacks, the measurements a correct report must name as missing, the M3c detectors expected to fire, and what a seed varies.

## Amendment A1 (before any catalogue entry)

The accepted publishing-gap detector never evaluates silence at the end of a topic, only the interval between two captured messages. A source that stops for good is therefore invisible to M3c, so the silent-source families F01, F06, F07 and F08 use a bounded silence after which the source resumes. This is also a real limit of the product: a permanent stop is not reported as a symptom today.
