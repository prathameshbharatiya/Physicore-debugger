# ruff: noqa: F811  (fixtures such as `world` are imported from another test module: the standard pytest pattern)
"""Day 5, real processes. What is REAL here: subprocesses, SIGKILL/SIGTERM, flock, /proc identity, process groups, the
detached supervisor, the `service-run` process, wall-clock timing, and (when the environment allows it) a real tmpfs
that really runs out of space. What is NOT real: the recorder is a stand-in script (tests/recorder_doubles.py), not
rosbag2/ROS 2; no systemd unit is installed or started; there is no robot and no real sensor load.

Timing assertions are deliberately loose upper bounds, not benchmarks. Measurements are printed (`pytest -s`) and
written to $PHYSICORE_STRESS_REPORT (a JSON file) when that variable is set."""

import hashlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from physicore_collector import incident as inc
from physicore_collector.capture_health import load_health
from physicore_collector.errors import IncidentError
from tests.bagtools import write_bag
from tests.test_incident_cli import EXE, _run_service, _running_session, _service_setup, cli, wait_for, world  # noqa: F401

NOW_ENV = {k: v for k, v in os.environ.items()}


def digest_tree(root: Path) -> dict:
    root = Path(root)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    return {str(p.relative_to(root.parent)): (p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files}


def evidence(session: Path) -> dict:
    return {k: v for k, v in digest_tree(session).items()
            if "/bag/" in k or k.endswith(("capture_health.json", "profile.yaml"))}


def staging_dirs(parent: Path) -> list[Path]:
    return sorted(parent.glob(".*incident-staging-*"))


def report(name: str, **metrics) -> None:
    line = {"test": name, **metrics}
    print("MEASURED", json.dumps(line))
    path = os.environ.get("PHYSICORE_STRESS_REPORT")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(line) + "\n")


def kill_group_quietly(pid: int | None) -> None:
    if pid:
        for fn in (lambda: os.killpg(pid, signal.SIGKILL), lambda: os.kill(pid, signal.SIGKILL)):
            try:
                fn()
            except (ProcessLookupError, PermissionError):
                pass


def pids_of(session: Path) -> tuple[int | None, int | None]:
    h = load_health(session / "capture_health.json").health
    return (h.supervisor.identity.pid if h.supervisor.identity else None, h.recorder.identity.pid if h.recorder.identity else None)


def alive(pid: int) -> bool:
    from physicore_collector.procident import process_state
    st = process_state(pid)
    return st is not None and st != "Z"


def slow_prepare(args: list[str], mib_per_sec: float) -> subprocess.Popen:
    return subprocess.Popen([EXE, "incident", "prepare", *args, "--max-mib-per-sec", str(mib_per_sec)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


@pytest.fixture
def big_bag(tmp_path):
    return write_bag(tmp_path / "src" / "bigbag", payload_pad=100_000)  # about 11.7 MiB


# ------------------------------------------------------------------ process interruption during preparation


def test_sigkill_during_copy_leaves_stale_staging_and_no_incident_then_retry_cleans_it(big_bag, synth_profile, tmp_path):
    out = tmp_path / "inc"
    before = digest_tree(big_bag)
    p = slow_prepare([str(big_bag), "--profile", str(synth_profile), "--out", str(out)], 6)
    try:
        wait_for(lambda: staging_dirs(tmp_path), timeout=30, what="staging directory (the copy phase)")
        wait_for(lambda: any(f.is_file() and f.stat().st_size > 0 for s in staging_dirs(tmp_path) for f in s.rglob("*")),
                 timeout=30, what="bytes copied into staging")
        p.send_signal(signal.SIGKILL)  # no cleanup code can run: this is the "killed by OOM / power button" case
        p.wait(timeout=20)
    finally:
        if p.poll() is None:
            p.kill()
    assert staging_dirs(tmp_path), "a hard-killed run must leave its staging behind (that is what recovery has to handle)"
    assert not out.exists() and digest_tree(big_bag) == before  # nothing published, source untouched
    r = cli("incident", "prepare", big_bag, "--profile", synth_profile, "--out", out)
    assert r.exit_code == 0, r.output
    assert not staging_dirs(tmp_path) and inc.verify_incident_detailed(out).state == "verified_now"
    rec, _ = inc.load_incident(out)
    assert any("stale staging directory" in n for n in rec.notes)
    assert digest_tree(big_bag) == before


def test_sigterm_during_copy_cancels_cleanly_removes_staging_and_publishes_nothing(big_bag, synth_profile, tmp_path):
    out = tmp_path / "inc"
    before = digest_tree(big_bag)
    p = slow_prepare([str(big_bag), "--profile", str(synth_profile), "--out", str(out)], 6)
    try:
        wait_for(lambda: staging_dirs(tmp_path), timeout=30, what="staging directory")
        p.send_signal(signal.SIGTERM)  # what `systemctl stop` or an operator sends
        t0 = time.time()
        _, err = p.communicate(timeout=30)
        latency = time.time() - t0
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 2 and "interrupted" in err.lower()
    assert not staging_dirs(tmp_path) and not out.exists() and digest_tree(big_bag) == before
    assert latency < 10, f"cancel took {latency:.1f}s"
    assert cli("incident", "prepare", big_bag, "--profile", synth_profile, "--out", out).exit_code == 0  # and a retry works


def test_two_real_preparers_racing_on_one_target_cannot_corrupt_or_overwrite_it(big_bag, synth_profile, tmp_path):
    out = tmp_path / "inc"
    first = slow_prepare([str(big_bag), "--profile", str(synth_profile), "--out", str(out)], 8)
    try:
        wait_for(lambda: staging_dirs(tmp_path), timeout=30, what="the first preparer to be mid-copy")
        second = subprocess.run([EXE, "incident", "prepare", str(big_bag), "--profile", str(synth_profile), "--out", str(out), "--json"],
                                capture_output=True, text=True, timeout=60)
        assert second.returncode == 2 and json.loads(second.stdout)["error"]["code"] == "preparation_in_progress"
        assert not out.exists()  # the loser published nothing and the winner has not finished
        first.wait(timeout=120)
    finally:
        if first.poll() is None:
            first.kill()
    assert first.returncode == 0 and inc.verify_incident_detailed(out).state == "verified_now"
    again = cli("incident", "prepare", big_bag, "--profile", synth_profile, "--out", out)
    assert again.exit_code == 0 and "ALREADY PREPARED" in again.output  # a late third caller is idempotent, not destructive


# ------------------------------------------------------------------ a recorder that outlives its supervisor


def _start_detached(world, sid: str, *flags: str, pad: int = 0, monkeypatch=None):
    r = cli("capture", "start", world.profile, "--out", world.out, "--session-id", sid, *flags)
    assert r.exit_code == 0, r.output
    sess = world.out / sid
    wait_for((sess / "bag").is_dir, what="recorder bag")
    return sess


def test_recorder_alive_after_supervisor_death_is_never_packaged_and_only_stopped_on_explicit_request(world):
    sess = _start_detached(world, "o1", "--no-auto-prepare")
    sup, rec = pids_of(sess)
    try:
        os.kill(sup, signal.SIGKILL)  # the supervisor dies; its recorder lives on
        wait_for(lambda: not alive(sup), what="supervisor to die")
        assert alive(rec) and load_health(sess / "capture_health.json").health.state.value == "running"
        before = evidence(sess)

        pend = json.loads(cli("incident", "pending", "list", world.out, "--json").stdout)
        assert pend[0]["kind"] == "needs_reconcile" and pend[0]["blocked_by"] == "recorder_may_be_writing"
        assert "--stop-orphan" in pend[0]["action"]

        r = cli("incident", "prepare", sess)
        assert r.exit_code == 3 and "may still be writing" in r.output  # deferred, not failed, not prepared
        r = cli("incident", "pending", "run", world.out)
        assert r.exit_code == 3 and alive(rec), r.output  # still never signalled without the explicit flag
        assert evidence(sess) == before and not (sess / "incident").exists()
        assert load_health(sess / "capture_health.json").health.state.value == "running"  # not even reconciled while live

        r = cli("incident", "pending", "run", world.out, "--stop-orphan")  # explicit, and through identity-verified recovery
        assert r.exit_code == 0, r.output
        wait_for(lambda: not alive(rec), what="orphan recorder to stop")
        rec_inc, _ = inc.load_incident(sess)
        assert rec_inc.capture_health.state == "interrupted" and rec_inc.outcome == "degraded"
        assert load_health(sess / "capture_health.json").health.state.value == "interrupted"
        assert inc.verify_incident_detailed(sess).state == "verified_now"
    finally:
        kill_group_quietly(rec)
        kill_group_quietly(sup)


def test_machine_died_mid_capture_stale_session_is_recovered_prepared_and_stays_degraded(world):
    """Power-loss shape: supervisor AND recorder both gone, record still says `running`. (A physical reboot is not tested.)"""
    sess = _start_detached(world, "p1", "--no-auto-prepare")
    sup, rec = pids_of(sess)
    try:
        for pid in (sup, rec):
            kill_group_quietly(pid)
        wait_for(lambda: not alive(sup) and not alive(rec), what="both processes to die")
        assert load_health(sess / "capture_health.json").health.state.value == "running"
        (item,) = json.loads(cli("incident", "pending", "list", world.out, "--json").stdout)
        assert item["kind"] == "needs_reconcile" and item["blocked_by"] is None and item["retry_eligible"] is True
        r = cli("incident", "pending", "run", world.out)
        assert r.exit_code == 0 and "reconciled" in r.output, r.output
        rec_inc, _ = inc.load_incident(sess)
        assert rec_inc.capture_health.state == "interrupted" and rec_inc.outcome == "degraded"
        assert rec_inc.session_status.value in ("partial", "failed", "complete")  # whatever validation honestly found ...
        assert any("INTERRUPTED" in x for x in rec_inc.outcome_reasons)  # ... it is never reported as a clean capture
        assert cli("incident", "pending", "run", world.out).output.strip() == "Nothing pending."  # idempotent
    finally:
        kill_group_quietly(rec)
        kill_group_quietly(sup)


# ------------------------------------------------------------------ real disk exhaustion (tmpfs), only if supported


@pytest.fixture
def tiny_tmpfs(tmp_path):
    mnt = tmp_path / "tiny"
    mnt.mkdir()
    r = subprocess.run(["mount", "-t", "tmpfs", "-o", "size=4m", "tmpfs", str(mnt)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"cannot mount a tmpfs here (needs privileges): {r.stderr.strip()}")
    try:
        yield mnt
    finally:
        subprocess.run(["umount", "-l", str(mnt)], capture_output=True)


def test_real_disk_exhaustion_during_copy_publishes_nothing_frees_the_space_and_retry_succeeds(tiny_tmpfs, synth_profile, tmp_path):
    src = write_bag(tmp_path / "src" / "wide", payload_pad=50_000)  # about 5.9 MiB: cannot fit in a 4 MiB tmpfs
    before = digest_tree(src)
    free0 = os.statvfs(tiny_tmpfs).f_bavail * os.statvfs(tiny_tmpfs).f_frsize
    # 1) the honest pre-flight refuses before copying anything
    with pytest.raises(IncidentError) as e:
        inc.prepare_incident(src, synth_profile, tiny_tmpfs / "inc")
    assert e.value.code == "insufficient_space" and "Nothing was copied" in e.value.hint and not (tiny_tmpfs / "inc").exists()
    # 2) the pre-flight is bypassed (it can be wrong: another writer may fill the disk meanwhile): the copy itself really hits ENOSPC
    with pytest.raises(Exception) as e:  # the export layer reports the real ENOSPC; failure_info classifies it
        inc.prepare_incident(src, synth_profile, tiny_tmpfs / "inc", free_space=lambda p: 10**12)
    assert isinstance(e.value.__cause__, OSError) and e.value.__cause__.errno == 28  # a REAL ENOSPC, not an injected one
    assert inc.failure_info(e.value)[0] == "insufficient_space", e.value
    assert not (tiny_tmpfs / "inc").exists() and not staging_dirs(tiny_tmpfs)
    free1 = os.statvfs(tiny_tmpfs).f_bavail * os.statvfs(tiny_tmpfs).f_frsize
    assert free1 >= free0 - 64 * 1024, "the partial staging copy must be removed so the space is returned"
    assert digest_tree(src) == before
    # 3) after space is added the same command succeeds
    subprocess.run(["mount", "-o", "remount,size=64m", str(tiny_tmpfs)], check=True, capture_output=True)
    res = inc.prepare_incident(src, synth_profile, tiny_tmpfs / "inc")
    assert res.state == "prepared" and inc.verify_incident_detailed(tiny_tmpfs / "inc").state == "verified_now"


# ------------------------------------------------------------------ service shutdown stays bounded


def test_service_start_and_stop_never_wait_for_pending_copies(world, monkeypatch):
    """A large finished-but-unprepared session sits in the output directory. A service start must not touch it beyond a
    stat-only record, and a stop must not wait for any copy."""
    monkeypatch.setenv("FAKE_ROS2_PAD", "400000")  # about 47 MiB per session
    r = cli("capture", "run", world.profile, "--out", world.out, "--duration", 1, "--session-id", "old", "--no-auto-prepare")
    assert r.exit_code == 0, r.output
    old = world.out / "old"
    size = sum(f.stat().st_size for f in (old / "bag").iterdir())
    assert size > 30 * 1024 * 1024
    monkeypatch.delenv("FAKE_ROS2_PAD")
    before = evidence(old)
    t_start = time.time()
    p = _run_service(world, _service_setup(world))
    new = None
    try:
        new = wait_for(lambda: _first_new_running(world, "old"), timeout=60, what="service recording")
        start_latency = time.time() - t_start
        t_stop = time.time()
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=60)
        stop_latency = time.time() - t_stop
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, (out, err)
    assert "pending incident preparation recorded for old" in out and "DEFERRED (service mode)" in out
    assert evidence(old) == before and not (old / "incident").exists() and not staging_dirs(old)
    assert json.loads((old / "incident_attempt.json").read_text())["code"] == "deferred_stale_session"
    assert json.loads((new / "incident_attempt.json").read_text())["code"] == "deferred_service_mode"
    assert start_latency < 30 and stop_latency < 30
    report("service_start_stop_with_47MiB_pending", pending_bytes=size, start_to_recording_s=round(start_latency, 2),
           sigterm_to_exit_s=round(stop_latency, 2))
    assert cli("incident", "pending", "run", world.out).exit_code == 0  # both are then drained when idle
    assert {i["state"] for i in json.loads(cli("incident", "list", world.out, "--verify", "--json").stdout)} == {"verified_now"}


def _first_new_running(world, existing: str):
    for d in sorted(world.out.iterdir()):
        if d.name != existing and (d / "capture_health.json").is_file() and (d / "bag").is_dir():
            try:
                if load_health(d / "capture_health.json").health.state.value == "running":
                    return d
            except Exception:
                pass
    return None


# ------------------------------------------------------------------ a capture starting while preparation runs (measured)


@pytest.mark.parametrize("when", ["during_copy", "immediately"])
def test_next_capture_is_not_blocked_and_preparation_yields_to_it(world, monkeypatch, when):
    """The stress case: capture A stops (auto-preparation of a ~47 MiB bag begins, throttled so it is still running),
    then capture B starts at once. B must start promptly; A's preparation must stop cleanly and be queued, without
    losing or altering A's recording; afterwards `incident pending run` prepares both."""
    monkeypatch.setenv("FAKE_ROS2_PAD", "400000")
    monkeypatch.setenv(inc.THROTTLE_ENV, "20")  # explicit throttle: keeps preparation of A running long enough to overlap B
    a = _start_detached(world, "A")
    assert cli("capture", "stop", a).exit_code == 0
    t_a_stopped = time.time()
    a_size = sum(f.stat().st_size for f in (a / "bag").iterdir())
    a_evidence = evidence(a)
    if when == "during_copy":
        wait_for(lambda: staging_dirs(a), timeout=30, what="A's preparation to be copying")
        copying_seen = True
    else:
        copying_seen = bool(staging_dirs(a))
    monkeypatch.delenv("FAKE_ROS2_PAD")
    t0 = time.time()
    r = cli("capture", "start", world.profile, "--out", world.out, "--session-id", "B", "--no-auto-prepare")
    b_start_latency = time.time() - t0
    assert r.exit_code == 0, r.output  # capturing was NOT blocked by the preparation of A
    b = world.out / "B"
    sup_b, rec_b = pids_of(b)
    try:
        t_b_up = time.time()
        att = wait_for(lambda: _deferred_attempt(a), timeout=40, what="A's preparation to yield")
        yield_latency = time.time() - t_b_up
        assert att["code"] == "deferred_capture_active" and att["state"] == "deferred"
        if when == "during_copy":
            assert "started while this was running" in att["message"], att["message"]  # it really was mid-copy
        assert not (a / "incident").exists() and not staging_dirs(a)  # nothing partial is left or published
        assert evidence(a) == a_evidence  # A's recording and capture record are byte-identical
        assert load_health(b / "capture_health.json").health.state.value == "running" and alive(rec_b)  # B kept recording
        assert yield_latency < 15
        assert cli("capture", "stop", b).exit_code == 0
    finally:
        kill_group_quietly(rec_b)
        kill_group_quietly(sup_b)
    monkeypatch.delenv(inc.THROTTLE_ENV)
    t1 = time.time()
    drain = cli("incident", "pending", "run", world.out)
    drain_s = time.time() - t1
    assert drain.exit_code == 0, drain.output
    rows = {i["name"]: i for i in json.loads(cli("incident", "list", world.out, "--verify", "--json").stdout)}
    assert rows["A"]["state"] == "verified_now" and rows["B"]["state"] == "verified_now"
    final = json.loads((a / "incident_attempt.json").read_text())
    assert final["state"] == "prepared" and final["attempt_count"] >= 2 and final["first_attempted_at_utc"]
    report(f"capture_B_starts_{when}", bag_bytes=a_size, preparation_was_copying_when_B_started=copying_seen,
           b_capture_start_s=round(b_start_latency, 2), a_yield_after_b_up_s=round(yield_latency, 2),
           a_stop_to_b_start_s=round(t0 - t_a_stopped, 2), drain_both_s=round(drain_s, 2), a_attempts=final["attempt_count"])


def _deferred_attempt(session: Path):
    f = session / "incident_attempt.json"
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return d if d.get("state") == "deferred" else None
