# PhysiCore Collector

Local-first robotics evidence acquisition and packaging tool.

**What it does.** It inspects a recording (a single `.mcap` file **or a complete rosbag2 bag
directory**), validates it against a versioned robot profile, and exports a portable, checksummed
evidence package that can be re-verified offline. **Day 2** adds a thin supervisor that starts the
supported recorder (`ros2 bag record`), stops it safely, and writes a machine-readable
capture-health record. **Day 3** makes that capture reliable when unattended: it can run detached from a
terminal, survive (and recover from) supervisor crashes, power loss and reboots, watch disk space, and run
as a systemd service. **Day 4** prepares an investigation-ready **incident package** automatically when a
capture ends, and on demand for any recording that already exists on disk. It does **not** contain a recorder of its own, capture triggers or snapshots,
use the cloud, diagnose faults, or provide a viewer. Offline commands never need ROS 2.

Design and responsibilities: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Reuse decision:
[DAY2_LANDSCAPE_AND_REUSE_AUDIT.md](DAY2_LANDSCAPE_AND_REUSE_AUDIT.md). Snapshot capture analysis:
[docs/SNAPSHOT_FEASIBILITY.md](docs/SNAPSHOT_FEASIBILITY.md). What was and was not verified:
[DAY2_IMPLEMENTATION_REPORT.md](DAY2_IMPLEMENTATION_REPORT.md). Running unattended:
[docs/OPERATIONS.md](docs/OPERATIONS.md). Day 3: [DAY3_IMPLEMENTATION_REPORT.md](DAY3_IMPLEMENTATION_REPORT.md). Day 4: [DAY4_IMPLEMENTATION_REPORT.md](DAY4_IMPLEMENTATION_REPORT.md). Day 5: [DAY5_IMPLEMENTATION_REPORT.md](DAY5_IMPLEMENTATION_REPORT.md).

## The rules this tool never breaks

1. **Observed evidence is kept apart from unknowns.** "No gaps found" is never reported as "no data lost".
   Every result and manifest carries `loss_detection_coverage` (with `no_loss_claim_permitted: false`,
   enforced by the schema) and a non-empty `known_gaps` list.
2. **A cadence gap is timing evidence, not proof of DDS message loss.** It only says two consecutive
   recorded `log_time` values were further apart than the threshold.
3. **A trigger timestamp is compared with MCAP `log_time` only when their clock compatibility is
   explicitly established** (see [Timestamp basis](#timestamp-basis)). Otherwise the comparison is
   reported `unavailable` or `inconclusive`.
4. **A bag is `complete` only if everything about it is accounted for.** Missing/unreadable/unsupported/
   duplicate/unlisted segments, a missing or unusable `metadata.yaml`, count disagreements, an unclean or
   interrupted capture, recorder-reported message loss, or files that changed after capture all prevent
   `complete`. File names never prove order or completeness. `metadata.yaml` is the recorder's own assertion:
   agreement with the files shows consistency, not completeness.
5. **Unknown stays unknown.** Fields the tool cannot observe (recorder statistics, finalization when ambiguous,
   clock relation between supervisor time and `log_time`) are `null`/`unknown`/`not_established`, never `false`/`0`.

## Setup

Python **3.10 or newer** (declared). Tested versions are listed under [Verification](#verification).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest                          # runs the full suite
physicore-collector --help
```

Runtime dependencies (see `pyproject.toml`): `typer>=0.24`, `pydantic>=2.6`, `mcap>=1.2.2`, `pyyaml>=6`.
The lower bounds are the lowest versions the full test suite was actually run against. Older
versions are untested; in particular older `typer` releases misbehave with current `click`.

## Fixtures (synthetic, deterministic, no real robot data)

```bash
python -m tests.make_fixtures examples/fixtures
```

Everything is derived from fixed constants, so repeated runs produce byte-identical files.
A test checks both determinism and that the committed files in `examples/fixtures/` match the generator.

| File | What it is |
|------|-----------|
| `valid.mcap` | Complete, finalized recording: `/joint_states` (50 Hz), `/camera/image` (10 Hz), `/diagnostics` (1 Hz), 2.0 s, with a metadata record |
| `cadence_gap.mcap` | Same, but `/joint_states` has one objectively defined 0.52 s gap (last message at +0.48 s, next at +1.00 s) |
| `missing_topic.mcap` | Same, without the required `/camera/image` |
| `truncated.mcap` | `valid.mcap` cut at 60% of its bytes (no footer, no end magic) |
| `corrupted.mcap` | `valid.mcap` with one byte flipped inside the data section |
| `not_mcap.mcap` | Plain text, not MCAP |
| `metadata.json` | Example supplied metadata file |
| `triggers/trigger_unknown_basis.json` | `timestamp_basis: unknown` |
| `triggers/trigger_labels_match_unattested.json` | basis `system_time`, no compatibility attestation |
| `triggers/trigger_declared_incompatible.json` | basis `sim_time`, explicitly declared not compatible |
| `triggers/trigger_attested_within.json` | basis `system_time`, attested compatible, inside the recording |
| `triggers/trigger_attested_after.json` | same attestation, 10 s into the future (after the recording) |

## Usage

```text
physicore-collector --help
physicore-collector profile validate <profile.yaml|json>
physicore-collector session inspect  <file.mcap | bag-dir | capture-session-dir> [--json]
physicore-collector session validate <file.mcap | bag-dir | capture-session-dir> --profile <profile> [--trigger T] [--metadata M] [--health H] [--json]
physicore-collector export           <file.mcap | bag-dir | capture-session-dir> --profile <profile> --out <new-directory> [--metadata M] [--trigger T] [--health H]
physicore-collector package verify   <package-dir> [--json]
physicore-collector capture run      <profile> --out <existing-dir> [--duration SECONDS] [--session-id ID] [--no-auto-prepare]   # foreground
physicore-collector capture start    <profile> --out <existing-dir> [--duration SECONDS] [--session-id ID] [--no-auto-prepare]   # detached
physicore-collector capture status   <session-dir> [--json]
physicore-collector capture stop     <session-dir> [--wait SECONDS] [--reason TEXT]
physicore-collector capture recover  <session-dir> | --all <out-dir> [--stop-orphan]
physicore-collector capture list     <out-dir> [--json]
physicore-collector capture install-service   --name N --user U --profile P --out DIR [--ros-setup S ...] [--env K=V ...] [--enable] [--dry-run] [--no-auto-prepare]
physicore-collector capture uninstall-service --name N
physicore-collector capture service-run       ...   # what the systemd unit executes
physicore-collector incident prepare <file.mcap | bag-dir | capture-session-dir> [--profile P] [--out DIR] [--dry-run] [--metadata M] [--trigger T] [--health H] [--json]
physicore-collector incident list    <dir holding sessions and/or incidents> [--verify] [--json]
physicore-collector incident pending list <out-dir>...                       # sessions still needing a package, and what blocks each
physicore-collector incident pending run  <out-dir>... [--stop-orphan] [--max N] [--max-mib-per-sec X]   # idle-only drain
physicore-collector incident show    <incident-dir | session-dir> [--verify] [--json]
```

A **capture session directory** (what `capture run` writes: `<session>/bag/` + `<session>/capture_health.json`) can be
passed directly to `inspect`/`validate`/`export`; the sidecar is then used automatically. A directory that has its own
`metadata.yaml` is always treated as a bag. `--health` is only valid with a bag directory.

`--metadata` on `session validate` is an addition to the minimum interface so that `validate` and
`export` always give the same answer for the same inputs.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success. `validate`/`export`: status `complete`. `inspect`: fully readable. `package verify`: verified. `capture run`: clean stop and a fully readable, finalized bag. `capture status`: running, or ended cleanly. `capture stop`: stopped cleanly. |
| 1 | The command ran but the result was not clean: status `partial` or `failed` (`export` still writes the package, labelled with its true status); `inspect`: partially readable (including an incomplete bag); `package verify`: problems found; `capture run`/`status`/`stop`: the recording exists but ended unclean, was interrupted, or the supervisor is gone. |
| 2 | Usage, input, or runtime error: missing/invalid file, invalid profile, export target already exists, unreadable recording for `inspect`, capture could not start (`ros2` missing, invalid capture settings, session directory exists), no capture record, ... |

Errors are printed to stderr as `ERROR: ...` with the exact file and field path where possible.

### Examples (run from the repository root after installing)

```bash
# 1. validate a profile (prints its SHA-256)
physicore-collector profile validate examples/profiles/synthetic_fixture.profile.yaml

# 2. inspect a recording: topics, message counts, min/max log_time, integrity
physicore-collector session inspect examples/fixtures/valid.mcap
physicore-collector session inspect examples/fixtures/truncated.mcap      # exit 1, shows what could be read
physicore-collector session inspect examples/fixtures/valid.mcap --json

# 3. validate against a profile
physicore-collector session validate examples/fixtures/valid.mcap       --profile examples/profiles/synthetic_fixture.profile.yaml   # complete, exit 0
physicore-collector session validate examples/fixtures/cadence_gap.mcap --profile examples/profiles/synthetic_fixture.profile.yaml   # partial, exit 1
physicore-collector session validate examples/fixtures/valid.mcap --profile examples/profiles/synthetic_fixture.profile.yaml \
    --trigger examples/fixtures/triggers/trigger_attested_within.json                                                                # complete, exit 0
physicore-collector session validate examples/fixtures/valid.mcap --profile examples/profiles/synthetic_fixture.profile.yaml \
    --trigger examples/fixtures/triggers/trigger_unknown_basis.json                                                                  # partial: comparison unavailable

# 4. export a portable evidence package (the --out directory must NOT exist)
physicore-collector export examples/fixtures/valid.mcap \
    --profile examples/profiles/synthetic_fixture.profile.yaml \
    --metadata examples/fixtures/metadata.json \
    --trigger examples/fixtures/triggers/trigger_attested_within.json \
    --out /tmp/my_package
```

## Profile format

```yaml
profile_schema_version: "1.0"        # quoted; the only supported version
profile_id: example-arm-v1
robot_name: Example Arm              # optional
log_time_basis: system_time          # what the recording's log_time represents; default "unknown"
required_metadata_keys: [operator, session_label]
topics:
  - name: /joint_states
    required: true                   # default true
    expected_hz: 50                  # threshold = gap_factor / expected_hz  (gap_factor default 2.0)
  - name: /camera/image
    max_gap_seconds: 0.5             # explicit threshold wins over expected_hz
  - name: /diagnostics
    required: false
```

YAML or JSON. Unknown fields, duplicate keys, duplicate topics, non-finite numbers (`.inf`, `.nan`),
empty files, and files over 1 MiB are rejected. The SHA-256 is computed by streaming and describes
the exact bytes that were parsed.

## Session status (authoritative: `src/physicore_collector/status.py`)

First matching rule wins:

| # | Condition | Status |
|---|-----------|--------|
| 1 | profile invalid | `failed` |
| 2 | recording unreadable | `failed` |
| 3 | recording partially readable (truncated/malformed) | `partial` |
| 4 | no checks executed | `partial` |
| 5 | any **required** check failed | `partial` |
| 6 | any **required** check inconclusive | `partial` |
| 7 | otherwise | `complete` |

Non-required checks (topics marked `required: false`, cadence on such topics) are warnings: they stay in the
report but never block `complete`. The report schema also refuses to represent `complete` if evidence contradicts it.

Checks produced: `readability`, `topic_present:<topic>`, `cadence:<topic>` (topics with a threshold),
`metadata:<key>` (profile `required_metadata_keys`, looked up in MCAP Metadata records and in `--metadata`),
and `trigger:coverage` (only if a trigger is supplied; required). **Decision:** a supplied trigger must be
provably covered by the recording for the session to be `complete`, so an unestablished time basis yields `partial`.
Flip `TRIGGER_CHECK_REQUIRED` in `validation.py` to make it advisory.

## Readability: what is and is not established

* **readable**: valid start magic and Header; data section read to DataEnd and Footer; closing magic matches; no
  trailing bytes; every record parsed; chunk CRCs and the data-section CRC verified where the file carries them;
  channels/schemas consistent; `Statistics.message_count` equals messages actually read.
* **partially readable**: valid Header but any of the above failed. Data read before the first problem is reported;
  the rest is unknown. In such files an absent required topic or metadata key is `inconclusive`, not `fail`.
* **unreadable**: empty file, wrong start magic, or no valid Header.

Tested claim: **every proper prefix of a valid file is rejected** (never reported readable, never a crash), for
uncompressed, zstd and lz4 files. Not claimed: the content of the summary section (chunk/message indexes, summary
offsets) is parsed but **not cross-validated**; in a fuzz run about 6% of single-byte corruptions, all in that
section, went undetected. The scan is not hardened against adversarial files (e.g. decompression bombs).

## Timestamp basis

A trigger is a JSON file: `trigger_id`, `timestamp_ns`, `timestamp_basis`
(`unknown|ros_time|system_time|sim_time|utc_wall_clock|monotonic|other`), and optionally
`basis_compatibility: {compatible_with_log_time: bool, established_by: "<how you know>"}`.

The trigger is compared with the recording's `log_time` window **only if** the trigger basis is known, the profile
declares a known `log_time_basis`, the two labels are equal, **and** the trigger carries `compatible_with_log_time: true`
with a stated `established_by`. Equal labels alone are never enough (two `sim_time` clocks from different simulators
are different clocks). Otherwise: `unavailable` (basis unknown, profile basis unknown, or explicitly incompatible) or
`inconclusive` (labels differ, or no attestation). `established_by` is human-supplied text that this tool records but
cannot verify. If the recording is truncated, "after the window" becomes `inconclusive` (the trigger may lie in the unread part).

## rosbag2 bags (Day 2)

Supported: bag directories with **MCAP storage** (`storage_identifier: mcap`), `metadata.yaml` versions 3 to 9,
`compression_mode` none or MESSAGE (message payload compression does not affect framing checks). Explicitly
**unsupported and reported, never guessed**: other storage (e.g. `sqlite3`/`.db3`), file-level compression
(`compression_mode: FILE`, `*.mcap.zstd`), `metadata.yaml` versions outside 3..9. Such segments are hashed and
exported byte-identically as opaque evidence but not read.

How a bag is inspected:

* every storage file goes through the same streaming scanner as a lone `.mcap` (CRC/framing checks; only `log_time`
  values are kept in memory), then results are merged, so a cadence gap across a segment boundary is still seen;
* segment order is taken from `metadata.yaml`; without usable metadata the `*.mcap` files on disk are inspected as an
  **unconfirmed** set ordered by observed `log_time` then name (`segment_order_basis: inferred_from_observed_log_time`);
* metadata message counts (total, per file, per topic) are compared with what was read; a disagreement is reported
  as an inconsistency, not attributed to either side;
* paths in `metadata.yaml` that are absolute, contain `..`, or resolve (symlink) outside the bag are refused; a listed
  but absent segment is `missing`; a listed non-file is `not_a_file`; duplicate or conflicting references are reported;
* entries in the directory that `metadata.yaml` does not account for are reported and **not exported** (an unlisted
  storage-looking file also blocks `complete`).

`metadata.yaml` timestamps are on the recorder's clock and are never compared with `log_time`.

## Capture (Day 2)

`capture run` is a **foreground supervisor** around `ros2 bag record` (requires a sourced ROS 2 environment and
`rosbag2_storage_mcap`; everything else in this tool does not). It adds no recording logic: the profile's optional
`capture:` section (profile schema `1.1`, see `examples/profiles/capture_example.profile.yaml`) maps onto documented
`ros2 bag record` options (`--storage`, `--max-bag-size`, `--max-bag-duration`, topics). Behaviour:

* creates `<out>/<session>/` exclusively (never reuses one), lets rosbag2 create `<session>/bag/`;
* Ctrl+C / SIGTERM / `--duration` / `capture stop` request a graceful stop (SIGINT to the recorder's process group,
  exactly what Ctrl+C does to rosbag2), and the recorder is **killed** after `capture.stop_timeout_seconds`; a second
  Ctrl+C kills immediately; no orphans (the whole process group is killed);
* refuses to start on invalid settings, an option-like topic name (`-a`), or below `capture.min_free_disk_bytes`;
* after the recorder ends, inspects the bag and writes **`capture_health.json`** (atomically rewritten on every event,
  so a killed supervisor leaves `state: running` with a dead pid, read as *interrupted*).

The sidecar records: session id, state, requested settings (profile SHA-256, topics, limits, identity labels), the
sanitized command, recorder pid/exit code/signal, a bounded tail of recorder output, ordered events, errors, warnings,
known interruptions, the output inventory with SHA-256 per file at capture end, and a tri-state
`bag_finalization.appears_finalized`. It deliberately does **not** record: environment (except an allow-list of
`ROS_DISTRO` and `RMW_IMPLEMENTATION`), host paths, machine details, or any statistics rosbag2 does not expose.

What it cannot tell you: rosbag2 gives no machine-readable statistics and writes no drop counters to `metadata.yaml`.
It does print a human-readable warning when its own cache dropped messages; matching lines are kept **verbatim** in
`recorder.reported_loss_lines`, listed in `known_gaps`, and block `complete`. **No such line does not mean no loss.**
Capture start/stop times are the supervisor host's system clock and are explicitly *not established comparable* with
MCAP `log_time`.

## Unattended capture (Day 3)

Full guide: [docs/OPERATIONS.md](docs/OPERATIONS.md). In short:

* **Detached:** `capture start` returns once the recorder is up; the supervisor has no controlling terminal. `capture stop`
  writes a stop-request file the supervisor polls (no signals, no pid targeting) and returns only when the supervisor has
  released its locks. `capture status` reports **supervisor**, **recorder** and **capture** separately and never validates
  the recording.
* **Liveness without PIDs:** "is a supervisor attached" is answered by a per-session `flock` the kernel releases however the
  supervisor dies; recorder identity is (pid, start time, boot id) from `/proc`, so a reused pid is never mistaken for it
  and never signalled. One active capture per output directory.
* **Crash, power loss, reboot:** a record that says `running` without a supervisor is **interrupted**. `capture recover`
  persists that (idempotent; recording only read; end time stays unknown; reboot detected from boot ids) and, only on request,
  stops a recorder that outlived its supervisor. A recorder keeps recording if its supervisor dies (its signal state is made
  deterministic and SIGPIPE stays ignored), so a dead supervisor never costs data.
* **Heartbeat and disk:** the record is refreshed every `heartbeat_seconds` (profile 1.2); a stale heartbeat reports
  `degraded`. `disk_warn_free_bytes` / `disk_critical_free_bytes`: below *critical* the recorder is stopped gracefully and the
  session ends `stopped_disk_critical` (never `complete`). Nothing is deleted. If the *final* record cannot be written it is
  retried, then reported loudly with a non-zero exit.
* **systemd:** `install-service` generates a unit that runs `service-run` directly (no shell). The ROS environment comes from
  `--ros-setup` scripts only; forwarded variables are allow-listed. Installed disabled unless `--enable`. Each restart
  records a NEW session after settling the old one. Not verified against a real systemd run: see the Day 3 report.

## Incident preparation (Day 4)

An **incident** is an investigation-ready, self-contained, locally verifiable package plus a machine-readable result.
It is prepared **automatically when a capture ends** and **on demand for any existing recording**, by one code path.

```bash
# existing recording: a file, a rosbag2 directory, or a capture session directory. Segments and metadata are found for you.
physicore-collector incident prepare ~/bags/run12 --profile robot.yaml --out ~/incidents/run12
physicore-collector incident prepare ~/bags/run12/run12_3.mcap --profile robot.yaml --out ~/incidents/run12   # one listed segment => the whole bag
physicore-collector incident prepare /data/captures/20260101T120000Z-ab12cd34                                  # session: own profile, out=<session>/incident
physicore-collector incident prepare ~/bags/run12 --profile robot.yaml --out ~/incidents/run12 --dry-run      # what would be copied, space needed; writes nothing
physicore-collector incident list /data/captures          # prepared / FAILED (with retry command) / not_prepared
physicore-collector incident show /data/captures/<session> --verify
```

Layout: `<incident>/package/` (the unchanged export package: `package verify` works on it), `<incident>/incident.json`
(schema 1.0), `<incident>/INCIDENT_SUMMARY.md`. For a capture session the default location is `<session>/incident`.

**Four separate statements** are made and none implies another (`incident.json`):

| Statement | Question | Values |
|---|---|---|
| `artifact_integrity` | do the packaged bytes match their recorded digests (manifest; digests recorded at capture end)? | `verified` / `mismatch` |
| `capture_health` | what is known of the recorder lifecycle? | `finalized` / `interrupted` / `failed` / `degraded` / **`unknown`** (no capture-health record) |
| `evidence_coverage` | were the profile's EXPECTED topics/metadata observed? time gaps seen by cadence rules? | `expected_signals_observed` / `expected_signals_missing` / `undetermined` |
| `preparation` | was a self-contained package assembled and re-verified before publishing? | `prepared` (a failure publishes no incident) |

The result is `success` (nothing adverse is known), `degraded` (a verified package exists but the validation status is
`partial`/`failed`, or the capture was interrupted/failed, the recorder reported loss, or the bag changed since capture
end) or `failure` (no verified package). A valid checksum does not show that nothing was lost; a clean recorder exit does
not show every sensor delivered everything; `unknown` capture health is never read as healthy. The validation status keeps
its existing meaning (`complete | partial | failed`). Exit codes: **0** success, **1** degraded (package written), **2** failure.

**Scheduling and pending work (Day 5).** Preparation never knowingly competes with a recording: it is deferred (exit 3, queued in
`<session>/incident_attempt.json`) while a capture is active in a guarded directory, stops cleanly if one starts, and never packages a recording
a process still holds open for writing. `capture service-run` never prepares inline; it queues the session. Drain the queue with
`incident pending run` when idle (a command, not a daemon). `incident list` shows stored results as `PREVIOUSLY PREPARED (not re-verified)`;
`--verify` gives `VERIFIED NOW` / `TAMPERED` / `UNVERIFIABLE`. The overlap race is bounded (about 0.1 s plus one I/O call), not eliminated.
Full policy and limits: [docs/OPERATIONS.md](docs/OPERATIONS.md#incident-packages).

Safety: the source is only read; everything is built in a hidden staging directory and published with one rename (a
failure, Ctrl-C or SIGTERM removes it); an existing target is never overwritten; repeating the command on an unchanged
source is idempotent (`already prepared`, after re-verifying), a different source or a tampered target is a conflict; the
copy needs free space for itself plus the profile's `disk_critical_free_bytes` and is checked **before** copying; files are
streamed. Automatic preparation never changes the capture's own exit code. Disable it with `--no-auto-prepare`
(`capture run`/`start`/`service-run`/`install-service`). Details: [docs/OPERATIONS.md](docs/OPERATIONS.md#incident-packages).

## Export package

```
<out>/
  recording/<original name>     byte-identical copy of the MCAP
  recording/<bag dir name>/...  (rosbag2 bag) metadata.yaml + every accounted segment, byte-identical, same relative layout
  capture/capture_health.json   (if a capture-health record was supplied) byte-identical
  profile/<original name>       byte-identical copy of the profile
  metadata/<original name>      byte-identical copy of --metadata   (if supplied)
  trigger/<original name>       byte-identical copy of --trigger    (if supplied)
  validation_report.json
  manifest.json                 inventory of everything above; contains no checksum of itself
```

`manifest.json` holds: manifest schema version, tool name and version, creation time (UTC), session status, the
profile and its SHA-256, the artifact inventory (path, role, size, SHA-256), the recording window from MCAP `log_time`,
topic information, supplied and in-file metadata, the trigger (with `timestamp_basis`), validation results,
`known_gaps`, and `loss_detection_coverage`. Bag exports (manifest schema `0.2`) add `recording_kind`, a `bag` section
(metadata summary, per-segment state/size/SHA-256, unaccounted entries, conflicts, order basis) and, with a sidecar,
`capture_health`. A single-MCAP export still writes schema `0.1`, unchanged from Day 1, so Day 1 tooling keeps working.

`package verify` re-checks a package fully offline: every listed file's size and SHA-256, no unlisted files, no
symlinks, supported manifest version, and that the manifest's bag section agrees with its artifact inventory.
It proves the bytes equal what was recorded at export time. It does **not** prove the data is true or complete.

Safety (each point has tests):

* An existing target (file, directory, empty directory, dangling symlink) is **never** overwritten. There is no `--force`.
  The parent directory must already exist; export never creates parent directories.
* The package is built in a hidden staging directory next to the target and published with one rename, so it appears
  complete or not at all.
* The recording's SHA-256 is computed before analysis, while copying, and again on the staged copy; all must match
  or export aborts (this also catches a file that changes mid-export). The finished staging directory is re-verified
  against its manifest before publishing.
* On any failure (including Ctrl-C) the staging directory is removed; if removal itself fails, the error says so.
* A `partial` or `failed` session is still exported (preserving evidence), labelled with its true status, exit code 1.

Limits: "target must not exist" is check-then-rename; a process creating an *empty* directory at the target in the
instant between the final check and the rename could be replaced (POSIX semantics). A hard kill can leave a
`.<name>.staging-*` directory, which is safe to delete.

## Repository layout

```
src/physicore_collector/
  cli.py          Typer commands only (arguments, printing, exit codes)
  render.py       text rendering (pure functions)
  session.py      load inputs once, scan, validate (shared by validate and export)
  profile.py      profile loading        inputs.py      trigger/metadata loading
  mcap_reader.py  MCAP scan + integrity  cadence.py     gap analysis
  timebase.py     trigger comparison     validation.py  checks -> report
  status.py       status rules           manifest.py    manifest, known gaps, loss coverage
  export.py       safe export + verify   checksums.py   streaming SHA-256, copy
  schemas.py      Pydantic schemas       errors.py      typed errors   issues.py  error formatting
  bag_metadata.py rosbag2 metadata.yaml  bag.py         bag discovery, segment scan/merge, bag:* checks
  capture_health.py sidecar schema/loader/checks         recorder.py    recorder adapter (ros2 bag record)
  supervisor.py   capture lifecycle      procident.py   process identity (/proc)   sessionlock.py  flock locks
  liveness.py     read-time assessment   recovery.py    capture recover             ros_env.py      ROS env from setup scripts
  service.py      systemd unit/install   __main__.py    python -m physicore_collector
  incident.py     incident preparation (orchestrates bag/session/export/manifest/liveness; no parallel logic)
tests/            pytest suite; tests/make_fixtures.py generates fixtures; tests/bagtools.py builds synthetic bags;
                  tests/recorder_doubles.py (scripted recorder + stand-in `ros2`); tests/compat/ frozen Day 1 package;
                  tests/ros2_integration/ REAL-ROS 2 tests, skipped unless ROS 2 is available
examples/         profiles/ and fixtures/
docs/             ARCHITECTURE.md, OPERATIONS.md, SNAPSHOT_FEASIBILITY.md
```

## Not implemented

Triggered/snapshot capture (analysis only: [docs/SNAPSHOT_FEASIBILITY.md](docs/SNAPSHOT_FEASIBILITY.md)), ring buffers,
`sqlite3` storage and file-level-compressed bags (reported as unsupported), signatures/encryption, a reference-only (no-copy) incident package, remote control or
multi-robot management, Windows/macOS capture, a debugger, RRIE, AI
diagnosis, cloud services, a custom recorder, a viewer.

## Supported and tested versions

Declared: Python `>=3.10`. No dependency was added in Day 2. The full test suite was run (all passing; the 2
`ros2_integration` tests skip without ROS 2) in these environments:

| Python | typer | click | pydantic | mcap | PyYAML | pytest |
|--------|-------|-------|----------|------|--------|--------|
| 3.10.22 | 0.27.3 | (resolved by typer) | 2.14.1 | 1.5.0 | 6.0.3 | 8.4.2 |
| 3.12.3 | 0.27.3 | (resolved by typer) | 2.14.0 | 1.5.0 | 6.0.3 | 8.4.2 |
| 3.13.16 | 0.27.3 | (resolved by typer) | 2.14.1 | 1.5.0 | 6.0.3 | 8.4.2 |
| 3.10.22 (**minimum pins**) | 0.24.0 | 8.2.1 | 2.6.0 | 1.2.2 | 6.0 | 8.0.0 |

Capture uses POSIX process groups and `/proc`: **Linux only**. Offline commands are not platform-specific but only
Linux was tested.

Python 3.11 and Windows/macOS were not tested. Dependency versions below the minimum pins are untested
(`mcap<1.2.2` lacks an API this tool uses; `typer<0.24` was observed to misbehave with current `click`).

## Verification

Exact command output, test counts and the verification report accompany each delivery; test counts are not
hard-coded here so this file cannot go stale. To verify yourself: install as above and run `pytest`.
"# Physicore-debugger" 
