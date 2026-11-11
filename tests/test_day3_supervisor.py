"""Day 3 supervisor behaviour with a scripted recorder (no ROS 2, no real processes):
heartbeat, disk thresholds, stop requests, locks, adoption, write failures, Day 2 compatibility."""

import json
import os
from collections import namedtuple
from pathlib import Path

import pytest

from physicore_collector import procident as pi
from physicore_collector import supervisor as sv
from physicore_collector.capture_health import CaptureHealth, CaptureState, load_health
from physicore_collector.errors import CaptureConfigError, CaptureError
from physicore_collector.profile import load_profile
from physicore_collector.schemas import SessionStatus
from physicore_collector.session import analyze_session
from physicore_collector.sessionlock import FileLock, lock_is_held_elsewhere
from physicore_collector.supervisor import CaptureSupervisor, StopRequest
from tests.recorder_doubles import FakeAdapter, FakeClock
from tests.test_supervisor import PROFILE_11

PROFILE_12 = PROFILE_11.replace('profile_schema_version: "1.1"', 'profile_schema_version: "1.2"').replace(
    "capture:\n", "capture:\n  heartbeat_seconds: 1\n  disk_warn_free_bytes: 1000\n  disk_critical_free_bytes: 500\n")
Usage = namedtuple("Usage", "total used free")
COMPAT = Path(__file__).parent / "compat"


@pytest.fixture
def prof12(tmp_path):
    p = tmp_path / "p12.yaml"; p.write_text(PROFILE_12); return load_profile(p)


@pytest.fixture
def prof11(tmp_path):
    p = tmp_path / "p11.yaml"; p.write_text(PROFILE_11); return load_profile(p)


@pytest.fixture
def out(tmp_path):
    d = tmp_path / "caps"; d.mkdir(); return d


def sup(adapter, clock=None, disk=None):
    clock = clock or FakeClock()
    adapter.clock = clock
    return CaptureSupervisor(adapter, clock=clock, poll_interval=0.1, disk_usage=disk)


def free_disk(values):
    """disk_usage double returning successive `free` values (last one repeats)."""
    it = iter(values); last = [values[-1]]
    def f(path):
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return Usage(10_000, 10_000 - last[0], last[0])
    return f


# ------------------------------------------------------------------ heartbeat / identity / timing


def test_heartbeat_refreshes_the_record_without_growing_the_event_log(prof12, out):
    clock = FakeClock()
    writes = []
    orig = sv.write_health_atomic
    def spy(path, health):
        writes.append(health.timing.last_heartbeat_unix_ns); orig(path, health)
    sv.write_health_atomic = spy
    try:
        r = sup(FakeAdapter("clean"), clock).run(prof12, out, duration_seconds=5.5, session_id="s1")
    finally:
        sv.write_health_atomic = orig
    h = r.health
    assert [e.kind for e in h.events] == ["capture_started", "recorder_started", "stop_requested", "recorder_exited", "output_inspected"]
    beats = sorted({w for w in writes if w})
    assert len(beats) >= 5  # 1 s heartbeat over 5.5 s of (fake) time
    assert h.timing.last_heartbeat_unix_ns == max(beats)
    assert h.timing.supervisor_started_unix_ns <= h.timing.recorder_started_unix_ns <= h.timing.last_heartbeat_unix_ns
    assert h.timing.last_state_update_unix_ns >= h.timing.end_unix_ns
    assert h.requested.heartbeat_seconds == 1 and h.timing.stop_reason == "planned duration of 5.5 s elapsed"


def test_supervisor_identity_and_launch_mode_are_recorded(prof11, out):
    h = sup(FakeAdapter("clean")).run(prof11, out, duration_seconds=0.3, session_id="s1").health
    assert h.supervisor.identity is not None
    me = pi.identity_of(os.getpid())
    assert (h.supervisor.identity.pid, h.supervisor.identity.starttime_ticks, h.supervisor.identity.boot_id) == (me.pid, me.starttime_ticks, me.boot_id)
    assert h.supervisor.launch_mode == "foreground" and h.supervisor.service_name is None
    h2 = sup(FakeAdapter("clean")).run(prof11, out, duration_seconds=0.3, session_id="s2", launch_mode="service", service_name="robot-a").health
    assert h2.supervisor.launch_mode == "service" and h2.supervisor.service_name == "robot-a"


def test_profile_copy_is_byte_identical_and_no_host_paths_leak(prof11, out):
    r = sup(FakeAdapter("clean")).run(prof11, out, duration_seconds=0.3, session_id="s1")
    assert (out / "s1" / "profile.yaml").read_bytes() == prof11.path.read_bytes()
    assert str(out) not in r.health_path.read_text() and str(prof11.path) not in r.health_path.read_text()


# ------------------------------------------------------------------ disk


def test_disk_warning_degrades_but_does_not_block_complete(prof12, out):
    r = sup(FakeAdapter("clean"), disk=free_disk([5000, 5000, 900, 900, 900])).run(prof12, out, duration_seconds=4, session_id="s1")
    h = r.health
    assert h.state is CaptureState.STOPPED_CLEAN and h.disk.level == "warning" and h.disk.lowest_free_bytes == 900
    assert [e.kind for e in h.events].count("disk_warning") == 1  # a transition, not a log line per heartbeat
    ctx = analyze_session(r.bag_dir, prof12.path, health_path=r.health_path)
    c = next(c for c in ctx.report.checks if c.check_id == "capture:disk")
    assert c.outcome.value == "fail" and not c.required and "does not show that write throughput" in c.message
    assert ctx.report.status is SessionStatus.COMPLETE  # a warning is reported, not blocking


def test_disk_critical_stops_gracefully_and_never_claims_a_clean_complete_capture(prof12, out):
    a = FakeAdapter("clean")
    r = sup(a, disk=free_disk([5000, 5000, 400])).run(prof12, out, duration_seconds=60, session_id="s1")
    h = r.health
    assert h.state is CaptureState.STOPPED_DISK_CRITICAL
    assert a.calls.index("request_stop") < a.calls.index("wait:5") and "kill" not in a.calls  # graceful, bounded
    assert h.timing.stop_reason == "free disk space fell below the critical threshold"
    assert h.bag_finalization.appears_finalized is True  # the bag IS finalized ...
    assert any("ended earlier than intended" in i for i in h.interruptions)
    assert [e.kind for e in h.events].count("disk_critical") == 1
    ctx = analyze_session(r.bag_dir, prof12.path, health_path=r.health_path)
    life = next(c for c in ctx.report.checks if c.check_id == "capture:lifecycle")
    assert life.outcome.value == "fail" and "stopped_disk_critical" in life.message  # ... but the capture is not complete
    assert ctx.report.status is SessionStatus.PARTIAL


def test_disk_critical_stop_deletes_nothing(prof12, out):
    sentinel = out / "important.txt"; sentinel.write_text("keep")
    r = sup(FakeAdapter("clean"), disk=free_disk([5000, 400])).run(prof12, out, duration_seconds=60, session_id="s1")
    assert sentinel.read_text() == "keep" and any(r.bag_dir.iterdir())


def test_refuses_to_start_below_the_critical_threshold(prof12, out):
    a = FakeAdapter("clean")
    with pytest.raises(CaptureConfigError, match="already below capture.disk_critical_free_bytes"):
        sup(a, disk=free_disk([100])).run(prof12, out, duration_seconds=1, session_id="s1")
    assert "start" not in a.calls and not list(out.iterdir())


def test_unreadable_disk_space_is_a_warning_not_a_crash(prof12, out):
    n = {"i": 0}
    def flaky(path):
        n["i"] += 1
        if n["i"] > 1:  # the start check passes, later checks fail
            raise OSError(5, "Input/output error")
        return Usage(10_000, 0, 9_000)
    r = sup(FakeAdapter("clean"), disk=flaky).run(prof12, out, duration_seconds=3, session_id="s1")
    assert r.health.state is CaptureState.STOPPED_CLEAN and r.health.disk.level == "unknown"
    assert any("disk space could not be read" in w for w in r.health.warnings)


def test_no_thresholds_means_unknown_level_but_free_space_is_still_reported(prof11, out):
    r = sup(FakeAdapter("clean"), disk=free_disk([7000])).run(prof11, out, duration_seconds=2, session_id="s1")
    assert r.health.disk.level == "unknown" and r.health.disk.free_bytes == 7000


# ------------------------------------------------------------------ stop requests


def test_stop_request_file_stops_gracefully_and_records_the_reason(prof11, out):
    clock = FakeClock(); orig = clock.sleep; n = {"i": 0}
    def sleep(s):
        n["i"] += 1
        if n["i"] == 3:
            (out / "s1" / "stop_request.json").write_text(json.dumps({"reason": "end of shift"}))
        orig(s)
    clock.sleep = sleep
    r = sup(FakeAdapter("clean"), clock).run(prof11, out, session_id="s1")
    assert r.health.state is CaptureState.STOPPED_CLEAN
    assert r.health.timing.stop_reason == "operator request: end of shift"


def test_unreadable_stop_request_file_still_stops(prof11, out):
    clock = FakeClock(); orig = clock.sleep
    def sleep(s):
        (out / "s1" / "stop_request.json").write_bytes(b"\xff\xfe not json")
        orig(s)
    clock.sleep = sleep
    r = sup(FakeAdapter("clean"), clock).run(prof11, out, session_id="s1")
    assert r.health.state is CaptureState.STOPPED_CLEAN and "unreadable" in r.health.timing.stop_reason


def test_stop_request_object_carries_the_signal_reason(prof11, out):
    ev = StopRequest(); ev.request("signal SIGTERM")
    r = sup(FakeAdapter("clean")).run(prof11, out, stop_event=ev, session_id="s1")
    assert r.health.timing.stop_reason == "signal SIGTERM"
    ev2 = StopRequest(); ev2.request("first"); ev2.request("second")
    assert ev2.reason == "first"  # the first reason wins


# ------------------------------------------------------------------ locks


def test_session_lock_is_held_while_running_and_released_after(prof11, out):
    seen = {}
    class Probe(FakeAdapter):
        def poll(self):
            seen.setdefault("during", lock_is_held_elsewhere(out / "s1" / "supervisor.lock"))
            seen.setdefault("active", sv.read_lock_text(out / ".active-capture.lock"))
            return super().poll()
    sup(Probe("clean")).run(prof11, out, duration_seconds=0.3, session_id="s1")
    assert seen == {"during": True, "active": "s1"}
    assert lock_is_held_elsewhere(out / "s1" / "supervisor.lock") is False
    assert lock_is_held_elsewhere(out / ".active-capture.lock") is False


def test_second_capture_in_the_same_directory_cannot_record(prof11, out):
    inner = {}
    class Nested(FakeAdapter):
        def poll(self):
            if "r" not in inner:
                b = FakeAdapter("clean")
                inner["adapter"] = b
                inner["r"] = sup(b).run(prof11, out, duration_seconds=0.3, session_id="s2")
            return super().poll()
    r1 = sup(Nested("clean")).run(prof11, out, duration_seconds=0.3, session_id="s1")
    r2 = inner["r"]
    assert r1.health.state is CaptureState.STOPPED_CLEAN  # the first capture was unaffected
    assert r2.health.state is CaptureState.FAILED_TO_START
    assert "already active in this output directory (session 's1')" in r2.health.errors[0]
    assert "start" not in inner["adapter"].calls  # the second recorder was never started
    assert not r2.bag_dir.exists()


def test_two_supervisors_cannot_attach_to_one_session(prof11, out):
    s = sup(FakeAdapter("clean"))
    prepared = s.prepare(prof11, out, session_id="s1")
    held = FileLock(prepared.session_dir / "supervisor.lock"); held.acquire()
    try:
        with pytest.raises(CaptureError, match="already attached to session 's1'"):
            s.execute(prepared)
    finally:
        held.release()
    assert load_health(prepared.session_dir / "capture_health.json").health.state is CaptureState.STARTING  # untouched


# ------------------------------------------------------------------ prepare / adopt


def test_prepare_writes_a_starting_record_and_starts_nothing(prof11, out):
    a = FakeAdapter("clean")
    prepared = sup(a).prepare(prof11, out, session_id="s1", duration_seconds=7, launch_mode="detached")
    h = load_health(prepared.session_dir / "capture_health.json").health
    assert h.state is CaptureState.STARTING and h.supervisor.launch_mode == "detached" and h.requested.planned_duration_seconds == 7
    assert "start" not in a.calls and not prepared.bag_dir.exists()
    assert h.supervisor.identity is None  # nobody is supervising yet


def test_adopt_takes_over_a_prepared_session_once(prof11, out):
    a = FakeAdapter("clean")
    s = sup(a)
    s.prepare(prof11, out, session_id="s1", duration_seconds=0.3)
    r = s.adopt(out / "s1")
    assert r.health.state is CaptureState.STOPPED_CLEAN and r.health.requested.planned_duration_seconds == 0.3
    assert r.health.supervisor.launch_mode == "detached"
    with pytest.raises(CaptureError, match="its state is 'stopped_clean', not 'starting'"):
        s.adopt(out / "s1")  # never adopt twice


def test_adopt_rejects_a_tampered_profile_copy_and_records_the_failure(prof11, out):
    s = sup(FakeAdapter("clean"))
    s.prepare(prof11, out, session_id="s1")
    (out / "s1" / "profile.yaml").write_text(PROFILE_11 + "\n# tampered\n")
    with pytest.raises(CaptureError, match="does not match the recorded profile digest"):
        s.adopt(out / "s1")
    h = load_health(out / "s1" / "capture_health.json").health
    assert h.state is CaptureState.FAILED_TO_START and any("could not adopt" in e for e in h.errors)  # never left 'starting'


def test_adopt_a_session_without_a_record(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(CaptureError, match="Cannot adopt"):
        sup(FakeAdapter()).adopt(tmp_path / "x")


def test_prepare_refuses_a_profile_that_changed_after_loading(prof11, out):
    prof11.path.write_text(PROFILE_11 + "\n# edited after load\n")
    with pytest.raises(CaptureConfigError, match="changed after it was loaded"):
        sup(FakeAdapter()).prepare(prof11, out, session_id="s1")
    assert not list(out.iterdir())


# ------------------------------------------------------------------ write failures


def test_failing_health_writes_are_counted_and_do_not_kill_the_capture(prof11, out, capsys):
    orig = sv.write_health_atomic; n = {"i": 0}
    def flaky(path, health):
        n["i"] += 1
        if n["i"] in (3, 4):  # fail on two mid-run writes only
            raise OSError(28, "No space left on device")
        orig(path, health)
    sv.write_health_atomic = flaky
    try:
        r = sup(FakeAdapter("clean")).run(prof11, out, duration_seconds=7, session_id="s1")
    finally:
        sv.write_health_atomic = orig
    assert r.health.state is CaptureState.STOPPED_CLEAN and r.health.health_write_failures == 2
    assert load_health(r.health_path).health.health_write_failures == 2
    assert "No space left" in capsys.readouterr().err


# ------------------------------------------------------------------ compatibility with Day 2 artifacts


def test_day2_sidecar_still_loads_and_its_session_still_validates_and_exports(tmp_path):
    sess = COMPAT / "day2_session"
    h = load_health(sess / "capture_health.json").health
    assert h.capture_health_schema_version == "1.0" and h.state is CaptureState.STOPPED_CLEAN
    assert h.supervisor.identity is None and h.supervisor.launch_mode is None and h.disk.level == "unknown"
    prof = COMPAT / "day2_profile.yaml"
    ctx = analyze_session(sess / "bag", prof, health_path=sess / "capture_health.json")
    assert ctx.report.status is SessionStatus.COMPLETE, [c.message for c in ctx.report.checks if c.outcome.value != "pass"]
    from physicore_collector.export import export_package, verify_package
    res = export_package(ctx, tmp_path / "pkg")
    assert verify_package(tmp_path / "pkg") == [] and res.manifest.capture_health.health_schema_version == "1.0"


def test_day3_sidecar_roundtrips_and_rejects_unknown_versions(prof11, out):
    r = sup(FakeAdapter("clean")).run(prof11, out, duration_seconds=0.3, session_id="s1")
    d = json.loads(r.health_path.read_text())
    assert d["capture_health_schema_version"] == "1.1"
    assert CaptureHealth.model_validate(d) == r.health
    d["capture_health_schema_version"] = "1.2"
    with pytest.raises(Exception, match=r"supported: \['1.0', '1.1'\]"):
        CaptureHealth.model_validate(d)


def test_final_record_write_is_retried_and_failure_is_reported_not_swallowed(prof11, out, capsys):
    orig = sv.write_health_atomic
    n = {"i": 0}
    def full_disk(path, health):  # every write from the 3rd on fails: the FINAL outcome can never be persisted
        n["i"] += 1
        if n["i"] >= 3:
            raise OSError(28, "No space left on device")
        orig(path, health)
    sv.write_health_atomic = full_disk
    try:
        r = sup(FakeAdapter("clean")).run(prof11, out, duration_seconds=0.3, session_id="s1")
    finally:
        sv.write_health_atomic = orig
    assert r.record_persisted is False and r.health.state is CaptureState.STOPPED_CLEAN  # memory = truth
    assert load_health(r.health_path).health.state is CaptureState.RUNNING  # disk = stale, and we KNOW it
    err = capsys.readouterr().err
    assert "could NOT be written" in err and "capture recover" in err
    from physicore_collector.liveness import assess
    assert assess(load_health(r.health_path).health, r.session_dir).capture == "interrupted"  # stale => honest, never 'healthy'
