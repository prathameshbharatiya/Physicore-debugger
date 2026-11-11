# Operations guide: unattended capture

Audience: an engineer who must run the Collector on a robot without watching it. Everything here
is Linux-only. **Not verified against real systemd or real rosbag2** (see DAY3_IMPLEMENTATION_REPORT.md):
treat the first deployment as a commissioning exercise and run the checks in "Commissioning" below.

## Which mode to use

| Mode | Command | Survives terminal close | Survives crash/reboot | Use for |
|---|---|---|---|---|
| Foreground | `capture run` | no | no | bench work, debugging |
| Detached | `capture start` | yes* | no (recover afterwards) | ad-hoc recordings over SSH |
| systemd service | `install-service` | yes | restarts, recovers | unattended / every boot |

\* Not if the host kills a user's processes at logout (systemd-logind `KillUserProcesses=yes`): use the service.

## Detached capture

```bash
physicore-collector capture start profile.yaml --out /data/captures        # returns when recording
physicore-collector capture status /data/captures/<session>                # healthy / degraded / interrupted ...
physicore-collector capture stop   /data/captures/<session>                # graceful; waits until fully stopped
physicore-collector session validate /data/captures/<session> --profile profile.yaml
physicore-collector export          /data/captures/<session> --profile profile.yaml --out /data/packages/<name>
```

A session directory holds: `bag/` (rosbag2's files), `capture_health.json` (the record),
`profile.yaml` (byte-identical copy of the profile used), `supervisor.log`, `supervisor.lock`,
`stop_request.json` (only after a stop). Only `bag/` and `capture_health.json` go into an exported package.

`status` reports three things separately: **supervisor** (attached?), **recorder** (still running?),
**capture** (healthy / degraded / interrupted / finalized / failed). It never validates the recording.
Exit code 0 = healthy or finalized; 1 = anything else (usable in monitoring probes).

## systemd service

```bash
# 1. a dedicated, unprivileged account and a writable output directory
sudo useradd --system --create-home physicore
sudo install -d -o physicore -g physicore /data/captures

# 2. check what would be installed (writes nothing)
physicore-collector capture install-service --name robot-a --user physicore \
    --profile /etc/physicore/robot-a.profile.yaml --out /data/captures \
    --ros-setup /opt/ros/<YOUR_DISTRO>/setup.bash --ros-setup /home/robot/ws/install/setup.bash \
    --env ROS_DOMAIN_ID=7 --dry-run

# 3. install (still NOT enabled: nothing records at boot yet)
sudo physicore-collector capture install-service ...same options...

# 4. opt in to boot-time recording, and/or start now
sudo systemctl enable physicore-collector-robot-a      # or add --enable to install-service
sudo systemctl start  physicore-collector-robot-a
```

No ROS distribution is assumed: you name the setup scripts. They are sourced once, in a fixed
non-interactive `bash --noprofile --norc`; only the resulting environment goes to the recorder. A setup
script writable by other users, or owned by an unrelated user, is refused. Only these variables can be
forwarded with `--env`: ROS_DOMAIN_ID, RMW_IMPLEMENTATION, ROS_LOCALHOST_ONLY, ROS_AUTOMATIC_DISCOVERY_RANGE,
ROS_STATIC_PEERS, CYCLONEDDS_URI, FASTRTPS_DEFAULT_PROFILES_FILE, ROS_DISTRO. Paths must be absolute and use
only letters, digits and `_ . / @ + -`.

Check, logs, stop:

```bash
systemctl status physicore-collector-robot-a          # the SERVICE (is the supervisor process up?)
journalctl -u physicore-collector-robot-a -f          # supervisor output, startup reconciliation
physicore-collector capture list /data/captures       # the CAPTURES (each session's own state)
physicore-collector capture status /data/captures/<session>
sudo systemctl stop physicore-collector-robot-a       # SIGTERM -> graceful recorder stop -> finalized bag
```

Service state and capture state are different facts: a service can be `active` while its capture is
`degraded`, and `inactive` while the last capture is a perfectly good `finalized` one.

### What the unit does on its own

* `Restart=on-failure`, 10 s apart, at most 3 starts per 10 minutes. **Every restart records a NEW session**;
  nothing is resumed or overwritten, and the previous session is first settled (`interrupted`) by startup reconciliation.
  Exit status 78 (configuration error, another capture active, `ros2` missing) is **not** restarted.
  Use `--restart no` if you would rather investigate every failure by hand.
* One service per output directory records at most one capture at a time; a second `service-run` exits at once.
* `TimeoutStopSec` = profile `stop_timeout_seconds` + 15 s, so systemd never kills the recorder before the supervisor tried to stop it gracefully.
* Hardening used: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, writable only `OUT`, `/tmp` and its
  state directory. **These were not validated against a real DDS stack** (shared memory, discovery): if a recorder cannot see
  topics, relax them before suspecting anything else.

### Uninstall

```bash
sudo physicore-collector capture uninstall-service --name robot-a
```
Stops the service gracefully, disables and removes the unit if (and only if) it carries this tool's marker.
Recordings and sessions are never touched.

## Incident packages

An incident is a self-contained, locally verifiable package plus `incident.json` (see README). This section is the operational contract:
when preparation runs, when it deliberately does not, how to see and retry pending work, and exactly what each status proves.

### Scheduling policy: preparation never knowingly competes with a recording

Preparation re-reads the recording several times and writes a full copy, so it can slow a recorder that shares the disk. The rules:

1. **A capture is "active" when its `.active-capture.lock` is held** (a kernel lock every capture takes in its OUTPUT directory; a crashed capture's lock is released by the kernel). Preparation checks the capture output directory, the incident target's directory, every `--guard-dir`, and every directory in `$PHYSICORE_GUARD_DIRS` (colon-separated).
2. **Different directories are never assumed to be different disks.** Captures in directories you did not list are invisible to this check. List every capture output directory that shares storage with the incident target.
3. **Active capture, not started yet: deferred.** Nothing is written except the queue record. **Capture appears while preparing: it stops cleanly** (staging removed, source untouched) and the session is queued as `deferred`. A stopped copy is not resumed; the retry starts over.
4. **Checkpoints cover the whole I/O path.** Every read of the MCAP scan, every hash chunk, every copy chunk and the package re-verification report to one hook; the guard is probed at most every 0.1 s, and again between phases and just before publishing. While governed, copies `fdatasync` every 32 MiB so the end-of-file flush cannot become a long uninterruptible stall.
5. **The race is bounded, not eliminated.** Capture locking is deliberately unchanged, so preparation holds nothing a capture would have to wait for (blocking a recording would be worse than the problem). A capture can therefore start right after a check; both then touch the disk until the next probe: at most 0.1 s plus one read/write/`fdatasync` call. A capture's recorder starts writing only after it holds the lock, so a probe made after that moment always sees it. Not closed: unlisted directories, and the kernel's own write-back after a copy.
6. **Optional throttle**, explicit: `--max-mib-per-sec N` (or `$PHYSICORE_PREPARE_MAX_MIB_PER_SEC`) caps the bytes PhysiCore moves through its own loops averaged over the run. It reduces contention; **it is not a guarantee that a concurrent recording is safe.** The only guarantee is not running.
7. **Override on purpose:** `--allow-while-recording` (manual commands). It records a note in the incident. It does not override rule 8.
8. **A recording held open for writing is never packaged**, with or without a capture-health record (Linux `/proc` scan, only processes this user may inspect; the incident records how many could not be inspected). Preparation never stops that process.

### When preparation runs

| Entry point | Behaviour |
|---|---|
| `capture run` (foreground) | After the capture ended and its final record is written, locks released: inline, subject to the policy above. Ctrl-C/SIGTERM cancel cleanly. Exit code is the capture's, never the preparation's. |
| `capture start` (detached) | Same, inside the detached supervisor; `capture stop` returns before it finishes. |
| `capture service-run` / systemd | **Never inline** (changed from Day 4): a stop or `systemctl restart` must not wait for a copy and the copy must not compete with the next recording. Every ended session is recorded as `deferred` (stat-only write) and drained with `incident pending run`. Stale sessions found at start are recorded the same way. |
| `incident pending run <out>` | Drains the queue when idle (below). |
| `incident prepare <path>` | On demand for any recording. Exit 3 if deferred. |

`--no-auto-prepare` disables the automatic step everywhere (nothing is queued either; such a session shows as `NOT PREPARED` in `incident pending list`).

### Capture state, preparation state, package integrity: three different things

| Question | Where it is recorded | Values |
|---|---|---|
| How did the capture end? | `capture_health.json` (+ liveness) | `stopped_clean`, `interrupted`, `exited_unexpectedly`, `running` (stale if nobody is attached) ... |
| Is there an incident for it? | `incident_attempt.json` (queue) / `incident/` | `deferred` (on purpose; not an error), `failed` (real failure, with a code), `prepared`; no record = `not prepared` |
| Is the package intact? | `incident show/list --verify` | `previously prepared` (stored result only), `verified now`, `tampered`, `unverifiable` |
| What does the evidence show? | `incident.json` outcome | `success` / `degraded` (+ the four statements) |

They are independent: a capture that was interrupted yields a package that is `verified now` and whose outcome is `degraded`. Checksums passing never relabels an interrupted capture. A session with no `incident/` directory is never described as ready, whatever its record says.

### Pending queue: durable, visible, no daemon

The queue is the set of capture sessions without an incident; each carries its own `<session>/incident_attempt.json` (schema 1.1; 1.0 files from Day 4 are still read). It survives restarts and reboots because discovery is a directory scan.

```bash
physicore-collector incident pending list /data/captures          # kind, capture state, blockers, attempts, next action (read-only)
physicore-collector incident pending run  /data/captures          # drain, oldest first, only while no capture is active
physicore-collector incident pending run  /data/captures --stop-orphan   # explicit: also stop a leftover recorder (identity-verified)
```

Kinds: `DEFERRED`, `FAILED` (`NEEDS ATTENTION` when a plain retry cannot work: bad input, no profile, target in the way), `UNPREPARED`, `NEEDS RECONCILE` (record says `running` but no supervisor is attached). Blockers: `capture_active`, `recorder_may_be_writing`. `pending run` exit codes: 0 nothing pending or all prepared (a degraded incident is a result, not an error), 2 a failure or a session needing an operator, 3 something deferred.

**It is a command, not a daemon: nothing runs it for you.** A robot that records continuously never has an idle window, so the queue then grows and is visible but is not drained until you run the command. Run it by hand, from cron, or from a timer. Example (written, **not tested with real systemd**):

```ini
# /etc/systemd/system/physicore-pending.service
[Service]
Type=oneshot
ExecStart=/usr/local/bin/physicore-collector incident pending run /data/captures --max-mib-per-sec 50
SuccessExitStatus=3          # 3 = deferred because a capture is active: retried on the next tick
# /etc/systemd/system/physicore-pending.timer
[Timer]
OnBootSec=10min
OnUnitInactiveSec=30min
[Install]
WantedBy=timers.target
```

### Interrupted and stale sessions

- `capture recover` and the service's startup reconciliation are unchanged (they hash the bag once, idempotently, and only signal a recorder whose recorded identity still matches). Startup reconciliation runs before the service records and its time grows with the size of *stale* bags: that is Day 3 behaviour and is not bounded by this work.
- `pending run` reconciles a stale `running` session with that same logic, then prepares it. Result: `capture_health: interrupted`, outcome `degraded`, never `complete`.
- **A recorder still alive blocks everything.** If its supervisor died but the recorder lives, the session is `BLOCKED(recorder_may_be_writing)`: `incident prepare` exits 3, `pending run` leaves it alone. PhysiCore stops it only with the explicit `pending run --stop-orphan` or `capture recover --stop-orphan`, which verify the process identity (pid + start time + boot id + process-group leadership) before any signal.
- A recorder whose identity was never recorded cannot be recognised by identity; the open-file scan (rule 8) is then the only safeguard, and it only sees processes this user may inspect.
- Nothing here ever deletes or modifies a source recording; hard-killed preparation leaves a hidden staging directory that the next run removes under the per-target lock.

### Inspect, list, verify

```bash
physicore-collector incident list /data/captures            # package state per row; stored results are NOT shown as verified
physicore-collector incident list /data/captures --verify   # re-hash every package now
physicore-collector incident show <session-or-incident>            # stored result, labelled as such
physicore-collector incident show <session-or-incident> --verify   # VERIFIED NOW / TAMPERED / UNVERIFIABLE
physicore-collector package verify <incident>/package
```

`PREVIOUSLY PREPARED (not re-verified)` is the stored record, nothing more. `--verify` reads every package byte: cost is proportional to total package size (minutes for tens of GB) and it is itself skipped while a capture is active (`UNVERIFIABLE`, exit 3) unless `--allow-while-recording`. `TAMPERED` means "does not match what was prepared" (modified, truncated, replaced, damaged or extended; the check cannot tell which); `UNVERIFIABLE` is never a pass.

### Retry (safe, idempotent)

Run the same command again, or `incident pending run`. A failed or deferred attempt published nothing. An identical existing incident is re-verified and reported `already prepared`; a different or tampered target is a conflict and is never touched (use a new `--out`). Codes: `deferred_capture_active`, `deferred_recorder_alive`, `deferred_service_mode`, `deferred_stale_session`; `insufficient_space`, `checksum_mismatch` (the source changed while it was copied), `io_error`, `interrupted`, `target_exists`, `no_recording_files`, `preparation_in_progress`, `input_error`, `internal_error`.

### Storage

The package is a **self-contained byte copy**; a reference-only package is not implemented. Space needed = bytes to copy + 1 MiB + the profile's `capture.disk_critical_free_bytes` (0 if unset), checked before copying; exhaustion *during* the copy (another writer filled the disk) is also handled: staging is removed, nothing is published, the source is untouched. Each file is read about four times (scan, hash, copy, re-hash/verify) and written once. The default location is on the capture disk: on a small disk use `--no-auto-prepare` and prepare to external storage (`--out /mnt/usb/incident`), listing the capture directory with `--guard-dir`.

### What the results prove

*Verified now* means the package bytes equal what was prepared (and, with a capture-health record, what the supervisor hashed at capture end). It does not show the data is true, the recording complete, or that no message was lost. *Finalized* means a graceful stop and a consistent bag, not that every sensor delivered everything (rosbag2 exposes no loss counters). *Unknown* is never read as healthy. Evidence coverage concerns the profile's expectations and cadence gaps only. **Not verified:** behaviour on real ROS 2, under real systemd, on a robot, or under real sensor load; the scheduling guarantees above are as strong as the lock probe and the listed limits, no stronger.

## After a crash, power loss or reboot

1. `physicore-collector capture list /data/captures` shows which sessions are `running` with no supervisor
   (`capture=interrupted`) and which recorders survived (`recorder=orphaned`).
2. `physicore-collector capture recover /data/captures --all` settles them (read-only for recordings). An orphaned
   recorder is reported and **left running** unless you add `--stop-orphan` (graceful stop first; killed only after the
   profile's stop timeout; only a process whose recorded identity still matches is ever signalled).
3. The service does steps 1 and 2 (with `--stop-orphan`) by itself at every start.
4. What you can claim afterwards: the session is `interrupted`; its bag is still validated/exportable
   (`session validate` reports `partial`: `capture:lifecycle` fails; `bag:*` checks tell you whether `metadata.yaml` exists
   and whether the segments are readable). The end time is **unknown**, not "when recovery ran".

## Disk space

Set in profile schema 1.2 under `capture:` (`disk_warn_free_bytes` > `disk_critical_free_bytes`). Below *warn* the capture
is reported `degraded`; below *critical* the supervisor stops the recorder **gracefully** and the session ends as
`stopped_disk_critical` (bag finalized, capture ended early, never `complete`). Nothing is ever deleted. Free space is not
throughput: a slow disk can make rosbag2 drop messages long before a threshold fires; recorder loss warnings are kept verbatim
in the record when present, and their absence proves nothing.

## What evidence exists, and what it does and does not prove

`capture_health.json` records: lifecycle state and ordered events; process identities; heartbeat, disk and stop-reason;
recorder exit status and output tail; SHA-256 of every bag file at capture end; reconciliation details if recovered.
All timestamps are the host's wall clock, **not** established comparable with MCAP `log_time`. The record contains no
host paths and only two environment names (`ROS_DISTRO`, `RMW_IMPLEMENTATION`). It cannot show that no message was lost.

## Commissioning checklist (do this once per robot)

1. `capture install-service ... --dry-run`, then install, **without** `--enable`.
2. `systemctl start`, wait, then `capture status` shows `HEALTHY` and `session validate` on the session. Confirm the bag has the
   topics you expect (this is the first test against the real recorder in your environment).
3. `systemctl stop`; confirm `FINALIZED` and `metadata.yaml` present.
4. `kill -9` the supervisor (`capture status` shows the pid) while recording; confirm the orphan is reported and
   `capture recover --stop-orphan` finalizes it. Pull power once on a test unit and confirm `recover` after boot.
5. Only then `systemctl enable`.
