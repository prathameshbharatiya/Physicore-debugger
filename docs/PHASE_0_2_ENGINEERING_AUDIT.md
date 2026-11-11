# Phase 0–2 engineering audit

**Repository:** `prathameshbharatiya/Physicore-debugger`  
**Working branch:** `meeting-sprint`  
**Audit baseline commit:** `6f6c84788d1f6ce6ed03b4d4f239456c38ff9b02`  
**Audit date:** 2026-11-11

## Purpose and status semantics

This document separates implementation visible in the repository from checks that have actually been executed and independently observed. A historical implementation report is not evidence that the current branch passes. A configured workflow is not evidence of a successful workflow run. Synthetic fixtures are not real-robot validation.

Statuses:
- **Implemented (code present):** functionality and corresponding tests are present in the inspected tree.
- **Configured (not yet verified):** automation or process exists, but no successful current-branch execution has been observed.
- **Blocked:** a required verification cannot be performed in the current environment.
- **Outstanding:** additional evidence or implementation is still required.

## Phase 0 — Engineering baseline

| Check | Status | Evidence / remaining action |
|---|---|---|
| Confirm repository and working branch | Implemented (inspection) | Repository and `meeting-sprint` branch inspected; baseline commit recorded above. |
| Inspect project metadata and supported Python range | Implemented (inspection) | `pyproject.toml` declares Python >=3.10 and a pytest dev extra. |
| Inspect CI configuration | Configured | GitHub Actions matrix covers Python 3.10–3.13 on Ubuntu. Workflow now includes dependency consistency and CLI smoke checks. |
| Execute current-branch tests | Blocked | Tests have not been run in this environment. Local execution is required, or a successful GitHub Actions run must be observed. |
| Verify current-branch CI result | Outstanding | No successful run/status has been observed for the inspected branch head. |
| Verify branch protection / required checks | Outstanding | GitHub branch metadata reports protection disabled. Enable protection and require CI after confirming the workflow is healthy. |
| Establish verified baseline | Outstanding | Do not designate this commit as a known-good baseline until current-branch test and CI evidence exists. |

### Phase 0 acceptance gate

Do not mark Phase 0 complete until:
1. A clean environment installs the package and dev dependencies.
2. `python -m pip check` succeeds.
3. `physicore-collector --help` succeeds.
4. The complete pytest suite passes on the supported Python matrix.
5. The run is attached to the exact commit under review.
6. The branch protection policy requires the CI check for pull requests into `main`.

## Phase 1 — Evidence acquisition and ingestion

### Existing implementation observed

- MCAP scanning with bounded reads, framing and CRC checks, topic/schema/encoding metadata, and log-time collection.
- rosbag2 directory and segment discovery, metadata parsing, segment ordering and consistency checks.
- Supervised capture lifecycle, capture-health sidecar, recovery states, and active-recorder safety boundaries.
- Synthetic MCAP and rosbag2 fixtures, including valid, corrupted, truncated, missing-topic and cadence-gap cases.
- Documentation explicitly distinguishes offline inspection from ROS 2-dependent live capture.

### Verification boundaries and outstanding work

- Real robot / production ROS 2 capture has not been validated by the inspected synthetic reports.
- Cross-distribution ROS 2 compatibility needs a declared support matrix and execution against each supported distribution.
- Large-recording and long-duration capture behaviour need repeatable resource and soak tests on representative hardware.
- MCAP reader resource limits and adversarial-input behaviour need dedicated stress/security testing; current code stores per-message log-time values and the earlier audit identified decompression-bomb hardening as an open question.
- Real customer recordings must be used only with permission and a documented sanitization/data-handling process.

### Phase 1 acceptance gate

Require recorded results for supported ROS 2 distributions, representative real rosbag2/MCAP recordings, segment edge cases, long-duration capture/recovery, bounded resource use, and read-only source preservation. Synthetic fixtures alone do not satisfy this gate.

## Phase 2 — Evidence validation and preservation

### Existing implementation observed

- Validation covers readability, required topic presence, cadence, metadata, trigger coverage and capture-health checks.
- Evidence inventory distinguishes raw inputs from packages/incidents whose stored hashes can be re-checked.
- Package export uses a staging directory, exclusive file creation, copy/hash verification, final package verification and atomic publication.
- Existing-target overwrite is refused; source evidence is intended to remain read-only.
- Reports preserve known gaps, integrity states, coverage and limitations rather than treating unknown values as false.

### Verification boundaries and outstanding work

- Current-branch end-to-end execution has not been observed in this environment.
- Real incident packages and representative customer workflows have not been validated.
- Evidence quality should be expressed as explicit coverage and validation checks, not a single confidence score.
- Filesystem race, crash-recovery, permission-denied, disk-full and concurrent-capture scenarios need platform-specific tests on supported deployment environments.

### Phase 2 acceptance gate

Require passing automated tests on the exact commit, package export/verify round trips, mutation detection, interrupted export cleanup, source immutability checks, and at least one representative real recording/incident workflow. Synthetic-only validation must remain labelled synthetic.

## Safety invariants to preserve

1. Evidence is not inference; unknown is not false.
2. Cadence gaps are not, by themselves, proof of message loss.
3. Timebases are never silently merged.
4. Source recordings are not modified by inspection, inventory or investigation.
5. Missing evidence cannot increase a causal claim level.
6. Diagnostic requests must not overstate what a measurement can establish.
7. No uncalibrated probability or confidence score is presented as diagnostic certainty.

## Current disposition

**Phase 0: not complete.** Baseline inspection and CI configuration are present; execution and branch-policy verification remain outstanding.

**Phase 1: implementation substantially present; not complete.** Real-system, supported-distribution and scale validation remain outstanding.

**Phase 2: implementation substantially present; not complete.** Exact-commit test execution, operational failure-mode verification and real-incident validation remain outstanding.

No phase is represented as production-ready solely on the basis of code presence or historical test reports.
