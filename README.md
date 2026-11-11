# PhysiCore Debugger

**A local-first evidence and investigation toolkit for robotics systems.**

PhysiCore turns robot recordings into structured, inspectable evidence. It validates MCAP and rosbag2 recordings, packages evidence with integrity metadata, supports supervised capture, and provides progressively deeper offline analysis—from typed signal decoding and anomaly detection to event response, localization, and evidence-bounded root-cause hypotheses.

> **Core principle:** distinguish what the recording establishes from what remains unknown. PhysiCore is designed to expose evidence, coverage, uncertainty, and limitations—not to manufacture certainty.

[Architecture](docs/ARCHITECTURE.md) · [Operations](docs/OPERATIONS.md) · [Evidence inventory](docs/EVIDENCE_INVENTORY.md) · [Investigator](docs/INVESTIGATOR.md)

---

## What it does

PhysiCore is organized as a pipeline. You can use the acquisition and evidence stages independently, or continue into the investigation stages when the required inputs and supported message formats are available.

| Stage | Capability | What it produces |
|---|---|---|
| 1. Acquire | Inspect MCAP recordings and MCAP-backed rosbag2 bag directories; optionally supervise `ros2 bag record` | Capture-health records and recording metadata |
| 2. Validate & preserve | Check readability, topic presence, cadence, metadata, trigger coverage, bag structure and known gaps | Validation results and portable, checksummed evidence packages |
| 3. Prepare incidents | Build incident packages from recordings or capture sessions; defer work when a recorder may still be writing | Incident manifests, artifact digests and durable pending-preparation state |
| 4. Inventory | Re-inspect incidents, packages, raw bags and MCAP files without modifying the source | Versioned evidence inventory and explicit integrity/coverage states |
| 5. Decode & investigate | Decode supported ROS 2 CDR payloads and compare observations with an explicit expectation contract | Typed observations, blind spots and first defensible divergence |
| 6. Detect anomalies | Run configured signal-level and timing detectors over supported recording evidence | Structured anomaly findings and reports |
| 7. Localize | Normalize symptoms, apply declared dependency relationships and examine consistency/propagation | Evidence-linked symptoms, blockers, links, chains and localization output |
| 8. Examine hypotheses | Evaluate a mechanism catalogue against symptoms, declared system structure and optional evidence/baseline inputs | Competing or unresolved mechanism assessments, checks and reviewable reports |
| 9. Review | Render investigation reports as Markdown/HTML and verify report references against source evidence | Human-readable investigation views and verification results |

The stages are not interchangeable. For example, a localization report is not itself proof of a physical root cause, and a hypothesis marked as supported under the project's evidence rules is not a probability estimate.

## Key design constraints

- **Evidence is not inference.** Raw observations, declared expectations, inferred relationships and assumptions remain distinguishable.
- **Unknown is not false.** Missing timestamps, unsupported encodings, ambiguous clocks and unobservable recorder statistics remain unknown or inconclusive.
- **No unsupported loss claims.** A cadence gap is timing evidence; it is not by itself proof of DDS message loss. Absence of a detected gap is not proof that no messages were lost.
- **Timebases stay explicit.** MCAP `log_time`, payload timestamps, rosbag2 metadata time, trigger time and supervisor wall-clock time are not silently merged.
- **Source recordings are preserved.** Inventory and investigation operations are read-only; incident preparation copies and records evidence rather than editing source recordings.
- **Integrity is re-checkable.** Packages and incident artifacts carry hashes so later verification can detect changes.
- **Coverage and limitations travel with results.** Reports disclose detectors not run, unsupported payloads, incomplete evidence and other blind spots.

## Repository map

| Path | Purpose |
|---|---|
| `src/physicore_collector/` | Python package and command-line implementation |
| `src/physicore_collector/anomaly/` | Anomaly configuration, detectors, findings and reporting |
| `src/physicore_collector/investigator/` | Expectation contracts, observations, CDR support and divergence analysis |
| `src/physicore_collector/localization/` | Symptoms, dependency graphs, blockers, consistency and chain analysis |
| `src/physicore_collector/rootcause/` | Mechanism catalogue, system model, evaluation, causal/claim rules and reports |
| `src/physicore_collector/investigation_view/` | Markdown/HTML view generation and verification |
| `tests/` | Unit, integration-style, fixture, regression and safety tests |
| `examples/` | Synthetic recordings, profiles, configs, contracts and expected reports |
| `docs/` | Architecture, operations, stage specifications and test reports |
| `DAY*_IMPLEMENTATION_REPORT.md` | Build-stage implementation notes and verification boundaries |

## Requirements

- Python **3.10 or newer**
- Runtime dependencies are declared in `pyproject.toml`: Typer (`>=0.24,<1`), Pydantic, MCAP and PyYAML. The CLI was checked with Typer 0.27.3; the declared range is broader than that checked version, so pin the version in a reproducible environment if exact CLI behaviour matters.
- ROS 2 is **not** required for offline inspection, validation, packaging, inventory or supported analysis of existing files.
- Live capture requires a sourced ROS 2 environment, `rosbag2` and the MCAP storage plugin.
- Optional CDR decoding support is installed with the `ros2` extra. The `xcheck` extra is for independent decoder cross-checking in development/testing.

## Install

From a clone of this repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

**Platform note:** Linux is the documented and tested environment. The Windows PowerShell command below is standard virtual-environment activation syntax, not a claim that PhysiCore has been validated on Windows. Live ROS 2 capture is Linux-only.

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

Install optional CDR decoding support when needed:

```bash
pip install -e ".[dev,ros2]"
```

Install the independent decoder cross-check extra only for development/testing:

```bash
pip install -e ".[dev,ros2,xcheck]"
```

Confirm the CLI is available:

```bash
physicore-collector --help
```

## Quick start

### 1. Inspect and validate a recording

The repository includes deterministic synthetic fixtures and a profile:

```bash
# Inspect recording structure, topics, counts and timestamps
physicore-collector session inspect examples/fixtures/valid.mcap

# Validate the recording against a robot profile
physicore-collector session validate examples/fixtures/valid.mcap \
  --profile examples/profiles/synthetic_fixture.profile.yaml

# Inspect the machine-readable result
physicore-collector session inspect examples/fixtures/valid.mcap --json
```

A deliberately gapped fixture demonstrates a non-clean result:

```bash
physicore-collector session validate examples/fixtures/cadence_gap.mcap \
  --profile examples/profiles/synthetic_fixture.profile.yaml
```

### 2. Export and verify an evidence package

```bash
physicore-collector export examples/fixtures/valid.mcap \
  --profile examples/profiles/synthetic_fixture.profile.yaml \
  --metadata examples/fixtures/metadata.json \
  --out /tmp/physicore-evidence

physicore-collector package verify /tmp/physicore-evidence
```

The export target must be a new directory. A package may still be written when validation is partial; its status is recorded rather than silently upgraded.

### 3. Build an incident package

```bash
# Prepare an incident package from a recording, bag or capture session
physicore-collector incident prepare examples/fixtures/valid.mcap \
  --profile examples/profiles/synthetic_fixture.profile.yaml \
  --out /tmp/physicore-incidents/incident-001

# List incidents and inspect the incident directory created above
physicore-collector incident list /tmp/physicore-incidents
physicore-collector incident show /tmp/physicore-incidents/incident-001
```

Preparation is guarded against active writers and can be deferred. See [Operations](docs/OPERATIONS.md) for recovery, pending work, background capture and service deployment.

### 4. Inventory evidence

```bash
physicore-collector evidence inventory /tmp/physicore-incidents/<incident-directory> --json
```

Inventory is a read-only, versioned description of the evidence and what can currently be established about it.

### 5. Investigate against explicit expectations

```bash
physicore-collector evidence investigate \
  examples/investigator/example_recording.mcap \
  --expectations examples/investigator/expectations.json
```

The Investigator does not learn normal behaviour from the recording. It evaluates a supplied, versioned contract and reports the earliest divergence it can defend within the available evidence. See [Investigator](docs/INVESTIGATOR.md).

### 6. Explore the anomaly and localization workflows

The detailed CLI and configuration interfaces are documented in the stage-specific references:

- [M3a — CDR decoding and typed signal access](docs/M3A_DECODING.md)
- [M3b — event response](docs/M3B_EVENT_RESPONSE.md)
- [M3c — anomaly detection](docs/M3C_ANOMALY_DETECTION.md)
- [M3d — localization](docs/M3D_LOCALIZATION.md)
- [M3e — investigation view](docs/M3E_INVESTIGATION_VIEW.md)
- [M4 — mechanism catalogue](docs/M4_CATALOGUE.md)
- [M4 — system model](docs/M4_SYSTEM_MODEL.md)
- [M4 — hypotheses](docs/M4_HYPOTHESES.md)
- [M4 — causal and claim rules](docs/M4_CAUSAL.md)
- [M4 — evaluation](docs/M4_EVALUATION.md)

To see the full command tree and options for the installed version, use:

```bash
physicore-collector --help
physicore-collector evidence --help
physicore-collector incident --help
```

## Live capture

PhysiCore can supervise the supported `ros2 bag record` process; it does not implement its own ROS 2 recorder.

Live capture requires a correctly sourced ROS 2 installation and `rosbag2_storage_mcap`. A profile with capture settings is provided at `examples/profiles/capture_example.profile.yaml`.

Typical command shapes:

```bash
# Foreground capture
physicore-collector capture run <profile.yaml> --out <existing-output-directory>

# Detached capture
physicore-collector capture start <profile.yaml> --out <existing-output-directory>

# Check, stop and recover a session
physicore-collector capture status <session-directory>
physicore-collector capture stop <session-directory>
physicore-collector capture recover <session-directory>
```

For unattended operation, systemd setup, disk-space controls, recorder liveness, crash recovery and deferred incident preparation, follow [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Tests and synthetic examples

Run the test suite:

```bash
pytest
```

The project includes synthetic MCAP and rosbag2 fixtures, generated contracts and expected outputs. Regenerate the base recording fixtures with:

```bash
python -m tests.make_fixtures examples/fixtures
```

Other generators and examples are organized under `tests/` and `examples/`. The fixtures are synthetic and deterministic; they are not recordings from a physical robot.

Some tests are environment-dependent. In particular, real ROS 2 integration tests require a sourced ROS 2 environment with `rosbag2_storage_mcap`; they are skipped when those prerequisites are absent. A successful synthetic test suite must not be interpreted as proof of end-to-end operation on a physical robot.

## Verification boundaries and limitations

The repository's implementation reports and stage-specific documents contain the detailed verification record. Important boundaries include:

- Live ROS 2 recording and systemd behaviour require validation in the target robot environment; scripted recorder tests are not equivalent to a real robot deployment.
- CDR decoding is limited to supported message schemas and types. Unsupported encodings, missing schemas, malformed messages and out-of-scope payloads are reported as blind spots rather than guessed.
- Image and point-cloud understanding is outside the documented M3a decoding scope.
- **M4 catalogue status:** all 12 version-1 mechanisms are `proposed` and 0 are reviewed, as recorded in [the catalogue review pack](docs/M4_CATALOGUE_REVIEW.md). They are candidate mechanisms, not established diagnoses; validation is on generated scenarios, and validation against real robot recordings remains outstanding.
- Root-cause outputs are constrained by the catalogue review state, available observations, system model, declared dependencies and explicit rule set. They are not calibrated probabilities and should not be treated as autonomous repair instructions.
- A clean result means no issue was established within the checks and evidence coverage that ran. It does not prove a robot is fault-free or that no data was lost.
- Large-bag performance, multi-GB verification cost and deployment behaviour on varied robot hardware/storage require workload-specific measurement.

For the exact test counts, simulated versus executed scenarios, known residual risks and compatibility notes, read [DAY5_IMPLEMENTATION_REPORT.md](DAY5_IMPLEMENTATION_REPORT.md) and the corresponding M3/M4 test reports.

## Documentation index

- [Architecture and component boundaries](docs/ARCHITECTURE.md)
- [Operational guide: capture, service, recovery and incident scheduling](docs/OPERATIONS.md)
- [Snapshot capture feasibility](docs/SNAPSHOT_FEASIBILITY.md)
- [Evidence inventory contract](docs/EVIDENCE_INVENTORY.md)
- [Investigator contract and semantics](docs/INVESTIGATOR.md)
- [CDR decoding](docs/M3A_DECODING.md)
- [Event response](docs/M3B_EVENT_RESPONSE.md)
- [Anomaly detection](docs/M3C_ANOMALY_DETECTION.md)
- [Localization](docs/M3D_LOCALIZATION.md)
- [Investigation view](docs/M3E_INVESTIGATION_VIEW.md)
- [Mechanism catalogue](docs/M4_CATALOGUE.md)
- [Catalogue review](docs/M4_CATALOGUE_REVIEW.md)
- [System model](docs/M4_SYSTEM_MODEL.md)
- [Hypothesis evaluation](docs/M4_HYPOTHESES.md)
- [Causal/claim rules](docs/M4_CAUSAL.md)
- [Evaluation methodology](docs/M4_EVALUATION.md)
- [Day 2–5 implementation reports](DAY2_IMPLEMENTATION_REPORT.md) · [Day 2 reuse audit](DAY2_LANDSCAPE_AND_REUSE_AUDIT.md) · [Day 3 report](DAY3_IMPLEMENTATION_REPORT.md) · [Day 4 report](DAY4_IMPLEMENTATION_REPORT.md) · [Day 5 report](DAY5_IMPLEMENTATION_REPORT.md)

---

**Project:** [Physicore Debugger](https://github.com/prathameshbharatiya/Physicore-debugger)  
**Package / CLI name:** `physicore-collector`
