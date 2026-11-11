# Day 2: landscape and reuse audit

Method: cloned and read the code (not READMEs); ran Flightcase's own test suite; read rosbag2's source. Facts below are marked **[read]** (seen in code), **[ran]** (executed here) or **[not inspected]**. Nothing is inferred from marketing text.

## Sources

| Project | Version / commit examined | License | Examined |
|---|---|---|---|
| Flightcase (`marouane53/Flightcase`) | `2.0.0rc0`, single commit "Initial public release" (2026-05-07) | Apache-2.0 (full text present) | `ros2_tools.py`, `recorder_profile.py`, `recorderd.py`, `ros2/flightcase_sidecar/.../sidecar_node.py` (first ~140 lines), `heavy_policy.py` (first ~70), `mcap_export.py` (first ~80), `clock.py` (first ~60), `cache.py` (retention/repair/schema), `replay.py` (first 50), `integrity/legacy.py` (first 50), packaging metadata. **[ran]** `pytest`: 89 passed, 1 skipped (the optional ROS 2 test). |
| rosbag2 (`ros2/rosbag2`) | shallow clone at work time | Apache-2.0 | `metadata.yaml` (de)serialisation (`yaml.hpp`), `sequential_writer.cpp`, message caches, `recorder.cpp`, `record.py`, `Snapshot.srv`. |
| MCAP Python library | already a Day 1 dependency | MIT (package metadata) | unchanged use via `mcap.reader` |

**Not inspected in depth:** Flightcase's federation/relay/Zenoh code, conformance suite, v2 manifest internals, the eval harness, apps/. No claim is made about them.

## Capability table

| Capability | PhysiCore Day 1 | Flightcase | Evidence | Decision | Reason |
|---|---|---|---|---|---|
| Recording a rosbag2 bag | reads single MCAP only | **does not use rosbag2**: grep for `rosbag`, `ros2 bag`, `metadata.yaml`, `subprocess` over `src/` and `ros2/` returns nothing; its sidecar is an `rclpy` subscriber writing into Flightcase's own cache | **[read]** | **Skip**; build thin supervisor around `ros2 bag record` | A second recorder is the "custom recorder" this project must not build, and there is nothing to reuse for rosbag2 |
| Recorder daemon | none | `recorderd.py` runs a **mock** session (`create_mock_session`) | **[read]** | Skip | Not a real recorder |
| Incident/window sealing | n/a | `seal_incident` hard-codes a robot path (`acme/warehouse-a/zone-3/r17`) and discards the requested window (`del start_ns, end_ns`) | **[read]** | Skip | Not a basis to build on; also solves a different problem (trigger windows) |
| Rolling/ring cache with retention, holds, repair | none | SQLite-backed cache, eviction that *declares* gaps, `verify --repair` | **[read]** | Defer (see snapshot doc) | Valuable ideas (declared gaps on eviction) but tied to its own cache format; rosbag2 snapshot mode is the supported ring buffer |
| MCAP export | n/a | exports its cache to MCAP; schema record `data` is the schema *name*/hash, not a message definition | **[read]** `mcap_export.py` | Skip | Output would not be replayable by tools needing real schemas; unrelated to reading a bag |
| Integrity | SHA-256 + manifest | blake3 hashes, Merkle roots, Ed25519 signatures | **[read]** | **Defer: signing** | Signing needs key management and a trust model; out of Day 2 scope. SHA-256 inventory + `package verify` is the honest minimum. No code copied |
| Gap / loss declaration | `known_gaps` + explicit "loss detection NOT PERFORMED" | typed `GapDeclaration`s | **[read]** | Reuse the *idea* only | Day 2 adds recorder-reported loss lines and unclean-capture gaps in the existing `known_gaps` vocabulary |
| Clock handling | declared `log_time_basis`, never verified | `ClockSample` with offset/drift/uncertainty; uncertainty defaults to a constant | **[read]** | Skip | A default uncertainty constant is not a measurement; Collector keeps "not established" |
| Heavy-sensor policy (budgets, encryption, trigger_required) | none | `heavy_policy.py` | **[read]** | Skip | Product-layer policy; not evidence acquisition |
| Replay | none | `replay_manifest_dry_run` only (dry-run report) | **[read]** | Skip | Out of scope |
| Test posture | 346 tests (Day 1) | 89 pass, ROS 2 path skipped | **[ran]** | n/a | Same limitation as ours: ROS 2 path unexecuted without ROS 2 |

## Verdict on reuse

**No Flightcase code was copied, vendored, adapted or depended upon.** Apache-2.0 would permit it, but the audit found nothing that fits a thin supervisor around rosbag2: it neither uses rosbag2 nor produces rosbag2-compatible output. Therefore no NOTICE/attribution obligation arises and no dependency was added. Ideas noted for later: declared gaps on eviction (snapshot work), signature envelope design (when signing is scoped).

rosbag2 is used **only as an external executable** (`ros2 bag record`) and as the *documented format reference* for `metadata.yaml` (`bag_metadata.py` cites the upstream file). Its source was read, not copied.

## What the audit changed in the implementation

* Reading rosbag2 `message_cache.cpp` showed the recorder **does** count dropped messages per topic and prints them as a WARN (`Cache buffers lost messages per topic … Total lost: N`, also `Cache buffers were unflushed with N remaining messages`). That is not machine-readable and not in `metadata.yaml`, so the supervisor now captures matching console lines verbatim (never parsing numbers), records them in the sidecar and `known_gaps`, and a required `capture:recorder_reported_loss` check blocks `complete`. The sidecar explicitly says absence of such lines proves nothing. Before this reading, the draft sidecar text claimed rosbag2 exposed no loss information at all, which would have been wrong.
* `metadata.yaml` is written at writer close (`SequentialWriter`), which is why a missing file is reported as "consistent with an interrupted recording" and never as proof of one.
