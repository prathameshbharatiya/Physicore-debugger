# M3c — test report

Everything below was **executed** in this work copy (local git branch `m3c-baseline`, baseline commit `95a6b2a`; nothing pushed). Where a test is specified but not executed, it is
listed under "Not executed". Synthetic data only.

## Results

| Suite | ros2 venv (decoder + rosbags) | plain venv (no decoder, no rosbags) |
|---|---|---|
| Accepted baseline, before M3c (step 0) | 1173 passed, 5 skipped | 1109 passed, 69 skipped |
| Whole repository after M3c step 9 and the final memory fix | **1704 passed, 6 skipped** | **1603 passed, 107 skipped** |
| of which M3c tests (`tests/anomaly`, collected) | 532 | 532 (more skipped: CDR cases need the decoder) |

All accepted suites pass unmodified and with counts at or above the baseline. In the plain venv the CDR tests skip; JSON payloads never need the decoder (X-18 is demonstrated by
the plain-venv run itself, and X-17 runs only there). One accepted real-process test (`test_day5_realprocess::test_sigterm_during_copy_…`) failed once while both suites ran
concurrently on a loaded machine and passed three times in isolation and in the next full run; no accepted code is involved. Suites were afterwards run sequentially.

### Mutation testing

306 hand-written mutants (textual replacements applied to a copy of `src/`) across steps 1–9; control (unmodified) passes. **Final run (after the last source change): control PASS, 306 killed, 0 survived, 0 invalid.** Mutants that were equivalent or had stale patterns during development were replaced or removed (not left as survivors): Step 7 had five such cases
(equivalent: a scalar accepted as array is still blocked at run time by the same reason code; `bool` already falls through `classify`; JSON never has a schema width so a bit-info flag
mutant changes nothing; two ill-formed mutants). The CS-04j finding-id mutants m1 (ignore artifacts), m2 (first artifact only), m3 (path instead of digest) are all killed (plus m4–m6).

### Gates per step (all met)

| Step | Content | Gate evidence |
|---|---|---|
| 1 | clock, findings, config, severity, exit codes, report skeleton, FX-ART fixtures | green; PRES green (suite counts for this step were not recorded in my notes) |
| 2 | access layer, constants, runner | 1303 / 1236 passed |
| 3 | Tier A log + diagnostics | 1405 / 1332 |
| 4 | Tier A goal status + battery | 1473 / 1395 |
| 5 | gap + rate | 1538 / 1460 |
| 6 | stamp order | 1588 / 1507 |
| 7 | array length + non-finite | 1655 / 1554; 47/47 step mutants |
| 8 | finding-identity hardening, CS-04a–j | 1664 / 1563; 6/6 |
| 9 | CLI, reports, replay verifier | 1704 / 1603; 26/26 |

(each pair: ros2 passed / plain passed.)

### Preservation (PRES)

* PRES-01: accepted suites pass unmodified, counts above.
* PRES-02–04 / PRES-06: golden/byte-identity tests; the baseline manifest `tests/anomaly/baseline_files.sha256` pins every accepted file; only `src/physicore_collector/cli.py` differs (+29 lines, one added command). Verified by `git diff --stat`.
* PRES-05 / X-23: `--help` of `evidence inventory`, `investigate`, `decode` and the top level are **byte-identical** to the baseline (golden files generated from commit `95a6b2a`); the `evidence` group differs only by the new command line; existing exit codes re-checked.
* PRES-07/08/09: no new mandatory dependency; `cdr.py` remains the only importer of the third-party decoder; importing `physicore_collector.investigator` and `physicore_collector.cli` does not import the anomaly package (the command imports it lazily).
* PRES-11: the pinned deliverables re-verified unchanged: `m3b-example-rev2.zip cf0c431c…`, `physicore-collector-m3b-rev2.zip fc2117bb…`, `m3b-rev2.patch 5b67b333…`, `M3B_EVENT_RESPONSE.md e9885c32…`, `M3B_TEST_REPORT.md 7593bb8b…` (all five match `BASELINE_DELIVERABLES.sha256`); the M3a zip starts `d4c955d1…`.

### Replay verifier (X-03 / X-05)

`tests/anomaly/tools/replay_verifier.py` re-reads the artifact files with the plain `mcap` reader and, for CDR, the independent `rosbags` serializer. On a multi-detector recording
every retained observation of every detector replays (file digest, message at index, topic, log time, value at the field path, payload stamp), including a CDR recording. The verifier
itself is tested (X-05): corrupted index, log time, topic, digest, value, and stamp are each rejected.

### Exit codes (X-19 a–i)

Each row of the exit-code table has its own test, run through the real command; X-25 asserts the report's `exit_code` equals the process exit code over exit values 0, 1, 2 and 3.
Tampered evidence is a manifest-bearing incident package with one byte flipped and the manifest untouched.

## Benchmarks (recorded, not gated)

Measured in fresh subprocesses (peak RSS from `ru_maxrss`; one run each, on a shared cloud container, so absolute numbers are indicative only). Input: one topic, one segment, a
streaming-written fixture; 1000 NaN findings among the 1M messages. `load` = accepted inspection and loader only.

| Messages | Payload | Scenario | inspect + load (s) | detect (s) | peak RSS after load (MB) | peak RSS at end (MB) | RSS added by M3c (MB) | bytes / message |
|---:|---|---|---:|---:|---:|---:|---:|---:|
| 100k | JSON | gap + rate (no decode) | 0.85 | 0.15 | 77.7 | 80.3 | 2.6 | ~26 |
| 100k | JSON | non-finite (decodes all) | 0.83 | 0.80 | 77.7 | 87.8 | 10.1 | ~100 (before fix) |
| 1M | JSON | gap + rate (no decode) | 9.8 | 1.8 | 356.2 | 431.7 | 75.5 | **~75** |
| 1M | JSON | non-finite (decodes all) | 9.4 | 8.0 | 356.2 | 419.6 | 63.4 | ~63 |
| 1M | JSON | stamp order (decodes all) | 9.6 | 7.2 | 356.3 | 419.7 | 63.4 | ~63 |
| 1M | CDR | gap + rate (no decode) | 10.0 | 1.7 | 357.1 | 432.9 | 75.8 | ~76 |
| 1M | CDR | non-finite (before fix) | 10.2 | 7.6 | 357.0 | 497.7 | 140.7 | ~141 |

Throughput of the detectors on top of loading: about 125k messages/s for decoding detectors on JSON and about 130k messages/s for CDR on this host, over 500k messages/s for gap and
rate. The accepted loader dominates (about 100k messages/s).

**Honest memory finding.** The design budget (X-12, DESIGN §7.2) was "about 16 bytes per message per timing topic plus findings". The retained store is exactly that (a test pins it),
but the **measured peak** for gap + rate at 1M messages is about 75 bytes per message over the loader, because of transient interval lists built when the findings are computed.
This **exceeds the stated budget**; it is reported, not hidden. Decoding detectors measured about 140 bytes per message at first because a diagnostic per-message decode tally grew with
the recording; production runs now switch that tally off (63 bytes per message afterwards; the tally stays on in the tests that assert "decoded at most once"). Memory is still
dominated by the accepted loader (about 356 bytes per message at 1M). No streaming loader was built (out of scope by decision); the 2M-message guard of the accepted loader applies
unchanged.

## Not executed / outstanding

* Real-recording validation: **not done**. All fixtures are synthetic; nothing shows these detectors behave sensibly on real robot data.
* Decoder licensing and redistribution review (mcap-ros2-support): **outstanding**; the decoder is an optional extra and is not bundled.
* ROS documentation verification of constants and field types: constants and types were checked against the rosbags typestore only; independent confirmation against ROS documentation remains outstanding.
* float32 NaN payload bits cannot be recovered from the decoder (pinned by NF-02c); only class and sign are reported.
* X-04 variants beyond the single byte-flip (missing segment inside a package, digest-only mismatch, count mismatch) are covered through the inventory's own behaviour (exit 1) and through X-06 for incompleteness; each specific tamper kind is not separately enumerated.
* X-13 "guard behaviour at the 2M-message limit identical to M3b" was not run at 2M messages (the guard is the accepted loader's; not exercised here).
* The memory budget of X-12 is **not met** at peak (above).
