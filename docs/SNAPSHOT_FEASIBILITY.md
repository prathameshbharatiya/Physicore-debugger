# Snapshot ("incident trigger") capture: feasibility (bounded experiment)

**Status: analysis from upstream source is done; the measurement experiment is written but was NOT executed** (no ROS 2 in the development environment). Nothing below is a measured number. Run `tests/ros2_integration/test_real_ros2.py::test_snapshot_feasibility_experiment` on a ROS 2 machine to obtain them (it writes `snapshot_results.json`).

Source examined: `ros2/rosbag2` (cloned at the time of work, Apache-2.0): `ros2bag/verb/record.py`, `rosbag2_cpp/.../sequential_writer.cpp`, `cache/message_cache*.cpp`, `rosbag2_transport/.../recorder.cpp`, `rosbag2_interfaces/srv/Snapshot.srv`.

## What rosbag2 provides (read from source)

| Fact | Where |
|---|---|
| `ros2 bag record --snapshot-mode` keeps messages only in an in-memory **circular** buffer sized by `--max-cache-size` (bytes) and/or `--max-cache-duration` (seconds); snapshot mode throws if both are 0 | `record.py`, `sequential_writer.cpp:164` |
| A snapshot is triggered by the service `<recorder node>/snapshot` (`rosbag2_interfaces/srv/Snapshot`, response `bool success`), exposed only in snapshot mode; it fails if not recording | `recorder.cpp:~845`, `Snapshot.srv` |
| `take_snapshot()` flushes the buffer to storage and then **splits the bag file**: each trigger yields a new storage file inside the same bag; the recorder keeps running | `sequential_writer.cpp:547` |
| Old messages are silently evicted from the buffer; a message larger than the buffer is dropped with a log warning | `message_cache_circular_buffer.cpp` |
| Transient-local messages that predate the window are re-added to the snapshot with their timestamp **rewritten** to the earliest snapshot timestamp | `sequential_writer.cpp:772-784` |
| Nothing reaches disk until the first trigger; `metadata.yaml` is written when the writer closes | `sequential_writer.cpp` (close) |

## Consequences for evidence semantics (the reason it is not "just a flag")

1. **Cadence checks are meaningless across snapshots.** Windows are separated by design; the existing cadence analysis would flag the gap between snapshot N and N+1 as a failure. `metadata.yaml` carries no snapshot-mode marker (checked in `yaml.hpp`), so the Collector cannot detect a snapshot bag from the bag alone: the capture-health sidecar would have to declare the mode, and validation would have to evaluate cadence per window.
2. **`log_time` of re-added transient-local messages is not their receive time.** Any timebase or trigger comparison must exclude or flag them.
3. **Loss is expected, not exceptional**: eviction is the design. "Complete" must never be claimed; the honest statuses are about *which window was captured around which trigger*, not whether everything was recorded.
4. **Crash before a trigger leaves no recording at all**; after a trigger the file is only as good as a clean close. The existing `appears_finalized` logic applies unchanged.
5. **Trigger identity/latency is unobservable offline.** The service returns only `success`; when the trigger was *requested* vs when the window was frozen is not recorded by rosbag2. The supervisor would have to timestamp the request on its own clock (not comparable to `log_time`, see `capture_clock_not_comparable`).
6. **Memory**: the circular cache is held in RAM (the buffer plus the consumer side); sizing must be planned per robot. Not measured here.

## Experiment (executable, unexecuted)

`test_snapshot_feasibility_experiment` runs the real recorder in snapshot mode with a 5 s window, publishes at 50 Hz for 9 s, calls the snapshot service once, and records: service latency, pre-/post-trigger coverage (vs the test host's clock, valid only because publisher, recorder and test share one host), segment count, recorder peak RSS, recorder exit code, and what the Collector's `scan_bag` makes of the result. Its assertions are loose sanity bounds; **the printed JSON is the result**.

## Recommendation (decision, not a promise)

Do **not** add snapshot capture to the Collector yet. What a safe version needs, in order: (1) run the experiment on the target distro/RMW and review the numbers; (2) a `capture.mode: snapshot` setting recorded in the sidecar with the cache limits; (3) per-window cadence evaluation and a status vocabulary that never says `complete`; (4) trigger-request timestamps with an explicit "not comparable to log_time" note; (5) a decision on transient-local rewriting. Until then the Collector's `capture run` cannot start snapshot mode, so a snapshot bag can only arrive as a foreign recording, where cadence failures at window boundaries are expected and documented here.
