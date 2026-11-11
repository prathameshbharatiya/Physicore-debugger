"""REAL ROS 2 / rosbag2 integration tests. They need a sourced ROS 2 environment and are SKIPPED
otherwise. As delivered, they were NOT EXECUTED (no ROS 2 was available while developing: see
DAY2_IMPLEMENTATION_REPORT.md). Run them on a Linux machine with ROS 2 + rosbag2_storage_mcap:

    source /opt/ros/<distro>/setup.bash
    pytest tests/ros2_integration -m ros2_integration -s

Test 1 verifies the supervisor against the real `ros2 bag record`.
Test 2 is the bounded snapshot feasibility experiment: it MEASURES and prints; its assertions are
loose sanity bounds, not performance claims. Results are written to $PHYSICORE_SNAPSHOT_RESULTS
(default: tests/ros2_integration/snapshot_results.json) so they can be attached to the report.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from physicore_collector.bag import scan_bag
from physicore_collector.capture_health import CaptureState
from physicore_collector.profile import load_profile
from physicore_collector.recorder import Rosbag2CliAdapter
from physicore_collector.schemas import ArtifactReadability
from physicore_collector.session import analyze_session
from physicore_collector.supervisor import CaptureSupervisor

pytestmark = pytest.mark.ros2_integration


def _has_ros2() -> bool:
    if shutil.which("ros2") is None:
        return False
    r = subprocess.run(["ros2", "pkg", "prefix", "rosbag2_storage_mcap"], capture_output=True, text=True)
    return r.returncode == 0


requires_ros2 = pytest.mark.skipif(not _has_ros2(), reason="ROS 2 with rosbag2_storage_mcap is not available in this environment")

TOPIC = "/pc_test"
PROFILE = f"""\
profile_schema_version: "1.1"
profile_id: ros2-integration
topics:
  - {{name: {TOPIC}, expected_hz: 20}}
capture:
  stop_timeout_seconds: 20
"""


def _publisher(rate_hz: int = 50) -> subprocess.Popen:
    return subprocess.Popen(
        ["ros2", "topic", "pub", "-r", str(rate_hz), TOPIC, "std_msgs/msg/String", "{data: pc}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def _stop(p: subprocess.Popen) -> None:
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGINT)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)


@requires_ros2
def test_supervisor_with_real_rosbag2(tmp_path):
    prof = tmp_path / "p.yaml"
    prof.write_text(PROFILE)
    out = tmp_path / "caps"
    out.mkdir()
    pub = _publisher()
    try:
        time.sleep(2)  # let discovery find the publisher
        r = CaptureSupervisor(Rosbag2CliAdapter()).run(load_profile(prof), out, duration_seconds=4, session_id="real")
    finally:
        _stop(pub)
    h = r.health
    print("capture state:", h.state.value, "exit:", h.recorder.exit_code, "finalized:", h.bag_finalization.appears_finalized)
    print("recorder output tail:", *h.recorder.stderr_tail[-10:], sep="\n  ")
    assert h.state is CaptureState.STOPPED_CLEAN, h.errors
    ctx = analyze_session(r.bag_dir, prof, health_path=r.health_path)
    print("validation status:", ctx.report.status.value)
    for c in ctx.report.checks:
        print(f"  [{c.outcome.value}] {c.check_id}: {c.message}")
    assert ctx.scan.inspection.readability is ArtifactReadability.READABLE
    assert ctx.scan.message_count_for_topic(TOPIC) > 0
    assert ctx.bag.info.metadata_valid


def _vmhwm_kib(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


@requires_ros2
def test_snapshot_feasibility_experiment(tmp_path):
    """Direct use of rosbag2 snapshot mode (the Collector deliberately does NOT expose it yet).

    Records into rosbag2's in-memory circular buffer (--max-cache-duration CACHE_S), publishes for
    RUN_S seconds, triggers the documented ~/snapshot service once, then stops. Measures:
      trigger_service_latency_s, pre_trigger_coverage_s, post_trigger_coverage_s, segment count,
      recorder peak RSS (VmHWM), and whether the Collector sees a consistent bag afterwards.
    """
    CACHE_S, RUN_S = 5, 9
    bag = tmp_path / "snap"
    rec = subprocess.Popen(
        ["ros2", "bag", "record", "--output", str(bag), "--storage", "mcap", "--snapshot-mode",
         "--max-cache-size", "0", "--max-cache-duration", str(CACHE_S), TOPIC],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True,
    )
    pub = _publisher(50)
    results: dict = {"cache_duration_s": CACHE_S, "publish_rate_hz": 50, "run_s": RUN_S}
    try:
        time.sleep(RUN_S)
        results["recorder_peak_rss_kib_before_trigger"] = _vmhwm_kib(rec.pid)
        t0 = time.time()
        call = subprocess.run(
            ["ros2", "service", "call", "/rosbag2_recorder/snapshot", "rosbag2_interfaces/srv/Snapshot"],
            capture_output=True, text=True, timeout=60,
        )
        t1 = time.time()
        results["trigger_wall_clock_unix_ns"] = int(t0 * 1e9)
        results["trigger_service_latency_s"] = round(t1 - t0, 3)
        results["service_output"] = call.stdout.strip()[-300:]
        time.sleep(1.0)
    finally:
        _stop(pub)
        os.killpg(rec.pid, signal.SIGINT)
        try:
            rec.wait(timeout=30)
            results["recorder_exit_code"] = rec.returncode
        except subprocess.TimeoutExpired:
            os.killpg(rec.pid, signal.SIGKILL)
            results["recorder_exit_code"] = "killed_after_30s"
    results["recorder_stderr_tail"] = (rec.stderr.read().decode(errors="replace").splitlines()[-15:] if rec.stderr else [])

    scan, analysis = scan_bag(bag)
    w = scan.inspection.recording_window
    results.update(
        collector_readability=scan.readability.value,
        segment_count=len(analysis.info.segments),
        metadata_present=analysis.info.metadata_present,
        message_count=scan.inspection.total_message_count,
    )
    if w is not None:
        # NOTE: compares recorder log_time with this test's wall clock. Valid only because both
        # are the same host's system clock here; the Collector itself never makes this assumption.
        trig = results["trigger_wall_clock_unix_ns"]
        results["pre_trigger_coverage_s"] = round((trig - w.min_log_time_ns) / 1e9, 3)
        results["post_trigger_coverage_s"] = round((w.max_log_time_ns - trig) / 1e9, 3)
    path = Path(os.environ.get("PHYSICORE_SNAPSHOT_RESULTS", Path(__file__).with_name("snapshot_results.json")))
    path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))

    # loose sanity bounds only; the printed numbers are the experiment's result
    assert scan.inspection.total_message_count > 0, "snapshot produced no messages"
    assert analysis.info.metadata_present
    assert w is not None and results["pre_trigger_coverage_s"] > 0
    assert results["pre_trigger_coverage_s"] <= CACHE_S + 2
