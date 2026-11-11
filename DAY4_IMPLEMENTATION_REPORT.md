# Day 4 implementation report: automatic incident preparation

Scope: make PhysiCore prepare an investigation-ready incident package automatically when a capture ends, and on demand for
recordings that already exist. Nothing outside that scope was built (no diagnosis, divergence detection, simulation, clustering,
cloud, custom recorder, or snapshot capture).

## 1. Files changed

| File | Change |
|---|---|
| `src/physicore_collector/incident.py` (new) | The whole feature: source discovery, status derivation, preparation, retry/idempotency, list/load/verify, automatic-after-capture entry point. Orchestration only. |
| `src/physicore_collector/cli.py` | New `incident prepare / list / show`; `--no-auto-prepare` on `capture run`, `start`, `service-run`, `install-service`; post-capture hooks in `capture run`, `_supervise`, `service-run`. About 240 changed lines. |
| `src/physicore_collector/service.py` | `ServiceSpec.auto_prepare` (default `True`); the unit passes `--no-auto-prepare` only when it is `False`. Default unit text is byte-identical to before. |
| `src/physicore_collector/errors.py` | `IncidentError` (+ `IncidentConflictError`, `IncidentRefusedError`, `InsufficientSpaceError`, `NoRecordingError`), each with a stable machine-readable `code`. |
| `src/physicore_collector/versions.py` | `INCIDENT_SCHEMA_VERSION = "1.0"`, `ATTEMPT_SCHEMA_VERSION = "1.0"`. |
| `tests/test_incident.py`, `tests/test_incident_cli.py` (new) | 68 tests (53 library level, 15 CLI/lifecycle). |
| `examples/demo_incident.sh` (new) | Reproducible demo (section 8). |
| `README.md`, `docs/OPERATIONS.md`, `docs/ARCHITECTURE.md` | New workflow, commands, storage, retry, opt-out, what results prove. |

No existing test, schema, fixture, or data format was modified. The supervisor, export, bag, validation, manifest and capture-health
modules are untouched.

## 2. What already existed and was reused (unchanged)

- **Discovery and ordering:** `bag.scan_bag` (metadata order, else observed `log_time`; unsafe paths rejected), `session.resolve_recording_input` (session dir to bag + sidecar).
- **Validation, gaps, coverage:** `analyze_session`, `validation`, `manifest.build_known_gaps`, `build_loss_coverage`, the `topic_present:* / cadence:* / metadata:* / capture:*` checks, including `capture:output_unchanged` (bag vs. digests the supervisor hashed at capture end).
- **Capture health:** the sidecar and `liveness.assess` (read-only judgement; also used to refuse a still-active capture).
- **Copy / hash / verify / atomic publish / never overwrite:** `export.export_package`, `verify_package`, `checksums.copy_and_hash`.
- **Locking and atomic writes:** `sessionlock.FileLock`, the sidecar's write-then-rename pattern.
- **Existing status vocabulary:** `complete | partial | failed` is kept as the *validation* status.

Missing before this sprint: nothing ran after a capture; no incident-level result separating the four concerns; no promotion of a segment file to its bag;
no storage preflight; no idempotent retry or conflict handling for a prepared target; no list/show; no opt-out.

## 3. New behaviour

- **Automatic preparation** after the capture ended, its final record is written and its locks are released (foreground, detached, and service after a *clean* stop). Output: `<session>/incident/{package/, incident.json, INCIDENT_SUMMARY.md}`; last attempt in `<session>/incident_attempt.json`.
- **Existing recordings:** `incident prepare` accepts an `.mcap`, a rosbag2 directory, a directory of loose segments, a capture session, or a session's `bag/`. A segment listed by its neighbouring `metadata.yaml` is promoted to the whole bag (recorded in the result); an unrelated lone file is prepared alone with a note naming the neighbours that were *not* included.
- **Four separate statements** in `incident.json` (artifact integrity, capture health, evidence coverage, preparation), plus `outcome: success | degraded`. Missing capture health is `unknown`, never healthy. A degraded or interrupted recording stays labelled; valid checksums never upgrade it (tested).
- **Safety:** source only read; staging + one rename; Ctrl-C/SIGTERM clean up; never overwrites; idempotent on an unchanged source (re-verifies first); different or tampered target = conflict; per-target lock; stale staging of a killed run removed under that lock; output inside the recording refused; free space (copy + 1 MiB + profile `disk_critical_free_bytes`) checked before copying; streaming copy.
- **Package form:** self-contained byte copy only. `package/` is the unchanged export package (`package verify` works on it); the new files sit beside it because `verify_package` rejects unlisted files by design.
- **Exit codes** (existing contract): 0 success, 1 degraded (package written), 2 failure (nothing published). Capture commands keep their own exit codes; preparation never changes them.
- **Opt-out:** `--no-auto-prepare`.

## 4. Commands

```bash
physicore-collector incident prepare <file.mcap | bag-dir | session-dir> [--profile P] [--out DIR] [--dry-run] [--metadata M] [--trigger T] [--health H] [--json]
physicore-collector incident list <dir> [--json]
physicore-collector incident show <incident-dir | session-dir> [--verify] [--json]
physicore-collector package verify <incident>/package
physicore-collector capture run|start <profile> --out DIR [--no-auto-prepare]
physicore-collector capture install-service ... [--no-auto-prepare]
```

## 5. Tests actually executed

| Environment | Result |
|---|---|
| Python 3.12.3, parallel (`-n 4`) | 643 passed, 3 skipped |
| Python 3.12.3, serial, final tree | 643 passed, 3 skipped (70 s) |
| Python 3.10.22, current pins | 643 passed, 3 skipped |
| Python 3.13.16, current pins | 643 passed, 3 skipped |
| Python 3.10.22 at the declared **minimum** pins (typer 0.24.0, click 8.2.1, pydantic 2.6.0, mcap 1.2.2, PyYAML 6.0, pytest 8.0.0) | 643 passed, 3 skipped |

Baseline before changes: 575 passed, 3 skipped, so +68 new, 0 regressions. The 3 skips are unchanged: two need real ROS 2 + `rosbag2_storage_mcap`; one needs a non-root user.
`ruff check --select F` on the new and changed files: clean (the repo's 9 pre-existing pyflakes findings elsewhere are untouched; the repo has no ruff configuration, so default-rule output, 153 findings, was not used as a gate).

New tests cover: standalone MCAP, rosbag2 directory, multi-segment, segment-to-bag promotion; ordering (metadata vs. file names, inferred from `log_time`, reproducible); missing metadata, missing segment, inconsistent counts; truncated / corrupted / not-MCAP input;
interrupted, recorder-loss and bag-changed-since-capture sessions; active-capture refusal; conflicts (different source, non-incident target of 3 kinds, tampered target, output inside recording, concurrent preparation); checksum mismatch, ENOSPC mid-copy, insufficient space (and the profile reserve), KeyboardInterrupt,
stale staging; failed-attempt record, listing and retry; `auto_prepare` never raising; preparation starting only after locks are released; dry-run writes nothing; source byte/mtime-unchanged snapshots; a 64 MiB recording streamed with peak traced memory about 3 MiB (assertion: under a quarter of the file; see limits);
no network (sockets patched to fail); frozen Day 1 package and Day 2 session still prepare; real-process lifecycle (detached start/stop, foreground run, `service-run` clean stop and unclean end, `--no-auto-prepare`, `install-service --no-auto-prepare`) against the stand-in recorder.

Mutation check: I broke six behaviours one at a time (liveness refusal, space check, outcome derivation, fingerprint conflict check, staging cleanup, stale-staging removal); each made at least one test fail; the source was restored byte-identical.
Bugs the tests found in my own code and fixed: `typer.echo(flush=...)` crashed the detached supervisor after the capture ended (recording safe, but wrong); `failure_info` classified an export-wrapped I/O error as `export_failed` instead of `io_error`.

## 6. Not executed, and why

- **Real ROS 2 / rosbag2 / `rosbag2_storage_mcap`:** not installed here; the 2 real-ROS tests are skipped. All recorder behaviour is a stand-in script. Nothing here shows the feature works against a real recorder's output beyond the synthetic and frozen bags.
- **Real systemd:** `service-run` ran as a process with SIGTERM; no unit was installed or started; `TimeoutStopSec` behaviour is untested.
- **Real robot, real disk pressure:** disk-full is simulated (injected ENOSPC, patched free space), not a full filesystem. No throughput or I/O-contention measurement.
- **A real `kill -9` during copying:** stale staging is tested by creating a leftover directory, not by killing a live preparation.
- **Multi-GB recordings:** the largest test is 64 MiB; `tracemalloc` sees Python allocations only, not OS page cache or the `mcap` library's native buffers.
- **Non-root permission failures:** the existing root-skipped test is still skipped, and I added none that needs non-root.

## 7. Known limitations and remaining risks

1. **No reference-only mode.** Every incident is a full copy (and each file is read about four times: analysis, copy, re-hash, verification). On a small robot disk this is heavy; the preflight fails cleanly rather than filling the disk, and `--out` can point elsewhere.
2. **I/O contention:** preparation is unthrottled and unbounded in time. After a foreground/detached capture, starting the next capture while it runs can make the new recording drop messages. Mitigation is operational (`--no-auto-prepare`), not enforced. Interruptible and retryable, but there is no time budget.
3. **Service mode prepares only after a clean stop.** After an unclean end (restart expected) it prints the command instead, so the very recordings most worth preparing are not auto-prepared under systemd. This was a deliberate trade against delaying the restart; it may surprise. Under a systemd stop, a long preparation is killed at `TimeoutStopSec` (unverified here) and must be retried.
4. **`success` can coexist with `capture_health: unknown`** (a bag without a sidecar), matching how `export` already treats such bags. The unknown is always printed and stored, but a script reading only the exit code will not see it; use `--json`.
5. **Evidence coverage is limited to what the profile expects.** "Observed" means at least one message; time intervals are assessed only through cadence rules; no sequence/DDS-level loss analysis exists (rosbag2 exposes none).
6. **`incident.json` contains host paths** (provenance only; nothing is verified through them). The package itself has none. `incident.json` has no self-checksum; its binding is the manifest digest it records, and `incident show --verify` checks that.
7. **Segment promotion is a heuristic** keyed on a neighbouring `metadata.yaml` listing the file. It is reported in the result and notes, but someone who really wanted one segment alone gets the whole bag.
8. **Behaviour change by default:** `capture run/start` now write an extra `incident/` and `incident_attempt.json` into the session directory and take extra time/disk after stopping. Opt-out exists.
9. Hidden `.<name>.prepare.lock` files are left next to targets (harmless; documented). The fingerprint does not include the tool version.
10. A failed **explicit-`--out`** preparation of a non-session input is reported on stderr/exit code only (nothing is written next to a user's recording); only default-location session attempts are recorded in `incident_attempt.json`.

## 8. Reproducible end-to-end demonstration

From the repository root, after `pip install -e ".[dev]"`: `bash examples/demo_incident.sh`. It builds a synthetic 3-segment rosbag2 directory, then (exit codes are the commands' own):

```
### 1. a multi-segment bag: point at ONE segment, get the whole bag
# Incident run12-cc865fc71cf5: SUCCESS
- Source: rosbag2 directory (found via a segment file, promoted to its whole bag)
- Segments: 3 in order (metadata)
- Artifact integrity: VERIFIED. Package files match the manifest (6 artifact(s)); bag vs. digests recorded at capture end: not available.
- Capture health: UNKNOWN (no capture-health record was supplied or found: ...)
- Evidence coverage: EXPECTED SIGNALS OBSERVED; 3/3 expected topic(s) observed; 0 cadence gap(s) observed
[exit 0]
### 2. same command again: idempotent, nothing written
ALREADY PREPARED: an identical, verified incident exists at '.../inc/run12'. Nothing was written.
[exit 0]
### 3. a recording with an observed cadence gap: verified bytes, DEGRADED evidence
# Incident cadence_gap-dd4b2a07f6bf: DEGRADED
- validation status is PARTIAL: required checks failed: ['cadence:/joint_states']
- Artifact integrity: VERIFIED. ...      - Evidence coverage: ... 1 cadence gap(s) observed
[exit 1]
### 4. list and re-verify offline
gap    DEGRADED  validation=partial   integrity=verified  capture=unknown  coverage=expected_signals_observed ...
run12  SUCCESS   validation=complete  integrity=verified  capture=unknown  coverage=expected_signals_observed ...
VERIFIED: package files match manifest.json and incident.json (SHA-256, sizes, no unexpected entries).
### 5. refuses to overwrite
ERROR: A different incident already exists at '.../inc/run12' ... It was not touched.
[exit 2]
```

Automatic path with the stand-in recorder (also covered by `test_detached_capture_prepares_automatically_after_stop`):
`capture start p.yaml --out caps --session-id demo1`, then `capture stop caps/demo1`; shortly afterwards `caps/demo1/incident/` exists (capture health `FINALIZED`, bag vs. capture-end digests `match`), and `incident list caps` shows it.

## 9. Definition of done

An engineer can finish a supported capture, or point at an existing recording, and get a locally prepared package with separate integrity, capture-health, coverage and preparation statements and an explicit list of what is not established, without assembling anything by hand. What remains unproven is behaviour against real ROS 2, real systemd, real disks and robot-level load (sections 6 and 7).
