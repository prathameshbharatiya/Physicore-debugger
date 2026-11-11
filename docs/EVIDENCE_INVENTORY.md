# Evidence inventory (Phase 1 integration boundary)

A read-only, versioned view of an incident / package / recording, for a separate consumer (the future Investigator).
It reports what the Collector can establish and what it cannot. **It does not diagnose.**

```
physicore-collector evidence inventory <PATH> [--json]
```

```python
from physicore_collector.inventory import inspect_evidence, inventory_json, exit_code
inv = inspect_evidence("path/to/incident")      # EvidenceInventory (pydantic, schema 1.0); never raises for expected problems
text = inventory_json(inv)                       # deterministic JSON
```

## Accepted input

| `provenance.input.kind` | What | Integrity | Validated against a profile |
|---|---|---|---|
| `incident` | incident directory, its `incident.json`, or a capture session directory holding `incident/` | re-hashed now (`incident.verify_incident_detailed`) | yes, as stored in the manifest |
| `package` | export package directory (`manifest.json`) | re-hashed now (`export.verify_package`) | yes, as stored in the manifest |
| `raw_bag` | rosbag2 directory (single or split MCAP), loose `.mcap` directory, or a capture session without an incident | `not_applicable` (no recorded digests) | no |
| `raw_mcap` | one `.mcap` file | `not_applicable` | no |
| `unsupported` | anything else, a missing path, a symlink | n/a | n/a |

## Output (top-level fields of `inventory.json`)

`inventory_schema_version` · `provenance` (tool, input as given, `read_only`, `integrity_checked_now`, `content_basis`, source schema
versions, reused components, `acquisition`) · `inspection` (did the *inventory* complete: complete / partial / failed) ·
`session_status` (the existing complete/partial/failed taxonomy, or null for a raw input) · `integrity` · `capture_health` ·
`recording` (kind, segments in order with state / counts / first-last log_time / artifact reference) · `artifacts` (relative path,
kind, supported?, size, sha256 and its basis, per-artifact integrity) · `topics` · `timebase` (clock domains and alignments) ·
`checks`, `cadence_findings`, `trigger_comparison` (existing results, verbatim) · `incident` (the stored record) · `gaps` ·
`not_established` · `warnings` · `errors` (structured: code, message, hint, artifact_ref).

Four things are deliberately kept apart: **inspection state** (did this tool finish), **integrity** (do bytes match recorded
digests), **session_status / checks** (the existing validation), **capture_health** (what is known about the recorder).

## Exit codes

0 inspected, nothing known to be wrong · 1 inspected, but integrity failed or could not be checked, the evidence is partial/failed,
or the inventory is partial · 2 the input could not be inspected.

## Rules this module keeps (each has a test in `tests/test_inventory.py`)

* Read-only: nothing is written, renamed, locked or re-stamped inside (or next to) the input. Tested by tree snapshot and by a guard that
  raises on any write attempt (the guard itself is tested, including the `pathlib` route).
* Deterministic: no wall-clock time is recorded; the same bytes give the same JSON (only `provenance.input.path`, the path as given,
  differs between copies).
* Nothing invented: counts, windows and digests come from the existing parsers or manifest. A total message count is `null` whenever any
  segment's count is unknown. There are no drop counters (rosbag2 exposes none); `no_loss_claim_permitted` is pinned to false.
* Clocks are never merged. `timebase.clock_domains` lists `mcap_log_time`, `rosbag2_metadata_time`, `capture_supervisor_wall_clock` and
  `trigger` separately; `alignments` copy the Collector's own verdicts (`not_compared`, `not_established`, the trigger comparison result).
* `content_basis`: for an incident/package, recording / topics / validation are the manifest's **stored** results; only integrity is
  re-checked now. If integrity fails, a warning says the stored results may not describe the bytes on disk.
* Offline only: `provenance.acquisition.live_ros2_capture_verified` is pinned to false.

## Path safety (hardened)

* **One manifest path rule, POSIX and Windows** (`schemas.relative_path_problem`): a path must be relative and made of plain components.
  Rejected: absolute and rooted (`/x`, `\\x`), drive-qualified (`C:\x`, `C:/x`, `C:x`, `x:y/z`), UNC (`\\\\h\\s`, `//h/s`), any `..`
  through `/` or `\`, backslashes at all, empty / `.` components, `a//b`, trailing `/`, components Windows would strip to `..`
  (`.. `, `... `), and control characters. Existing manifests (including `tests/compat`) still load.
* **Symlinks are refused at every path component**, not only the final file (`export.first_symlink_component`). Applies to
  `verify_package`, `incident` verification, the `rglob` replacement (a non-following walk) and every read in `inventory.py`.
  A refused path is reported as a structured `unsafe_path` error (or the existing `is a symlink` / `passes through the symlink` integrity
  problem) and is **never opened**. An unsafe `ArtifactRef` cannot be constructed.
* **Inaccessible input** (missing, permission denied, unreadable directory, unsupported kind) gives a structured inventory
  (`inspection.state`, `errors[]` with code/message/hint) and exit code 2 or 1, not a traceback.
* Tests: `tests/test_path_safety.py` (new) and the hostile-layout, no-write, no-lock and permission tests in `tests/test_inventory.py`.

## What is and is not verified (this build)

Verified by tests (synthetic files, offline): everything above, on single and split MCAP, rosbag2 directories with and without
`metadata.yaml`, unsupported storage (`.db3`), a missing segment, truncated recordings, modified/missing/unlisted/symlinked artifacts,
malformed manifest and incident records, trigger clock cases, and capture sessions produced by the scripted `FakeAdapter`.

**Not verified: live ROS 2 capture.** No ROS 2 is installed where this was built and the two real-ROS tests in `tests/ros2_integration` are
skipped. Capture-health sidecars in the tests come from a scripted stand-in recorder, not `ros2 bag record`. Importing a file proves
nothing about live capture; that is Phase 6.

## Known limitations

* Integrity of an incident/package re-reads every byte (slow for multi-GB) and, unlike `incident show --verify`, does **not** check for an
  active capture first, so it can compete with a recording. Run it when idle.
* Per-artifact integrity is derived from the `path: problem` strings of the existing `verify_package`; a change to that message format
  would need a matching change here (covered by tests).
* Recording / validation content of an incident or package is not re-scanned (see `content_basis`).
* Raw inputs are not validated against a profile: no required-topic or cadence checks.
* Not inventoried: text logs, JSON/YAML sidecars other than the Collector's own, CSV. They appear as `unlisted` (package) or are ignored (raw).
* The inventory is not written into the package (no `investigation.json` yet).
* The symlink check is a check, not a lock: a path swapped between the check and the read (TOCTOU) is not prevented. Run on evidence that is
  not being modified.
* The input root's own ancestors may be symlinks (e.g. `/data -> /mnt/x`); only components *below* the root are refused.
* Windows rules are verified with `ntpath` / `PureWindowsPath` only; there was no Windows host. Reserved device names (`CON`, `NUL`),
  NTFS alternate streams and 8.3 short names are not specially checked.
* A POSIX file name containing a backslash is now refused when packaging (it would be a separator on Windows).
* The real-permission test needs a non-root user; as root it is skipped (it was run as `nobody` once, see the report).
* Large-bag performance and memory were not measured here.
