"""Supervisor lifecycle with a scripted adapter (no ROS 2) + real-subprocess adapter tests
against a stand-in `ros2` script. Neither proves behaviour of the real rosbag2."""

import json
import signal
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from physicore_collector.capture_health import CaptureHealth, CaptureState, load_health
from physicore_collector.errors import CaptureConfigError, CaptureSessionExistsError, HealthError
from physicore_collector.profile import load_profile
from physicore_collector.recorder import Rosbag2CliAdapter
from physicore_collector.schemas import ArtifactReadability, SessionStatus
from physicore_collector.session import analyze_session
from physicore_collector.export import export_package, verify_package
from physicore_collector.supervisor import CaptureSupervisor
from tests.recorder_doubles import FakeAdapter, FakeClock, make_fake_ros2

PROFILE_11 = """\
profile_schema_version: "1.1"
profile_id: cap-test
log_time_basis: system_time
required_metadata_keys: [operator, session_label]
topics:
  - {name: /joint_states, expected_hz: 50}
  - {name: /camera/image, max_gap_seconds: 0.5}
  - {name: /diagnostics, required: false}
capture:
  max_bag_size_bytes: 1000000
  max_bag_duration_seconds: 30
  stop_timeout_seconds: 5
  identity: {robot_id: r1, run_id: run-7}
"""


@pytest.fixture
def prof(tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text(PROFILE_11)
    return load_profile(p)


@pytest.fixture
def out(tmp_path):
    d = tmp_path / "captures"
    d.mkdir()
    return d


def run(adapter, prof, out, clock=None, **kw):
    clock = clock or FakeClock()
    adapter.clock = clock
    return CaptureSupervisor(adapter, clock=clock, poll_interval=0.1).run(prof, out, session_id="s1", **kw)


# -------------------------------------------------------------- lifecycle with the double


def test_successful_lifecycle(prof, out):
    a = FakeAdapter("clean")
    r = run(a, prof, out, duration_seconds=1.0)
    h = r.health
    assert h.state is CaptureState.STOPPED_CLEAN and h.recorder.exit_code == 0
    assert h.bag_finalization.appears_finalized is True
    assert h.output.readability is ArtifactReadability.READABLE and h.output.segment_count == 1
    assert [e.kind for e in h.events] == ["capture_started", "recorder_started", "stop_requested", "recorder_exited", "output_inspected"]
    assert a.calls.index("request_stop") < a.calls.index("wait:5")
    assert h.timing.clock_compatible_with_log_time == "not_established"
    assert h.requested.identity.robot_id == "r1" and h.requested.max_bag_size_bytes == 1000000
    assert h.recorder_statistics.available is False
    # command is sanitized and carries no host path
    assert h.recorder.command[0] == "ros2" and "{SESSION_DIR}/bag" in h.recorder.command
    assert str(out) not in json.dumps(h.model_dump(mode="json"))
    # on-disk sidecar equals the returned object and re-validates
    assert load_health(r.health_path).health == h


def test_graceful_interruption_via_event(prof, out):
    ev = threading.Event()
    a = FakeAdapter("clean")
    clock = FakeClock()
    orig = clock.sleep
    n = {"i": 0}
    def sleep(s):
        n["i"] += 1
        if n["i"] == 3:
            ev.set()
        orig(s)
    clock.sleep = sleep
    r = run(a, prof, out, clock=clock, stop_event=ev)
    assert r.health.state is CaptureState.STOPPED_CLEAN
    assert next(e for e in r.health.events if e.kind == "stop_requested").detail == "stop requested"


def test_keyboard_interrupt_is_treated_as_a_graceful_stop(prof, out):
    a = FakeAdapter("clean")
    clock = FakeClock()
    def sleep(s):
        raise KeyboardInterrupt
    clock.sleep = sleep
    r = run(a, prof, out, clock=clock)
    assert r.health.state is CaptureState.STOPPED_CLEAN
    assert next(e for e in r.health.events if e.kind == "stop_requested").detail == "keyboard interrupt"


def test_startup_failure(prof, out):
    r = run(FakeAdapter("start_fail"), prof, out)
    h = r.health
    assert h.state is CaptureState.FAILED_TO_START
    assert any("ros2" in e for e in h.errors)
    assert h.bag_finalization.appears_finalized is None  # unknown, not False
    assert h.output.bag_directory_present is False and not h.output.inspected
    assert not r.bag_dir.exists()


def test_unexpected_recorder_exit(prof, out):
    r = run(FakeAdapter("exit_early"), prof, out, duration_seconds=60)
    h = r.health
    assert h.state is CaptureState.EXITED_UNEXPECTEDLY and h.recorder.exit_code == 7
    assert h.interruptions and "before the capture was stopped" in h.interruptions[0]
    assert h.bag_finalization.appears_finalized is False  # nothing was produced
    assert any("no bag directory" in e or "expected output missing" in e for e in h.errors)


def test_unexpected_exit_with_output_is_never_labelled_finalized(prof, out):
    r = run(FakeAdapter("exit_early_with_output"), prof, out, duration_seconds=60)
    h = r.health
    assert h.state is CaptureState.EXITED_UNEXPECTEDLY
    assert h.output.readability is ArtifactReadability.READABLE  # a perfectly good-looking bag...
    assert h.bag_finalization.appears_finalized is None  # ...still cannot be affirmed finalized
    # downstream: validation refuses to call it complete
    ctx = analyze_session(r.bag_dir, prof.path, health_path=r.health_path)
    assert ctx.report.status is SessionStatus.PARTIAL
    assert next(c for c in ctx.report.checks if c.check_id == "capture:lifecycle").outcome.value == "fail"


def test_stop_timeout_kills_and_marks_interrupted(prof, out):
    a = FakeAdapter("stop_hangs")
    r = run(a, prof, out, duration_seconds=0.3)
    h = r.health
    assert h.state is CaptureState.STOP_TIMEOUT_KILLED
    assert "kill" in a.calls and a.calls.index("request_stop") < a.calls.index("kill")
    assert any("did not stop within 5 s" in i for i in h.interruptions)
    assert h.bag_finalization.appears_finalized is False  # partial output without metadata.yaml
    assert h.output.metadata_present is False


def test_stop_with_nonzero_exit(prof, out):
    r = run(FakeAdapter("stop_error"), prof, out, duration_seconds=0.2)
    assert r.health.state is CaptureState.STOPPED_WITH_ERROR
    assert r.health.bag_finalization.appears_finalized is not True


def test_missing_expected_output(prof, out):
    r = run(FakeAdapter("no_output"), prof, out, duration_seconds=0.2)
    h = r.health
    assert h.state is CaptureState.STOPPED_CLEAN  # the recorder said it was fine...
    assert h.output.bag_directory_present is False
    assert h.bag_finalization.appears_finalized is False  # ...but there is nothing: not finalized
    assert any("expected output missing" in e for e in h.errors)


def test_partial_output_after_failure_is_preserved_and_reported(prof, out):
    r = run(FakeAdapter("partial_output"), prof, out, duration_seconds=0.2)
    assert r.bag_dir.is_dir()
    h = r.health
    assert h.output.metadata_present is False and h.output.segment_count == 1
    assert h.bag_finalization.appears_finalized is False
    assert len(h.output.files) == 1  # hashed so export can prove it is unchanged


def test_existing_session_directory_is_never_overwritten(prof, out):
    (out / "s1").mkdir()
    (out / "s1" / "precious.txt").write_text("keep")
    with pytest.raises(CaptureSessionExistsError):
        run(FakeAdapter(), prof, out, duration_seconds=0.1)
    assert (out / "s1" / "precious.txt").read_text() == "keep"


def test_missing_output_root_and_bad_duration(prof, tmp_path):
    with pytest.raises(CaptureConfigError, match="does not exist"):
        run(FakeAdapter(), prof, tmp_path / "nope", duration_seconds=1)
    with pytest.raises(CaptureConfigError, match="greater than 0"):
        run(FakeAdapter(), prof, tmp_path, duration_seconds=0)


def test_invalid_session_id(prof, out):
    with pytest.raises(CaptureConfigError, match="Invalid session id"):
        CaptureSupervisor(FakeAdapter(), clock=FakeClock()).run(prof, out, session_id="../evil", duration_seconds=1)


@pytest.mark.parametrize("topic", ["-a", "no_slash"])
def test_option_like_or_unqualified_topic_names_are_refused_before_starting(tmp_path, out, topic):
    p = tmp_path / "bad.yaml"
    p.write_text(f'profile_schema_version: "1.0"\nprofile_id: x\ntopics:\n  - name: "{topic}"\n')
    a = FakeAdapter()
    with pytest.raises(CaptureConfigError, match="fully qualified"):
        CaptureSupervisor(a, clock=FakeClock()).run(load_profile(p), out, duration_seconds=1)
    assert "start" not in a.calls and not list(out.iterdir())  # nothing created, nothing started


def test_min_free_disk_is_enforced(tmp_path, out):
    p = tmp_path / "d.yaml"
    p.write_text('profile_schema_version: "1.1"\nprofile_id: x\ntopics: [{name: /a}]\ncapture: {min_free_disk_bytes: 1000000000000000}\n')
    a = FakeAdapter()
    with pytest.raises(CaptureConfigError, match="below capture.min_free_disk_bytes"):
        CaptureSupervisor(a, clock=FakeClock()).run(load_profile(p), out, duration_seconds=1)
    assert not list(out.iterdir())


def test_supervisor_bug_is_recorded_recorder_killed_and_error_reraised(prof, out):
    a = FakeAdapter("clean")
    clock = FakeClock()
    def boom(s): raise RuntimeError("boom")
    clock.sleep = boom
    with pytest.raises(RuntimeError, match="boom"):
        run(a, prof, out, clock=clock)
    assert "kill" in a.calls
    h = load_health(out / "s1" / "capture_health.json").health
    assert h.state is CaptureState.SUPERVISOR_ERROR and any("boom" in e for e in h.errors)


def test_sidecar_written_while_running_reads_as_interrupted(prof, out):
    """If the supervisor itself dies, the last sidecar says 'running'; validation treats that as interrupted."""
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    d = json.loads(r.health_path.read_text())
    d["state"] = "running"
    d["bag_finalization"] = {"appears_finalized": None, "basis": "not evaluated"}
    r.health_path.write_text(json.dumps(d))
    ctx = analyze_session(r.bag_dir, prof.path, health_path=r.health_path)
    c = next(c for c in ctx.report.checks if c.check_id == "capture:lifecycle")
    assert c.outcome.value == "fail" and "still running" in c.message
    assert ctx.report.status is SessionStatus.PARTIAL


# -------------------------------------------------------------- sidecar schema


def test_sidecar_schema_rejects_unearned_finalized_and_unknown_env(prof, out):
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    base = json.loads(r.health_path.read_text())
    bad = json.loads(json.dumps(base)); bad["state"] = "exited_unexpectedly"
    with pytest.raises(ValidationError, match="appears_finalized=true is invalid"):
        CaptureHealth.model_validate(bad)
    bad = json.loads(json.dumps(base)); bad["environment"] = {"AWS_SECRET_ACCESS_KEY": "x"}
    with pytest.raises(ValidationError, match="environment may only contain"):
        CaptureHealth.model_validate(bad)
    bad = json.loads(json.dumps(base)); bad["capture_health_schema_version"] = "9.9"
    with pytest.raises(ValidationError, match="unsupported capture_health_schema_version"):
        CaptureHealth.model_validate(bad)
    bad = json.loads(json.dumps(base)); bad["recorder_statistics"] = {"available": True}
    with pytest.raises(ValidationError):
        CaptureHealth.model_validate(bad)  # cannot claim statistics rosbag2 does not expose


def test_no_secrets_in_sidecar(prof, out, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "supersecret")
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    text = r.health_path.read_text()
    assert "supersecret" not in text and "AWS_SECRET" not in text
    assert r.health.environment == {"ROS_DISTRO": "jazzy"}


def test_load_health_errors(tmp_path):
    with pytest.raises(HealthError, match="not found"):
        load_health(tmp_path / "x.json")
    p = tmp_path / "x.json"; p.write_text("{not json")
    with pytest.raises(HealthError, match="not valid JSON"):
        load_health(p)
    p.write_text('{"a": 1}')
    with pytest.raises(HealthError, match="failed validation"):
        load_health(p)
    p.write_text('{"a": 1, "a": 2}')
    with pytest.raises(HealthError, match="not valid JSON"):
        load_health(p)


# -------------------------------------------------------------- supervisor output -> existing pipeline


def test_capture_output_flows_into_validation_and_export(prof, out, tmp_path):
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    ctx = analyze_session(r.bag_dir, prof.path, health_path=r.health_path)
    assert ctx.report.status is SessionStatus.COMPLETE, [c.message for c in ctx.report.checks if c.outcome.value != "pass"]
    ids = {c.check_id for c in ctx.report.checks}
    assert {"capture:lifecycle", "capture:output_unchanged", "bag:metadata"} <= ids
    res = export_package(ctx, tmp_path / "pkg")
    assert verify_package(tmp_path / "pkg") == []
    m = res.manifest
    assert m.manifest_schema_version == "0.2" and m.recording_kind == "rosbag2_directory"
    assert m.capture_health.state == "stopped_clean" and m.capture_health.appears_finalized is True
    roles = {a.role.value for a in m.artifacts}
    assert {"recording", "bag_metadata", "capture_health", "profile", "validation_report"} <= roles
    gaps = {g.code for g in m.known_gaps}
    assert {"capture_clock_not_comparable", "recorder_statistics_unavailable"} <= gaps
    assert "capture_health_unavailable" not in gaps


def test_bag_modified_after_capture_is_detected(prof, out):
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    seg = next(r.bag_dir.glob("*.mcap"))
    seg.write_bytes(seg.read_bytes()[:-1] + b"\x00")
    ctx = analyze_session(r.bag_dir, prof.path, health_path=r.health_path)
    c = next(c for c in ctx.report.checks if c.check_id == "capture:output_unchanged")
    assert c.outcome.value == "fail" and "differs" in c.message
    assert ctx.report.status is not SessionStatus.COMPLETE


def test_bag_without_sidecar_records_the_gap(prof, out):
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    ctx = analyze_session(r.bag_dir, prof.path)
    from physicore_collector.manifest import build_known_gaps
    assert "capture_health_unavailable" in {g.code for g in build_known_gaps(ctx)}


def test_health_with_single_mcap_is_rejected(prof, out, fx):
    from physicore_collector.errors import InputError
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    with pytest.raises(InputError, match="rosbag2 bag directory"):
        analyze_session(fx / "valid.mcap", prof.path, health_path=r.health_path)


# -------------------------------------------------------------- real subprocess, stand-in ros2


@pytest.fixture
def fake_ros2(tmp_path):
    d = tmp_path / "bin"; d.mkdir()
    return make_fake_ros2(d)


def real_run(fake, prof, out, **kw):
    return CaptureSupervisor(Rosbag2CliAdapter(str(fake)), poll_interval=0.05).run(prof, out, session_id="s1", **kw)


def test_adapter_delegates_to_ros2_bag_record_with_documented_options(fake_ros2, prof, out, tmp_path, monkeypatch):
    args_file = tmp_path / "args.txt"
    monkeypatch.setenv("FAKE_ROS2_ARGS_FILE", str(args_file))
    r = real_run(fake_ros2, prof, out, duration_seconds=0.5)
    args = args_file.read_text().split("\n")
    assert args[:2] == ["bag", "record"]
    assert args[args.index("--storage") + 1] == "mcap"
    assert args[args.index("--max-bag-size") + 1] == "1000000"
    assert args[args.index("--max-bag-duration") + 1] == "30"
    assert args[-3:] == ["/joint_states", "/camera/image", "/diagnostics"]
    assert args[args.index("--output") + 1] == str(r.bag_dir)
    assert r.health.state is CaptureState.STOPPED_CLEAN  # SIGINT -> finalize -> exit 0
    assert r.health.bag_finalization.appears_finalized is True
    assert "fake ros2 started" in r.health.recorder.stderr_tail[0]


def test_supervisor_has_no_recording_code():
    """The Collector must not contain its own recorder: no rclpy / message subscription / MCAP writing."""
    src = Path(__file__).parent.parent / "src" / "physicore_collector"
    for f in src.glob("*.py"):
        text = f.read_text()
        assert "import rclpy" not in text and "from rclpy" not in text, f.name
        assert "create_subscription" not in text, f.name
        assert "mcap.writer" not in text, f.name  # writers live only in tests/make_fixtures.py


def test_real_process_startup_failure(fake_ros2, prof, out, monkeypatch):
    monkeypatch.setenv("FAKE_ROS2_MODE", "start_fail")
    r = real_run(fake_ros2, prof, out, duration_seconds=5)
    assert r.health.state is CaptureState.EXITED_UNEXPECTEDLY  # started, then died with status 3
    assert r.health.recorder.exit_code == 3
    assert any("cannot start" in l for l in r.health.recorder.stderr_tail)


def test_real_process_crash(fake_ros2, prof, out, monkeypatch):
    monkeypatch.setenv("FAKE_ROS2_MODE", "crash")
    r = real_run(fake_ros2, prof, out, duration_seconds=5)
    assert r.health.state is CaptureState.EXITED_UNEXPECTEDLY and r.health.recorder.exit_code == 5


def test_real_process_missing_executable(prof, out, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    r = CaptureSupervisor(Rosbag2CliAdapter(), poll_interval=0.05).run(prof, out, session_id="s1", duration_seconds=1)
    assert r.health.state is CaptureState.FAILED_TO_START
    assert "Source your ROS 2 setup" in r.health.errors[0]


def test_real_process_that_ignores_sigint_is_killed_within_the_bound(fake_ros2, prof, out, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_ROS2_MODE", "ignore_sigint")
    p = tmp_path / "fast.yaml"
    p.write_text(PROFILE_11.replace("stop_timeout_seconds: 5", "stop_timeout_seconds: 0.5"))
    t0 = time.monotonic()
    r = real_run(fake_ros2, load_profile(p), out, duration_seconds=0.3)
    assert time.monotonic() - t0 < 5
    assert r.health.state is CaptureState.STOP_TIMEOUT_KILLED
    assert r.health.recorder.terminated_by_signal == 9 and r.health.recorder.exit_code is None
    assert not r.health.bag_finalization.appears_finalized


def test_no_orphaned_children_after_kill(fake_ros2, prof, out, monkeypatch, tmp_path):
    child = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_ROS2_MODE", "spawn_child")
    monkeypatch.setenv("FAKE_ROS2_CHILD_FILE", str(child))
    p = tmp_path / "fast.yaml"
    p.write_text(PROFILE_11.replace("stop_timeout_seconds: 5", "stop_timeout_seconds: 0.5"))
    # make the recorder not finalize on SIGINT so that it is force-killed with its child
    script = fake_ros2.read_text().replace("signal.signal(signal.SIGINT, finalize)", "signal.signal(signal.SIGINT, signal.SIG_IGN)")
    fake_ros2.write_text(script)
    r = real_run(fake_ros2, load_profile(p), out, duration_seconds=0.5)
    pid = int(child.read_text())
    time.sleep(0.2)
    # the grandchild must be gone, or at most a zombie awaiting reaping by init (dead, not running)
    stat = Path(f"/proc/{pid}/stat")
    state = stat.read_text().split()[2] if stat.exists() else "gone"
    assert state in ("gone", "Z"), f"grandchild still alive in state {state}"
    assert r.health.state is CaptureState.STOP_TIMEOUT_KILLED


# -------------------------------------------------------------- recorder-reported message loss


def test_recorder_loss_warning_is_kept_verbatim_and_blocks_complete(fake_ros2, prof, out, monkeypatch):
    monkeypatch.setenv("FAKE_ROS2_MODE", "reports_loss")
    # reports_loss mode otherwise behaves like normal: finalizes on SIGINT and exits 0
    r = real_run(fake_ros2, prof, out, duration_seconds=0.5)
    h = r.health
    assert h.state is CaptureState.STOPPED_CLEAN  # the recorder exited 0 ...
    assert any("Total lost: 12" in l for l in h.recorder.reported_loss_lines)  # ... but told us it lost messages
    assert any("reported dropped" in w for w in h.warnings)
    ctx = analyze_session(r.bag_dir, prof.path, health_path=r.health_path)
    c = next(c for c in ctx.report.checks if c.check_id == "capture:recorder_reported_loss")
    assert c.outcome.value == "fail" and "known to be missing data" in c.message
    assert ctx.report.status is SessionStatus.PARTIAL
    from physicore_collector.manifest import build_known_gaps
    assert "recorder_reported_message_loss" in {g.code for g in build_known_gaps(ctx)}


def test_no_loss_lines_means_no_claim_either_way(prof, out):
    r = run(FakeAdapter("clean"), prof, out, duration_seconds=0.2)
    ctx = analyze_session(r.bag_dir, prof.path, health_path=r.health_path)
    assert "capture:recorder_reported_loss" not in {c.check_id for c in ctx.report.checks}  # absence is not a PASS
    assert "does NOT show that nothing was lost" in r.health.recorder_statistics.reason


def test_stop_before_the_recorder_is_ready_is_never_reported_as_clean(fake_ros2, prof, out, monkeypatch):
    """SIGINT arriving before the recorder can handle it kills it. That is a real race (stop right
    after start); it must surface as an unclean, non-finalized capture with nothing inventing a bag."""
    monkeypatch.setenv("FAKE_ROS2_MODE", "slow_start")
    r = real_run(fake_ros2, prof, out, duration_seconds=0.2)
    h = r.health
    assert h.state is CaptureState.STOPPED_WITH_ERROR
    assert h.recorder.terminated_by_signal == signal.SIGINT and h.recorder.exit_code is None
    assert h.bag_finalization.appears_finalized is False and h.output.bag_directory_present is False
    assert any("expected output missing" in e for e in h.errors)
