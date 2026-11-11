# Day 3 implementation report: unattended capture

## Summary

Day 3 makes the Day 2 capture supervisor usable without an operator watching it: detached start, crash/power-loss/reboot
recovery, liveness that does not trust PIDs, heartbeat and disk monitoring, and a systemd service. Day 1 and Day 2 behaviour is
preserved (all prior tests pass; one Day 2 assertion was deliberately updated, see "Compatibility").

## HONEST LIMITS: read this first

| Not verified / not claimed | Why |
|---|---|
| **Real ROS 2 / rosbag2** | None was available. Every recorder in every test is a stand-in script (`tests/recorder_doubles.py`) or a double. How real `ros2 bag record` behaves at start-up, on SIGINT, on its log output, and under load is unproven. |
| **Real systemd start / stop / restart / boot ordering** | systemd is not running in the dev environment (PID 1 is not systemd). Only `systemd-analyze verify` (static) was run on generated units; it is **not** equivalent. Restart rate limiting, `KillMode=mixed` shutdown, sandboxing against a real DDS stack are unexercised. |
| **A real reboot or power loss** | Simulated only: processes were SIGKILLed and a recorded boot id was edited. |
| **Zero message loss / production readiness** | Not claimed. rosbag2 exposes no loss counters; free disk space is not write throughput; the record cannot show that no message was lost. |
| **Race fix confirmation (suite level)** | A parallel-load flake (about 1 in 10 on Python 3.10) was traced to a flaw in a test's measurement and fixed; the test then passed 40/40 under load and the suite 9/9 parallel runs on 3.10. That is suggestive, not statistically conclusive for the whole suite. An earlier background stress loop produced no results (it was killed with its tool call) and is **not** counted as evidence. |
| Platforms | Linux only. Capture uses /proc, flock, process groups. |

Other limits: a host that kills a user's processes at logout (`KillUserProcesses=yes`) can still kill a detached capture (use the
service). The `--duration` clock starts at recorder *process* start (rosbag2 gives no readiness signal), so a very short duration
can interrupt initialization. Free space does not guarantee throughput.

## What was implemented

* **Detached capture:** `capture start` (prepare session + STARTING record, spawn a detached supervisor with no tty, wait until the
  recorder is up), `capture stop` (stop-request file polled by the supervisor; returns only after the supervisor released its locks),
  `capture status` (supervisor / recorder / capture reported separately; never validates evidence), `capture list`.
* **Liveness without PIDs** (`sessionlock.py`, `procident.py`, `liveness.py`): per-session `flock` is the authority for "supervisor
  attached"; recorder identity = pid + /proc start time + boot id; signals are sent only after an identity match (group signals also
  require group leadership); one ACTIVE capture per output directory (second start fails with the active session named).
* **Recovery** (`recovery.py`, `capture recover [--all] [--stop-orphan]`): settles `running`/`starting` sessions without a live supervisor
  into `interrupted`; idempotent; recording only read; end time left unknown; reboot detected from boot ids; an orphaned recorder is
  stopped only on request, gracefully first, only if identity matches. Young `starting` sessions are left alone.
* **Heartbeat / disk** (profile schema 1.2): heartbeat refresh; stale heartbeat => `degraded`; warn/critical free-space thresholds;
  critical => graceful stop, state `stopped_disk_critical` (bag finalized, capture never `complete`); nothing is deleted. The FINAL record
  write is retried and a failure is reported loudly with a non-zero exit.
* **Recorder start-up contract** (`recorder.py`): the recorder is exec'd through a constant trampoline giving it a deterministic signal
  state (SIGINT works even if the parent ignored it; mask cleared; SIGPIPE ignored so a dead supervisor cannot kill it).
* **systemd service** (`service.py`, `ros_env.py`): `install-service` (validated, `--dry-run`, installed disabled unless `--enable`),
  `uninstall-service` (only our marked units; recordings untouched), `service-run` (per-name lock, recover-then-record, exit 78 for
  configuration problems so they are not restarted). ROS environment from `--ros-setup` scripts only (no shell interpolation, refuses
  world-writable/foreign-owned scripts, allow-listed forwarded variables). Each restart records a NEW session.
* **Schemas:** capture-health record 1.1 (1.0 still loads); profile 1.2 (1.0/1.1 load unchanged; new fields omitted from serialization
  while default so older readers keep working on unchanged profiles).

## Architecture

See `docs/ARCHITECTURE.md` ("Day 3 additions") and `docs/OPERATIONS.md` (runbook). Key rule: the lock answers "is a supervisor
attached", identity answers "is that recorder still that process", persisted state + both give the capture assessment
(`starting | healthy | degraded | interrupted | finalized | failed`).

## Files

New source: `procident.py`, `sessionlock.py`, `liveness.py`, `recovery.py`, `ros_env.py`, `service.py`, `__main__.py`.
Changed source: `supervisor.py` (rewritten around prepare/adopt/execute), `capture_health.py`, `schemas.py`, `versions.py`,
`recorder.py`, `cli.py`. Unchanged: all Day 1/Day 2 modules not listed (`bag.py`, `export.py`, `validation.py`, ...).
New tests: `test_procident_locks.py`, `test_day3_supervisor.py`, `test_day3_lifecycle.py`, `test_day3_service.py`;
fixtures `tests/compat/day2_session/`, `tests/compat/day2_profile.yaml` (generated by the untouched Day 2 code).
Changed tests/doubles: `tests/recorder_doubles.py` (stand-in installs its SIGINT handler first, creates its in-progress bag atomically,
models C++ log-write behaviour), `tests/test_profile_capture.py`.
Docs: `docs/OPERATIONS.md` (new), `docs/ARCHITECTURE.md`, `README.md`, this report.

## Compatibility

* Day 2 capture-health 1.0 sidecars load, validate and export (frozen real Day 2 session in `tests/compat/day2_session`). A 1.1 sidecar
  is not readable by Day 2 tooling.
* Profile 1.0/1.1 load unchanged. **One Day 2 test assertion changed on purpose:** the error message for an unsupported profile version
  now lists `['1.0', '1.1', '1.2']`; the test asserts the exact new list (not looser), and 1.2 gating tests were added.
* The sidecar deliberately contains no host paths (it ships inside exported packages); session paths come from where the file lives.

## Bugs found by the tests during Day 3 (and fixed)

1. Final record write lost when the disk fills at the end: now retried, and failure is surfaced (non-zero exit, loud message).
2. Recorder killed by SIGPIPE when its supervisor died (Popen restores SIGPIPE by default): fixed.
3. The first fix (not restoring signals) made recorders inherit "SIGINT ignored" from backgrounded/test-runner parents, silently disabling
   graceful stop: fixed with the trampoline; three hostile-environment tests fail without it.
4. `capture stop` returned before the supervisor released its locks: now waits (deterministic test added).
5. Profile round-trip leaked new fields into manifests Day 2 readers would reject: fixed (default-valued 1.2 fields not serialized).
6. Test-side races (stand-in readiness, non-atomic bag creation, forked status sampling): fixed in the test doubles/tests, not by loosening assertions.

## Test results (current tree)

Environments: Python 3.10.22, 3.12.3, 3.13.16; and 3.10.22 at minimum pins (typer 0.24.0, click 8.2.1, pydantic 2.6.0, mcap 1.2.2,
PyYAML 6.0, pytest 8.0.0). 3.10/3.13 use typer 0.27.3, pydantic 2.14.1, mcap 1.5.0, PyYAML 6.0.3, pytest 8.4.2; 3.12 uses pydantic 2.14.0.

| Run | Result |
|---|---|
| Serial full suite, 3.12 / 3.13 / 3.10 / 3.10 min pins | **575 passed, 3 skipped** each |
| Same four after the final lint-only source edit | 575 passed, 3 skipped each |
| Parallel (`-n 4`) 3.10, after the test-measurement fix | 9/9 runs 575 passed, 3 skipped |
| Same parallel runs *before* the fix | 2 failures in about 11 runs (the measurement flaw above) |
| Focused stress of the repaired signal-state test, 4-way | 40/40 pass; fails (3) with the trampoline removed |
| Mutation testing of Day 3 safety logic | 25 mutants: 23 killed, 2 provably equivalent, 2 test gaps found and closed (re-killed); recovery state guard verified with a combined mutant |

Skips: 2 need real ROS 2 (`tests/ros2_integration`), 1 needs a non-root user. `systemd-analyze verify` accepts generated units
(it warns only when the `nobody` account is used).

## Exact commands

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
pytest -q                          # serial, ~1 min
pip install pytest-xdist && pytest -q -n 4                          # parallel-load check
systemd-analyze verify --man=no <generated unit>                    # static only
# on a ROS 2 machine:  pytest tests/ros2_integration -m ros2_integration -s
```

## Remaining work (priority order)

1. Commission on a real robot with real ROS 2: `docs/OPERATIONS.md` checklist (start/stop, `kill -9`, power pull, recover). Run `tests/ros2_integration`.
2. Real systemd verification: start/stop/restart, restart rate limit, shutdown ordering, sandbox options against your DDS (relax if topics are invisible).
3. Decide on remote/multi-robot management and on signing/trust (still deferred); snapshot capture remains analysis-only.
4. More stress runs of the whole suite under load to turn "suggestive" into "conclusive" for the race fix.
