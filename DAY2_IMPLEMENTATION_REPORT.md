# Day 2 implementation report

## Summary

Day 2 adds, on top of the unchanged Day 1 pipeline: (1) complete **rosbag2 bag-directory** support (multi-segment MCAP, `metadata.yaml` cross-checking, explicit handling of every way a bag can be incomplete); (2) a thin **capture supervisor** around `ros2 bag record` with a versioned **capture-health sidecar**; (3) **offline `package verify`**; (4) a **landscape/reuse audit** (nothing reused) and a **snapshot feasibility** analysis (not implemented, with an executable but unexecuted experiment).

## IMPORTANT: what was and was not verified

| Claim | Evidence |
|---|---|
| Offline bag/MCAP/package behaviour works | **Executed.** 467 tests pass, 2 skipped, on Python 3.10.22 / 3.12.3 / 3.13.16 and at the minimum declared dependency pins (3.10), 3 consecutive runs each (12/12 green). Day 1's 346 tests are untouched and still pass. |
| Tests can actually fail | **Executed.** 17 hand-written mutants of the safety-critical logic (bag readability, missing/unsafe/symlink segments, count checks, finalization/exit-state labelling, kill-after-timeout, process group, option-like topics, hash verification, overwrite protection, recorder-loss handling): all 17 caught. |
| Supervisor lifecycle (start/stop/timeout/kill/crash/no-output/partial-output/overwrite/Ctrl+C) | **Executed against a scripted recorder double and against a real subprocess** (a stand-in `ros2` script that writes a synthetic bag and handles SIGINT), including process-group kill with no surviving grandchild, and a genuine two-process `capture run` / `capture status` / `capture stop`. |
| **Works with real ROS 2 / real rosbag2** | **NOT VERIFIED.** No ROS 2 was available. Real-recorder behaviour (startup time, exit code on SIGINT, what metadata.yaml contains for real bags, loss-line wording in your distro, MCAP storage plugin naming) is unproven. The synthetic bags follow the `metadata.yaml` layout read from rosbag2's source, which shows the Collector reads *that layout*, not that rosbag2 produces files it accepts in every version. `tests/ros2_integration/` contains two real-ROS 2 tests (supervisor against real `ros2 bag record`; snapshot experiment); they skip here and were **not executed**. |
| Snapshot feasibility numbers | **None exist.** Analysis is from reading rosbag2 source; the measuring experiment is written but unexecuted. |
| Linux only | Capture uses process groups and `/proc`. No other OS was tested. |

## What changed (Day 1 files touched, all additive)

`versions.py` (new version constants), `schemas.py` (enums, `capture:` profile section, bag/health models, optional manifest fields), `validation.py` (`extra_checks` hook), `session.py` (directory input, `--health`, session-directory resolution), `manifest.py` (bag/capture gaps and sections), `export.py` (bag copy path, symlink refusal, bag coherence check in `verify_package`), `cli.py`, `render.py`, `profile.py` (public alias of the duplicate-key YAML loader), `errors.py`. **No existing test was modified.** New modules: `bag_metadata.py`, `bag.py`, `capture_health.py`, `recorder.py`, `supervisor.py`.

## Behaviour decisions worth knowing

* **Compatibility by construction:** single-MCAP export still writes manifest/report `0.1`, byte-identical in shape to Day 1 (compared against a package produced by the untouched Day 1 code). Day 2 content uses `0.2`.
* **Missing `metadata.yaml` is `partial`, not `failed`**, because segments are still safely inspectable; the message says rosbag2 writes it at close, so this is consistent with an interrupted recording but not proof of one.
* **Unlisted `*.mcap` blocks `complete`; an unlisted notes file is only a warning.** Neither is exported.
* **Recorder-reported loss blocks `complete`.** Found by reading rosbag2's source: it prints a WARN when its cache drops messages. Captured verbatim, never parsed; its absence is explicitly not evidence of no loss.
* **`appears_finalized` is tri-state** and the schema refuses `true` after any non-clean state or non-readable output.
* **A stop that arrives before the recorder can handle SIGINT** is recorded as `stopped_with_error` (killed by signal 2, no output), never clean.

## A flaky test, found and fixed (disclosure)

The real two-process stop test failed ~1 in 6 runs on the first full-matrix run. Cause: my test sent SIGINT before the stand-in recorder had installed its handler. The supervisor's output was correct. I made the stand-in signal readiness (the test now waits) and added a deterministic test pinning the early-stop behaviour. 25/25 consecutive passes afterwards, then 12/12 full-suite runs. Residual risk: other timing-based real-process tests use generous bounds but could be flaky on a heavily loaded machine.

## Known limitations

* `capture run` is a foreground process; `capture stop` signals it (pid + `/proc` command-line check against pid reuse). No daemon, no remote control.
* Segment-order check uses strict log_time monotonicity across segments and is a warning only; real recorders with out-of-order timestamps could trigger spurious warnings.
* Unsupported (reported, not read): `sqlite3` storage, `compression_mode: FILE`, metadata versions outside 3..9. Per-file and per-topic count comparison needs metadata version >= 5 for per-file.
* Duplicate-segment detection compares normalized path strings; two names for one file via hard links are not detected.
* `bag_directory_name` is the resolved directory name; a session's bag is therefore named `bag` in packages.
* Memory: only `log_time` values (8 bytes/message) are retained; verified peak Python allocation stays far below file size for a ~40 MB segment, but a recording with billions of messages would still need proportional RAM for cadence analysis (unchanged from Day 1).
* No signatures or encryption (audited, deferred).

## Recommended next steps (priority order)

1. Run `tests/ros2_integration` on a real ROS 2 machine (target distro, MCAP plugin) and fix whatever the real recorder disagrees with, **before** trusting `capture run` in the field.
2. Run the snapshot experiment; decide on the five prerequisites in `docs/SNAPSHOT_FEASIBILITY.md`.
3. Decide whether `capture run` should become a managed background service (needed for unattended operation).
4. Scope signing/trust (key management first), then reconsider the Flightcase envelope ideas.

## How to reproduce

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest
# with ROS 2 sourced, also: pytest tests/ros2_integration -m ros2_integration -s
```
