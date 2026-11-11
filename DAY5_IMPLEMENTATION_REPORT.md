# Day 5 implementation report: safe background preparation and recovery

Core rule: **recording reliability is never traded for incident preparation.** Everything below is about what that does and does not
guarantee. Nothing here was run against real ROS 2, real systemd, a physical reboot or a robot; see section 7.

## 1. Audit findings (traced from the code, before editing)

| Termination path | Day 4 behaviour | Problem |
|---|---|---|
| Clean stop / crash / disk-critical in `capture run` or detached `_supervise` | locks released, then preparation runs inline | nothing checked for another capture; a next capture could start mid-copy and share the disk |
| `service-run`, clean SIGTERM | preparation inline before exit | `systemctl restart` waits for the whole copy (up to `TimeoutStopSec`) |
| `service-run`, unclean exit | nothing prepared, a printed hint | no durable record that work is pending |
| Supervisor killed / power loss / reboot | session stays `running`; next service start runs `recover_all` | nothing prepared afterwards, nothing queued |
| Hard kill during preparation | hidden staging left, no record | `incident list` showed only `not_prepared` |

Defects in my own Day 4 code, all fixed: (1) **safety hole:** an interrupted session whose recorder was still alive (orphaned) was packaged,
because only the supervisor was checked; (2) `incident list` printed `integrity=verified` from stored JSON; (3) no `deferred` state.

## 2. Files changed (versus the delivered Day 4 ZIP)

| File | Change |
|---|---|
| `src/physicore_collector/incident.py` | Scheduling gates, `IoGovernor`, live-writer detection, `deferred` queue state, `pending list/run` logic, `register_unprepared`, distinct verification states. Largest change. |
| `src/physicore_collector/checksums.py` | Optional per-chunk `io_hook` + `notify_io`; periodic `fdatasync` in copies **only while a hook is installed**. Unhooked behaviour unchanged. |
| `src/physicore_collector/mcap_reader.py` | One line: the scan's single read wrapper calls `notify_io`, so the scan is a checkpoint. |
| `src/physicore_collector/cli.py` | `incident prepare` options (`--guard-dir`, `--allow-while-recording`, `--max-mib-per-sec`, exit 3); `incident pending list/run`; `list/show --verify` states; `service-run` defers instead of preparing. |
| `errors.py`, `versions.py` | `IncidentDeferredError`; `incident_attempt.json` schema 1.1 (1.0 still read). |
| `tests/test_day5_scheduling.py`, `tests/test_day5_realprocess.py` | 32 library-level and 9 real-process tests. |
| `tests/test_incident.py`, `tests/test_incident_cli.py` | Updated: 5 Day 4 expectations that encoded the superseded behaviour (see section 4). |
| `tests/bagtools.py`, `make_fixtures.py`, `recorder_doubles.py` | Optional payload padding (default 0: committed fixture bytes unchanged) so the stand-in recorder can write large bags. |
| `README.md`, `docs/OPERATIONS.md`, `docs/ARCHITECTURE.md`, `examples/demo_day5.sh` | Policy, commands, limits, demo. |

Unchanged on purpose: supervisor, recovery, export, bag, validation, manifest, capture locking, all existing schemas and the recording engine.

## 3. The eight additional requirements

1. **Scheduling race: bounded, not eliminated.** Closing it would require capture to wait for preparation (or a lock the capture honours), i.e. modifying capture locking and risking exactly the recording failure this feature must avoid. So preparation holds nothing a capture needs. Protocol: gate before starting; then a probe of `.active-capture.lock` at most every 0.1 s at every checkpoint, plus explicit checks between phases and before publishing; a capture that appears makes preparation stop cleanly and queue the session. **Residual overlap: at most 0.1 s plus one read/write/`fdatasync` call**, and only for captures in directories PhysiCore was told about. A capture's recorder writes only after it holds the lock, so a probe after that moment always sees it. Not closed: unlisted directories; kernel write-back after a copy.
2. **Live recordings are never packaged.** Reuses `liveness.assess` and process identity: a recorder that is `running`/`orphaned`/`unknown` defers preparation, including for already-ended sessions. Never stopped automatically: only the explicit `pending run --stop-orphan` / `capture recover --stop-orphan`, which verify pid + start time + boot id (+ group leadership) before any signal. Additionally, a `/proc` scan defers any recording a process holds open **for writing**, with no sidecar needed (an unsupervised `ros2 bag record`); it is checked before copying and again before publishing. `--allow-while-recording` does not override it. Limits: only processes this user may inspect (the count that could not be inspected is recorded in the incident); a recorder whose identity was never recorded and that has not opened files yet is not detectable.
3. **Full I/O path.** Checkpoints now cover: every read of the MCAP scan, every hash chunk, every copy chunk, small-file reads, package re-verification, and verification for `list/show --verify`. A yield raised inside the scan propagates as a deferral and cannot be swallowed into a "corrupt recording" finding (tested with a 64 MiB file: a capture appearing at 8 MiB stops the scan before 12 MiB). Remaining uninterruptible units: one library read (record-sized), one write, one `fdatasync` (bounded to 32 MiB of dirty data while governed), CPU between reads.
4. **Readiness is no longer asserted from stored JSON.** Package states: `previously prepared (not re-verified)` (default; stored result, never called verified), `verified now`, `tampered`, `unverifiable` (also when deferred because a capture is active), alongside the evidence outcome `success`/`degraded` (independent axes). `--verify` reads every package byte: cost proportional to total package size (minutes for tens of GB, untested at that scale). `tampered` means "does not match what was prepared"; it cannot say how.
5. **Source evidence preserved.** No code path writes inside a recording. Tested: byte/mtime-identical sources after deferral, yield, checksum mismatch, ENOSPC (real tmpfs), SIGKILL and SIGTERM mid-copy; stale staging removed under the per-target lock; failed retries leave nothing published.
6. **Service shutdown bounded.** `service-run` never prepares inline; it records `deferred` (a small atomic write with directory fsync) after every ended session and stat-only at start for finished sessions. Measured with a 47 MiB pending bag: start to recording 0.4-0.5 s, SIGTERM to exit 0.1-0.17 s. **Not bounded by this work:** the Day 3 startup reconciliation (`recover_all`) hashes *stale* bags before recording starts; its time grows with stale bag size (idempotent, once per stale session). Real `systemctl restart` and `TimeoutStopSec` were not exercised.
7. **Real failure modes were tested**; the table is in section 6, with executed vs simulated stated per test.
8. **Kept small.** No daemon, no new lock system, no new state format beyond `incident_attempt.json` 1.1. Not built: resumable copies (a yielded copy restarts), throttle flags on `capture` commands (env var only), a timer installer (a documented example only).

## 4. Behaviour changes to know about

- `service-run` no longer prepares after a clean stop (Day 4 did). Two Day 4 tests asserted that and were rewritten to the new contract.
- `incident list`: row `state` `prepared` is now `previously_prepared`; JSON key `artifact_integrity` is now `integrity_at_preparation`; `--verify` adds `verified_now`/`tampered`/`unverifiable` (replacing my interim `verification_failed`). Three Day 4 list/show tests updated. This breaks any script written against the Day 4 JSON; I judged a misleading label worse than a one-day-old format.
- `incident prepare` / `incident pending run` / `show --verify` can exit 3 (deferred); 0/1/2 meanings unchanged. Capture commands' exit codes are untouched.
- `capture run/start` print a `DEFERRED` line (not an error) when preparation is deferred.

## 5. Commands

```bash
physicore-collector incident prepare <file|bag-dir|session-dir> [--profile P] [--out DIR] [--guard-dir D]... [--allow-while-recording] [--max-mib-per-sec N] [--dry-run] [--json]
physicore-collector incident pending list <out-dir>... [--guard-dir D]... [--json]
physicore-collector incident pending run  <out-dir>... [--no-recover] [--stop-orphan] [--max N] [--guard-dir D]... [--max-mib-per-sec N] [--json]
physicore-collector incident list <dir> [--verify] [--allow-while-recording] [--json]
physicore-collector incident show <incident-or-session> [--verify] [--allow-while-recording] [--json]
# environment: PHYSICORE_GUARD_DIRS=/a:/b   PHYSICORE_PREPARE_MAX_MIB_PER_SEC=50
```

Install and test: `unzip physicore-collector-day5.zip && cd physicore-collector-day3 && python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest -q`
(the folder name is kept from earlier days; `pytest-timeout` was used locally and is not required). Demos: `bash examples/demo_day5.sh`, `bash examples/demo_incident.sh`.

## 6. Tests

**Executed on the final tree, serial, each in its own environment:**

| Environment | Result |
|---|---|
| Python 3.12.3 | 684 passed, 3 skipped |
| Python 3.10.22 | 684 passed, 3 skipped |
| Python 3.13.16 | 684 passed, 3 skipped |
| Python 3.10.22, minimum pins (typer 0.24.0, click 8.2.1, pydantic 2.6.0, mcap 1.2.2, PyYAML 6.0, pytest 8.0.0) | 684 passed, 3 skipped |

684 = 575 (Days 1-3) + 68 (Day 4) + 41 (Day 5: 32 library-level, 9 real-process). The 3 skips are unchanged (two need real ROS 2 + `rosbag2_storage_mcap`; one needs non-root). The tmpfs test ran (root can mount here); it skips elsewhere.
`ruff --select F` on the files I wrote: clean (the repo's 9 pre-existing findings elsewhere are untouched).

**Executed with real processes/kernel features:** SIGKILL during copy (staging left, then recovered by retry); SIGTERM during copy (clean cancel, exit 2, staging removed); two real preparers racing on one target (the loser exits with `preparation_in_progress`, nothing corrupted, a late third call is idempotent); recorder alive after supervisor SIGKILL (deferred, never signalled without `--stop-orphan`, then stopped through identity-verified recovery and prepared as `interrupted`/`degraded`); both processes killed (stale `running` recovered and prepared, never `complete`); a **real tmpfs running out of space mid-copy** (errno 28 observed, staging removed, space returned, retry succeeds after remount); a process holding a bag open for writing; a service-run process started/stopped with a 47 MiB pending bag; **a second capture started while preparation of the first is copying (measured)**.

**Simulated (injected or mocked, so weaker evidence):** a capture "appearing" mid-copy in the library-level tests (the lock probe is swapped at a chosen copy), throttle timing (fake clock), `ENOSPC`/`EIO` at the unit level, tampering (bytes edited), `register_unprepared` being stat-only (hashing patched to fail), the stand-in recorder itself.

**Mutation checks:** I broke 8 safety behaviours one at a time (recorder-alive gate, governor yield, pre-start gate, tamper-reported-as-ok, scan checkpoint, pre-copy writer check, post-copy writer check, capture-active precedence); each fails a test. The first run left one mutant alive (removing the pre-copy writer check): the test was passing through the post-copy check and also contained sloppy `or True` assertions. I rewrote it to assert that nothing is copied, added a separate late-writer test, and both layers are now killed independently.

**Bugs found by testing and fixed:** the governor's default probe was bound at definition time so a yield could never fire (found by my own mid-run test); verification guarded the session directory instead of the capture output directory; `pending run --stop-orphan` would have reconciled and signalled while another capture recorded (found by the demo; regression test added); a lint `--fix` of mine briefly altered `render.py`, which I reverted and confirmed byte-identical to Day 4.

## 7. Not tested, and remaining risks

- **Not tested:** real ROS 2/rosbag2; real systemd (unit, `restart`, `TimeoutStopSec`, the example timer); a physical reboot (only "both processes killed"); robot hardware, real sensor rates, a real recorder's write pattern, spinning disks or SD cards; multi-GB bags (largest 64 MiB; `--verify` cost at tens of GB unmeasured); captures started from a *different* user or in unlisted directories; multi-disk setups.
- **Measurements are from a fast container filesystem with a stand-in recorder.** They show the mechanism works, not that a real recorder on a loaded robot is unaffected. 0.05 s yield latency is not a promise.
- **Starvation:** a robot that records continuously has no idle window; the queue grows (visible, durable) and is only drained when someone runs `pending run`. Nothing drains it automatically.
- **Yielded copies restart from zero**; repeated yields can waste I/O (the throttle and idle-only drain mitigate, they do not remove).
- The race residual, the unlisted-directory blind spot, the `/proc` permission limit, and the Day 3 startup-reconcile cost (section 3) are real.
- Exit code 3 and the changed `list` JSON are compatibility changes (section 4).

## 8. Measurements (`PHYSICORE_STRESS_REPORT`, final tree, 47 MiB bag, preparation throttled to 20 MiB/s so it stays running)

| Scenario | Result |
|---|---|
| B started while A's preparation was copying | B start 0.32 s; A yielded 0.05 s after B was up; A `deferred` with "started while this was running"; no staging, no incident, A's bytes identical; B kept recording |
| B started immediately after A's stop | B start 0.32 s; A deferred (before or at its first checkpoint) |
| Then `incident pending run` (unthrottled) | both prepared in about 0.3 s (page-cache warm); A `attempt_count` 2, first-attempt time preserved; both `verified now` |
| `service-run` start/stop with a 47 MiB unprepared session present | start to recording 0.4-0.5 s; SIGTERM to exit 0.1-0.17 s; old session queued `deferred_stale_session`, new one `deferred_service_mode` |

## 9. Demonstration (`bash examples/demo_day5.sh`, actual output, trimmed)

```
### 1. clean capture            state: STOPPED_CLEAN   Incident package prepared: SUCCESS -> .../clean/incident   [exit 0]
### 2. supervisor killed, recorder keeps running
crashed  NEEDS RECONCILE  capture=running/interrupted  recorder=orphaned  attempts=0  BLOCKED(recorder_may_be_writing)
### 3. incident prepare while the recorder may still write
DEFERRED: The recorder of 'crashed' may still be writing to the bag (recorder: orphaned). Nothing was prepared.   [exit 3]   recorder still alive: yes
### 4. a new capture is recording; pending run
crashed  DEFERRED  a capture is active; nothing is reconciled, stopped or prepared while it records   [exit 3]
### 5. live capture stopped; pending run --stop-orphan   -> Reconciling 'crashed' ... Preparing 'crashed' ... Preparing 'live' ...   [exit 0]
### 6. # Incident crashed: DEGRADED   Capture health: INTERRUPTED (... the surviving recorder was stopped gracefully by `capture recover`)
clean    VERIFIED NOW  evidence=SUCCESS   capture=finalized
crashed  VERIFIED NOW  evidence=DEGRADED  capture=interrupted      <- intact bytes, interrupted capture: never relabelled complete
live     VERIFIED NOW  evidence=SUCCESS   capture=finalized
```
