"""Day 3 lifecycle against REAL processes: detached supervisor + stand-in `ros2` recorder.

Real here: subprocesses, process groups, signals, SIGKILL, flock, /proc identity, timing.
NOT real: the recorder is a stand-in script (tests/recorder_doubles.py), not rosbag2, and a
'reboot' is simulated by editing a recorded boot id. See DAY3_IMPLEMENTATION_REPORT.md."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector import procident as pi
from physicore_collector.capture_health import CaptureState, load_health
from physicore_collector.cli import app
from physicore_collector.liveness import assess, supervisor_state
from physicore_collector.procident import ProcessIdentity
from physicore_collector.schemas import SessionStatus
from physicore_collector.session import analyze_session
from tests.recorder_doubles import make_fake_ros2
from tests.test_supervisor import PROFILE_11

runner = CliRunner()


def cli(*args):
    return runner.invoke(app, [str(a) for a in args])


@pytest.fixture
def env(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"; bindir.mkdir(); make_fake_ros2(bindir)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_ROS2_EARLY_BAG", "1")
    prof = tmp_path / "p.yaml"; prof.write_text(PROFILE_11)
    out = tmp_path / "caps"; out.mkdir()
    started = []
    yield type("Env", (), {"profile": prof, "out": out, "tmp": tmp_path, "started": started})
    for sess in started:  # never leave a process behind, whatever the test did
        try:
            h = load_health(sess / "capture_health.json").health
        except Exception:
            continue
        for rec in (h.recorder.identity, h.supervisor.identity):
            if rec is not None:
                pi.signal_if_same(ProcessIdentity(rec.pid, rec.starttime_ticks, rec.boot_id), signal.SIGKILL, group=True)
                pi.signal_if_same(ProcessIdentity(rec.pid, rec.starttime_ticks, rec.boot_id), signal.SIGKILL)


def start(env, sid, *extra, expect=0):
    r = cli("capture", "start", env.profile, "--out", env.out, "--session-id", sid, *extra)
    env.started.append(env.out / sid)
    assert r.exit_code == expect, r.output
    return env.out / sid


def health(sess):
    return load_health(sess / "capture_health.json").health


def state_of(sess):
    """None while the session directory exists but its first record is not written yet (a real,
    harmless window between mkdir and the STARTING record)."""
    try:
        return health(sess).state
    except Exception:
        return None


def wait_for(pred, timeout=15, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


def alive(ident):
    return pi.check_identity(ProcessIdentity(ident.pid, ident.starttime_ticks, ident.boot_id)) is pi.IdentityMatch.SAME


def supervisor_gone(sess, h):
    """The AUTHORITATIVE death signal is the released lock, not the pid: the kernel can show the
    thread-group leader as a zombie a few milliseconds before the shared fd table (and so the
    flock) is released, because the supervisor has helper threads. Wait for both."""
    return not alive(h.supervisor.identity) and supervisor_state(sess) == "dead"


def ready(sess):
    """the stand-in has created its in-progress bag: it is genuinely recording"""
    return wait_for(lambda: (sess / "bag").is_dir() and state_of(sess) is CaptureState.RUNNING and health(sess).recorder.identity is not None, what="recorder up")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ detached start / stop


def test_detached_start_status_stop_and_the_evidence_validates(env):
    sess = start(env, "s1")
    ready(sess)
    h = health(sess)
    assert h.state is CaptureState.RUNNING and h.supervisor.launch_mode == "detached"
    assert h.supervisor.identity is not None and alive(h.supervisor.identity) and alive(h.recorder.identity)
    assert (sess / "profile.yaml").read_bytes() == env.profile.read_bytes()
    st = cli("capture", "status", sess)
    assert st.exit_code == 0 and "assessment: HEALTHY" in st.output and "supervisor: alive" in st.output
    sp = cli("capture", "stop", sess)
    assert sp.exit_code == 0 and "FINALIZED" in sp.output and "stop reason: operator request: capture stop" in sp.output
    h = health(sess)
    assert h.state is CaptureState.STOPPED_CLEAN and h.bag_finalization.appears_finalized is True
    assert supervisor_state(sess) != "alive"  # `stop` returns only once the supervisor released its locks ...
    wait_for(lambda: not alive(h.supervisor.identity) and not alive(h.recorder.identity), what="processes gone")  # ... and both exit
    ctx = analyze_session(sess / "bag", env.profile, health_path=sess / "capture_health.json")
    assert ctx.report.status is SessionStatus.COMPLETE
    assert cli("session", "validate", sess, "--profile", env.profile).exit_code == 0


def test_stop_is_idempotent_and_a_second_stop_on_a_finished_capture_is_harmless(env):
    sess = start(env, "s1"); ready(sess)
    assert cli("capture", "stop", sess).exit_code == 0
    again = cli("capture", "stop", sess)
    assert again.exit_code == 0 and "Nothing to stop" in again.output


def test_duration_ends_a_detached_capture_on_its_own(env):
    # The duration clock starts when the recorder PROCESS starts (rosbag2 gives no readiness signal), so a
    # duration shorter than the recorder's start-up could interrupt its initialization: use a realistic one.
    sess = start(env, "s1", "--duration", "2.5")
    final = (CaptureState.STOPPED_CLEAN, CaptureState.STOPPED_WITH_ERROR, CaptureState.EXITED_UNEXPECTEDLY)
    wait_for(lambda: state_of(sess) in final, timeout=30, what="timed stop")
    h = health(sess)
    assert h.state is CaptureState.STOPPED_CLEAN, (h.state, h.errors)
    assert h.timing.stop_reason == "planned duration of 2.5 s elapsed"


def test_closing_the_terminal_does_not_stop_a_detached_capture(env):
    """Terminal-disconnect analogue: `capture start` runs inside a session whose leader is then
    sent SIGHUP and SIGKILL (what a closed terminal / dropped SSH does to its process group)."""
    script = f'{sys.executable} -m physicore_collector capture start {env.profile} --out {env.out} --session-id s1 && sleep 60'
    shell = subprocess.Popen(["bash", "-c", script], start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    env.started.append(env.out / "s1")
    sess = env.out / "s1"
    wait_for(lambda: state_of(sess) is CaptureState.RUNNING, what="running")
    sup, rec = health(sess).supervisor.identity, health(sess).recorder.identity
    os.killpg(shell.pid, signal.SIGHUP)
    time.sleep(0.3)
    os.killpg(shell.pid, signal.SIGKILL)
    shell.wait()
    time.sleep(0.8)
    assert alive(sup) and alive(rec), "the terminal's death killed the detached capture"
    assert health(sess).state is CaptureState.RUNNING
    assert cli("capture", "stop", sess).exit_code == 0 and health(sess).state is CaptureState.STOPPED_CLEAN


# ------------------------------------------------------------------ duplicate prevention


def test_concurrent_starts_cannot_create_two_recorders(env):
    sess1 = start(env, "s1"); ready(sess1)
    r = cli("capture", "start", env.profile, "--out", env.out, "--session-id", "s2")
    env.started.append(env.out / "s2")
    assert r.exit_code == 2 and "already active in this output directory (session 's1')" in r.output
    h2 = health(env.out / "s2")
    assert h2.state is CaptureState.FAILED_TO_START and h2.recorder.identity is None and not (env.out / "s2" / "bag").exists()
    assert alive(health(sess1).recorder.identity)  # the first capture is untouched
    assert cli("capture", "stop", sess1).exit_code == 0


def test_the_same_session_id_cannot_be_started_twice(env):
    sess = start(env, "s1"); ready(sess)
    r = cli("capture", "start", env.profile, "--out", env.out, "--session-id", "s1")
    assert r.exit_code == 2 and "already exists" in r.output
    assert alive(health(sess).recorder.identity)
    cli("capture", "stop", sess)


def test_simultaneous_starts_race_has_exactly_one_winner(env):
    procs = [subprocess.Popen([sys.executable, "-m", "physicore_collector", "capture", "start", str(env.profile), "--out", str(env.out),
                               "--session-id", f"r{i}", "--wait", "20"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
             for i in range(4)]
    codes = [p.wait(timeout=60) for p in procs]
    for i in range(4):
        env.started.append(env.out / f"r{i}")
    states = [health(env.out / f"r{i}").state for i in range(4)]
    assert states.count(CaptureState.RUNNING) == 1 and states.count(CaptureState.FAILED_TO_START) == 3, (states, codes)
    winner = next(env.out / f"r{i}" for i, s in enumerate(states) if s is CaptureState.RUNNING)
    assert cli("capture", "stop", winner).exit_code == 0


# ------------------------------------------------------------------ crashes and recovery


def test_recorder_crash_under_a_detached_supervisor_is_recorded(env, monkeypatch):
    monkeypatch.setenv("FAKE_ROS2_MODE", "crash")
    sess = start(env, "s1")  # the recorder is up when start looks; it dies 0.3 s later on its own
    wait_for(lambda: state_of(sess) not in (None, CaptureState.RUNNING, CaptureState.STARTING), what="crash recorded")
    h = health(sess)
    assert h.state is CaptureState.EXITED_UNEXPECTEDLY and h.recorder.exit_code == 5
    wait_for(lambda: supervisor_state(sess) == "dead", what="supervisor exit")  # it exited, releasing its lock
    assert assess(health(sess), sess).capture == "interrupted" and "ended on its own" in " ".join(h.errors)
    assert cli("capture", "status", sess).exit_code == 1


def test_supervisor_sigkilled_while_recorder_keeps_recording_is_detected_as_orphaned(env):
    sess = start(env, "s1"); ready(sess)
    h = health(sess)
    os.kill(h.supervisor.identity.pid, signal.SIGKILL)
    wait_for(lambda: supervisor_gone(sess, h), what="supervisor death")
    assert alive(h.recorder.identity)  # the recorder survived its supervisor
    a = assess(health(sess), sess)
    assert (a.supervisor, a.recorder, a.capture) == ("dead", "orphaned", "interrupted")
    st = cli("capture", "status", sess)
    assert st.exit_code == 1 and "ORPHANED RECORDER" in st.output and "--stop-orphan" in st.output
    sp = cli("capture", "stop", sess)
    assert sp.exit_code == 1 and "recover --stop-orphan" in sp.output and alive(h.recorder.identity)  # stop never signals
    # recover without the flag changes nothing and leaves the recorder running
    before = (sess / "capture_health.json").read_bytes()
    r = cli("capture", "recover", sess)
    assert r.exit_code == 1 and "ORPHAN LEFT RUNNING" in r.output and "Nothing was changed" in r.output
    assert (sess / "capture_health.json").read_bytes() == before and alive(h.recorder.identity)
    # with the flag: graceful stop (it finalizes the bag), then the record is settled
    r = cli("capture", "recover", sess, "--stop-orphan")
    assert r.exit_code == 0 and "RECONCILED" in r.output and "stopped gracefully" in r.output
    h2 = health(sess)
    assert not alive(h.recorder.identity)
    assert h2.state is CaptureState.INTERRUPTED and h2.reconciliation.orphan_recorder_action == "stopped_gracefully"
    assert h2.timing.end_unix_ns is None  # the end time is unknown; it was NOT invented
    assert h2.bag_finalization.appears_finalized is None  # consistent bag, but the capture was interrupted: not affirmed
    ctx = analyze_session(sess / "bag", env.profile, health_path=sess / "capture_health.json")
    assert ctx.report.status is SessionStatus.PARTIAL
    assert "interrupted" in next(c for c in ctx.report.checks if c.check_id == "capture:lifecycle").message


def test_power_loss_analogue_everything_sigkilled_then_recover_is_idempotent(env):
    sess = start(env, "s1"); ready(sess)
    h = health(sess)
    for ident in (h.supervisor.identity, h.recorder.identity):
        os.kill(ident.pid, signal.SIGKILL)
    wait_for(lambda: supervisor_gone(sess, h) and not alive(h.recorder.identity), what="both dead")
    assert health(sess).state is CaptureState.RUNNING  # the stale record: nobody could update it
    seg_hashes = {p.name: sha(p) for p in (sess / "bag").iterdir()}
    r = cli("capture", "recover", sess)
    assert r.exit_code == 0 and "RECONCILED (running -> interrupted)" in r.output
    h2 = health(sess)
    assert h2.state is CaptureState.INTERRUPTED and h2.reconciliation.supervisor_identity_result == "gone"
    assert h2.reconciliation.evidence_modified is False and h2.reconciliation.rebooted_since_capture_start is False
    assert h2.output.metadata_present is False and h2.bag_finalization.appears_finalized is False
    assert {p.name: sha(p) for p in (sess / "bag").iterdir()} == seg_hashes  # the recording was only READ
    first = (sess / "capture_health.json").read_bytes()
    again = cli("capture", "recover", sess)
    assert again.exit_code == 0 and "ALREADY FINAL" in again.output
    assert (sess / "capture_health.json").read_bytes() == first  # idempotent: not one byte rewritten
    ctx = analyze_session(sess / "bag", env.profile, health_path=sess / "capture_health.json")
    assert ctx.report.status is SessionStatus.PARTIAL and ctx.bag.info.metadata_present is False


def test_reboot_is_detected_from_boot_ids_and_pids_from_another_boot_are_never_signalled(env):
    sess = start(env, "s1"); ready(sess)
    h = health(sess)
    for ident in (h.supervisor.identity, h.recorder.identity):
        os.kill(ident.pid, signal.SIGKILL)
    wait_for(lambda: supervisor_gone(sess, h), what="death")
    # a bystander process that will inherit "the recorded pid" in the simulated new boot
    bystander = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        d = json.loads((sess / "capture_health.json").read_text())
        other_boot = "00000000-0000-0000-0000-00000000dead"
        d["supervisor"]["identity"]["boot_id"] = other_boot
        d["recorder"]["identity"] = {"pid": bystander.pid, "starttime_ticks": pi.read_starttime_ticks(bystander.pid), "boot_id": other_boot}
        (sess / "capture_health.json").write_text(json.dumps(d))
        r = cli("capture", "recover", sess, "--stop-orphan")
        assert r.exit_code == 0, r.output
        h2 = health(sess)
        assert h2.reconciliation.rebooted_since_capture_start is True and "rebooted" in h2.reconciliation.reason
        assert h2.reconciliation.recorder_identity_result == "different"
        assert bystander.poll() is None  # the unrelated process holding that pid was NOT signalled
    finally:
        bystander.kill(); bystander.wait()


def test_a_reused_pid_with_the_same_boot_is_never_signalled_either(env):
    sess = start(env, "s1"); ready(sess)
    h = health(sess)
    for ident in (h.supervisor.identity, h.recorder.identity):
        os.kill(ident.pid, signal.SIGKILL)
    wait_for(lambda: supervisor_gone(sess, h) and not alive(h.recorder.identity), what="death")
    bystander = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        d = json.loads((sess / "capture_health.json").read_text())
        d["recorder"]["identity"] = {"pid": bystander.pid, "starttime_ticks": pi.read_starttime_ticks(bystander.pid) + 7, "boot_id": pi.read_boot_id()}
        (sess / "capture_health.json").write_text(json.dumps(d))
        assert cli("capture", "recover", sess, "--stop-orphan").exit_code == 0
        assert bystander.poll() is None and health(sess).reconciliation.recorder_identity_result == "different"
    finally:
        bystander.kill(); bystander.wait()


def test_recover_never_disturbs_a_live_supervisor(env):
    sess = start(env, "s1"); ready(sess)
    r = cli("capture", "recover", sess, "--stop-orphan")
    assert r.exit_code == 0 and "SUPERVISOR ALIVE" in r.output
    h = health(sess)
    assert h.state is CaptureState.RUNNING and alive(h.recorder.identity) and alive(h.supervisor.identity)
    cli("capture", "stop", sess)


def test_prepared_session_whose_supervisor_never_arrives(env):
    from physicore_collector.profile import load_profile
    from physicore_collector.recorder import Rosbag2CliAdapter
    from physicore_collector.supervisor import CaptureSupervisor
    prepared = CaptureSupervisor(Rosbag2CliAdapter()).prepare(load_profile(env.profile), env.out, session_id="s1", launch_mode="detached")
    young = cli("capture", "recover", prepared.session_dir)
    assert "STARTING" in young.output and "may still be taking over" in young.output and young.exit_code == 0
    assert health(prepared.session_dir).state is CaptureState.STARTING  # a young session is left alone
    d = json.loads((prepared.session_dir / "capture_health.json").read_text())
    d["timing"]["start_unix_ns"] -= int(120e9)  # prepared two minutes ago, nobody ever came
    (prepared.session_dir / "capture_health.json").write_text(json.dumps(d))
    old = cli("capture", "recover", prepared.session_dir)
    assert old.exit_code == 0 and "never took over" in old.output
    assert health(prepared.session_dir).state is CaptureState.INTERRUPTED


def test_recover_all_and_list(env):
    a = start(env, "a"); ready(a)
    assert cli("capture", "stop", a).exit_code == 0
    b = start(env, "b"); ready(b)
    hb = health(b)
    for ident in (hb.supervisor.identity, hb.recorder.identity):
        os.kill(ident.pid, signal.SIGKILL)
    wait_for(lambda: supervisor_gone(b, hb) and not alive(hb.recorder.identity), what="death")
    (env.out / "not-a-session").mkdir()
    lst = cli("capture", "list", env.out)
    assert "a  state=stopped_clean  capture=finalized  supervisor=exited" in lst.output
    assert "b  state=running  capture=interrupted  supervisor=dead" in lst.output
    r = cli("capture", "recover", env.out, "--all")
    assert r.exit_code == 0 and "b: RECONCILED" in r.output and "a: ALREADY FINAL" in r.output
    assert health(b).state is CaptureState.INTERRUPTED and health(a).state is CaptureState.STOPPED_CLEAN
    assert json.loads(cli("capture", "list", env.out, "--json").output)[1]["capture"] == "interrupted"


def test_recover_on_something_that_is_not_a_session(env):
    (env.tmp / "x").mkdir()
    r = cli("capture", "recover", env.tmp / "x")
    assert r.exit_code == 2 and "not a capture session" in r.output
    (env.tmp / "y").mkdir(); (env.tmp / "y" / "capture_health.json").write_text("{broken")
    r = cli("capture", "recover", env.tmp / "y")
    assert r.exit_code == 2 and "left untouched" in r.output
    assert (env.tmp / "y" / "capture_health.json").read_text() == "{broken"


# ------------------------------------------------------------------ assessment of degraded states


def test_stale_heartbeat_is_degraded_not_healthy(env):
    sess = start(env, "s1"); ready(sess)
    h = health(sess)
    now = time.time_ns()
    assert assess(h, sess, now_ns=now).capture == "healthy"
    late = now + int(60e9)  # 60 s later with no heartbeat (threshold = max(3 x 5 s, 15 s))
    a = assess(h, sess, now_ns=late)
    assert a.capture == "degraded" and a.stale_heartbeat and any("heartbeat is stale" in r for r in a.reasons)
    cli("capture", "stop", sess)


def test_frozen_supervisor_is_alive_but_its_heartbeat_goes_stale(env):
    """A hung (SIGSTOPped) supervisor still holds its lock: 'alive' but not updating => degraded."""
    sess = start(env, "s1"); ready(sess)
    h = health(sess)
    os.kill(h.supervisor.identity.pid, signal.SIGSTOP)
    try:
        a = assess(health(sess), sess, now_ns=time.time_ns() + int(60e9))
        assert a.supervisor == "alive" and a.capture == "degraded" and a.stale_heartbeat
    finally:
        os.kill(h.supervisor.identity.pid, signal.SIGCONT)
    cli("capture", "stop", sess)


# ------------------------------------------------------------------ the recorder must not depend on its supervisor's pipe


def test_a_recorder_that_logs_after_its_supervisor_died_survives_if_sigpipe_is_not_restored(tmp_path):
    """Regression for a real hazard: with Popen's default restore_signals=True the child gets SIGPIPE
    back at its default action, so the first log line written after the supervisor (the pipe's only
    reader) is killed would kill the recorder instantly and ungracefully. bash stands in for a C++
    recorder here because, unlike Python, it does not turn SIGPIPE into an exception."""
    results = {}
    for restore in (True, False):
        flag = tmp_path / f"alive-{restore}"
        sleeper = tmp_path / f"fake-{restore}"
        sleeper.write_text(f'#!/bin/bash\nsleep 1.5\necho "late log line"\nsleep 0.5\ntouch {flag}\nsleep 30\n')
        sleeper.chmod(0o755)
        code = (
            "import sys, time, subprocess, signal, os\n"
            f"p = subprocess.Popen([{str(sleeper)!r}], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,\n"
            f"                     restore_signals={restore}, start_new_session=True)\n"
            "print(p.pid, flush=True); time.sleep(60)\n")
        sup = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        pid = int(sup.stdout.readline())
        sup.kill(); sup.wait()  # the pipe's only reader dies
        time.sleep(3.0)
        results[restore] = flag.exists()
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    assert results == {True: False, False: True}  # default behaviour kills the recorder; ours does not


def _proc_status_masks(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if k in ("SigIgn", "SigBlk", "SigCgt"):
            out[k] = int(v.strip(), 16)
    return out


@pytest.mark.parametrize("hostile", ["ignored", "blocked", "both"])
def test_the_recorder_gets_a_deterministic_signal_state_whatever_the_supervisor_inherited(tmp_path, hostile):
    """SIGINT (graceful stop) must work and SIGPIPE must stay ignored, even if the supervisor's own
    environment ignores/blocks SIGINT (backgrounded shell job, nohup, test runner)."""
    from physicore_collector.recorder import Rosbag2CliAdapter, RecorderSpec
    report = tmp_path / "sigstate"
    fake = tmp_path / "ros2"
    # Sampled with the `read` builtin: NO fork. (`cp /proc/$$/status` forks, and bash itself blocks
    # SIGINT/SIGTERM/SIGCHLD around its own fork, so that snapshot could catch bash's temporary mask
    # instead of the state the supervisor handed over: a flaky measurement, not a flaky recorder.)
    fake.write_text('#!/bin/bash\n{ while IFS= read -r l; do printf "%s\\n" "$l"; done < /proc/$$/status; } > ' + f'{report}.tmp\n'
                    + f'mv {report}.tmp {report}\nsleep 30\n')
    fake.chmod(0o755)
    old_handler = signal.getsignal(signal.SIGINT)
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    adapter = Rosbag2CliAdapter(executable=str(fake))
    try:
        if hostile in ("ignored", "both"):
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        if hostile in ("blocked", "both"):
            signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT, signal.SIGTERM])
        adapter.start(RecorderSpec(bag_dir=tmp_path / "bag", topics=("/a",)))
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        signal.signal(signal.SIGINT, old_handler)
    try:
        wait_for(lambda: report.exists() and "SigIgn" in report.read_text(), what="recorder status")
        m = _proc_status_masks(report.read_text())
        bit = lambda sig: 1 << (int(sig) - 1)
        assert not m["SigIgn"] & bit(signal.SIGINT), "SIGINT is ignored in the recorder: graceful stop would do nothing"
        assert not m["SigIgn"] & bit(signal.SIGTERM) and not m["SigBlk"] & (bit(signal.SIGINT) | bit(signal.SIGTERM))
        assert m["SigIgn"] & bit(signal.SIGPIPE), "SIGPIPE must stay ignored so a dead supervisor cannot kill the recorder"
    finally:
        adapter.kill()


def test_stop_does_not_return_until_the_supervisor_has_released_its_locks(tmp_path):
    """Deterministic version of the 'stop then start' guarantee: a stand-in supervisor writes its FINAL
    record, then keeps holding the session lock for 1.5 s. `capture stop` must wait for the lock."""
    from physicore_collector.profile import load_profile
    from physicore_collector.supervisor import CaptureSupervisor
    from tests.recorder_doubles import FakeAdapter, FakeClock
    prof = tmp_path / "p.yaml"; prof.write_text(PROFILE_11)
    out = tmp_path / "caps"; out.mkdir()
    adapter = FakeAdapter("clean"); clock = FakeClock(); adapter.clock = clock
    r = CaptureSupervisor(adapter, clock=clock).run(load_profile(prof), out, duration_seconds=0.3, session_id="s1")
    final = json.loads(r.health_path.read_text())
    running = json.loads(r.health_path.read_text())
    running["state"] = "running"
    running["bag_finalization"] = {"appears_finalized": None, "basis": "not evaluated"}
    sess = out / "s1"
    (sess / "stop_request.json").unlink(missing_ok=True)
    (sess / "capture_health.json").write_text(json.dumps(running))
    code = (
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "from physicore_collector.sessionlock import FileLock\n"
        "sess = Path(sys.argv[1]); final = sys.argv[2]\n"
        "lock = FileLock(sess / 'supervisor.lock'); lock.acquire()\n"
        "print('holding', flush=True)\n"
        "while not (sess / 'stop_request.json').exists(): time.sleep(0.02)\n"
        "tmp = sess / '.final.tmp'; tmp.write_text(final); os.replace(tmp, sess / 'capture_health.json')\n"
        "time.sleep(1.5)\n"      # the supervisor is finished (record written) but has not exited yet
        "lock.release()\n")
    holder = subprocess.Popen([sys.executable, "-c", code, str(sess), json.dumps(final)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "holding"
        t0 = time.time()
        res = cli("capture", "stop", sess, "--wait", "20")
        elapsed = time.time() - t0
        assert res.exit_code == 0, res.output
        assert elapsed >= 1.3, f"stop returned after {elapsed:.2f}s while the supervisor still held its lock"
        assert supervisor_state(sess) != "alive"
    finally:
        holder.kill(); holder.wait()
