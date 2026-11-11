"""Day 5: preparation must never knowingly compete with a recording, deferred work must be durable and visible,
interrupted sessions must be recovered without being relabelled. Library level, deterministic: synthetic bags,
stand-in recorder, injected clocks. Real-process, kill, tmpfs and stress tests are in test_day5_realprocess.py."""

import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from physicore_collector import checksums
from physicore_collector import export as export_mod
from physicore_collector import incident as inc
from physicore_collector.capture_health import CaptureHealth, load_health
from physicore_collector.errors import IncidentDeferredError, InputError
from physicore_collector.procident import identity_of
from physicore_collector.sessionlock import FileLock
from physicore_collector.supervisor import write_health_atomic
from tests.bagtools import write_bag
from tests.test_incident import NOW, make_session, snap

ACTIVE = ".active-capture.lock"


def edit_sidecar(session: Path, fn) -> None:
    data = load_health(session / "capture_health.json").health.model_dump(mode="json")
    fn(data)
    write_health_atomic(session / "capture_health.json", CaptureHealth.model_validate(data))


def make_stale(session: Path) -> None:
    """What a killed supervisor leaves behind: state `running`, nobody attached, outcome never recorded."""
    def f(d):
        d["state"] = "running"
        d["bag_finalization"] = {"appears_finalized": None, "basis": "test"}
    edit_sidecar(session, f)


def evidence(session: Path) -> dict:
    """Digest of what must NEVER change: the recording, the capture-health record and the profile copy. (Hidden lock
    files and `incident_attempt.json` are PhysiCore's own bookkeeping and are deliberately excluded.)"""
    keep = [p for p in session.rglob("*") if p.is_file() and ("bag" in p.relative_to(session).parts
            or p.name in ("capture_health.json", "profile.yaml", "profile.json")) and "incident" not in p.relative_to(session).parts]
    return {str(p.relative_to(session)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in keep}


def attempt(session: Path) -> dict:
    return json.loads((session / "incident_attempt.json").read_text())


class Active:
    """Holds a session-less `.active-capture.lock`, exactly what a running capture holds in its output directory."""

    def __init__(self, directory: Path):
        self.lock = FileLock(Path(directory) / ACTIVE)

    def __enter__(self):
        self.lock.acquire()
        return self

    def __exit__(self, *a):
        self.lock.release()


@pytest.fixture
def live_orphan():
    """A real process with a real, verifiable identity that stands in for a recorder that outlived its supervisor."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
    threading.Thread(target=p.wait, daemon=True).start()  # reap it when it dies so it never lingers as a zombie
    try:
        yield p
    finally:
        p.kill()


def set_recorder(session: Path, pid: int) -> None:
    ident = identity_of(pid)

    def f(d):
        d["recorder"]["identity"] = {"pid": ident.pid, "starttime_ticks": ident.starttime_ticks, "boot_id": ident.boot_id}
        d["recorder"]["pid"] = ident.pid
    edit_sidecar(session, f)


# ------------------------------------------------------------------ the I/O hook and the governor


def test_io_hook_sees_every_chunk_is_restored_and_is_inert_when_unset(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"x" * 2500)
    seen = []
    with checksums.io_hook(seen.append):
        checksums.sha256_file(f, chunk_size=1000)
        checksums.copy_and_hash(f, tmp_path / "g.bin", chunk_size=1000)
    assert seen == [1000, 1000, 500] * 2
    checksums.sha256_file(f)  # restored: no hook, no calls
    assert len(seen) == 6
    with pytest.raises(RuntimeError):
        with checksums.io_hook(lambda n: (_ for _ in ()).throw(RuntimeError("stop"))):
            checksums.sha256_file(f)
    assert checksums._io_hook is None  # restored after an exception too


def test_governor_throttle_sleeps_exactly_enough_and_yields_when_a_capture_appears():
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        t["now"] += s
    probes = []
    gov = inc.IoGovernor([Path("/x")], max_bytes_per_sec=1000.0, interval=0.5, clock=lambda: t["now"], sleep=sleep,
                         probe=lambda dirs: probes.append(1) or [])
    for _ in range(10):
        gov(1000)  # 10 000 bytes at 1000 B/s must take ~10 s
    assert abs(t["now"] - 10.0) < 1e-6 and probes  # throttled, and it kept checking while it waited
    active = [inc.ActiveCapture(Path("/x"), "session s")]
    gov2 = inc.IoGovernor([Path("/x")], interval=0.5, clock=lambda: t["now"], sleep=sleep, probe=lambda d: active)
    gov2(10)  # first chunk, interval not elapsed: no probe yet
    t["now"] += 0.6
    with pytest.raises(IncidentDeferredError) as e:
        gov2(10)
    assert e.value.code == "deferred_capture_active" and "started while this was running" in e.value.message


def test_throttle_environment_variable_is_validated(monkeypatch):
    monkeypatch.setenv(inc.THROTTLE_ENV, "0")
    with pytest.raises(InputError, match="positive"):
        inc.env_throttle_mib()
    monkeypatch.setenv(inc.THROTTLE_ENV, "12.5")
    assert inc.env_throttle_mib() == 12.5
    monkeypatch.delenv(inc.THROTTLE_ENV)
    assert inc.env_throttle_mib() is None


def test_guard_dirs_are_deduplicated_and_never_inferred_from_disks(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    monkeypatch.setenv(inc.GUARD_ENV, f"{b}{os.pathsep}{tmp_path / 'missing'}")
    dirs = inc.collect_guard_dirs(a, a, extra=[a])
    assert [Path(d) for d in dirs] == [a, b]  # env dir included, missing dir dropped, duplicates collapsed
    with Active(b):  # a capture in a DIFFERENT directory counts: different paths are never assumed to be different disks
        (cap,) = inc.find_active_captures(dirs)
        assert cap.directory == b


# ------------------------------------------------------------------ deferral: never compete with a recording


def test_active_capture_defers_before_anything_is_written_then_retry_succeeds(tmp_path):
    sess = make_session(tmp_path)
    out = sess.parent
    before = evidence(sess)
    with Active(out):
        with pytest.raises(IncidentDeferredError) as e:
            inc.prepare_incident(sess, None, None, now=NOW)
    assert e.value.code == "deferred_capture_active" and "not a failure" in e.value.hint
    assert evidence(sess) == before  # source & sidecar untouched
    assert not (sess / "incident").exists() and not list(sess.glob(".*staging*")) and not (sess / ".incident.prepare.lock").exists()
    att = attempt(sess)
    assert att["state"] == "deferred" and att["code"] == "deferred_capture_active" and att["attempt_count"] == 1
    assert att["retry_command"].endswith(str(sess)) and att["attempt_schema_version"] == "1.1"
    (row,) = inc.list_incidents(out)
    assert row.state == "deferred" and "Retry:" in row.detail  # observable, and never shown as prepared
    res = inc.prepare_incident(sess, None, None, now=NOW)  # idle again: the same command succeeds
    assert res.state == "prepared"
    att2 = attempt(sess)
    assert att2["state"] == "prepared" and att2["attempt_count"] == 2 and att2["first_attempted_at_utc"] == att["attempted_at_utc"]


def _capture_starts_after_first_copy(monkeypatch, directory: Path):
    """Deterministic 'a capture starts mid-preparation': the first copied file flips the world to 'capture active'."""
    state = {"active": False, "copies": 0}
    real_copy, real_probe = export_mod.copy_and_hash, inc.find_active_captures

    def copy(src, dst, **k):
        state["copies"] += 1
        if state["copies"] == 2:
            state["active"] = True  # a capture takes `.active-capture.lock` while preparation is copying
        return real_copy(src, dst, **k)
    monkeypatch.setattr(export_mod, "copy_and_hash", copy)
    monkeypatch.setattr(inc, "find_active_captures",
                        lambda dirs: [inc.ActiveCapture(directory, "a new capture")] if state["active"] else real_probe(dirs))
    monkeypatch.setattr(inc, "CHECK_INTERVAL_SECONDS", 0.0)
    return state, real_copy, real_probe


def test_a_capture_that_starts_midway_makes_preparation_yield_cleanly(tmp_path, monkeypatch):
    sess = make_session(tmp_path)
    state, real_copy, real_probe = _capture_starts_after_first_copy(monkeypatch, sess.parent)
    before = evidence(sess)
    with pytest.raises(IncidentDeferredError) as e:
        inc.prepare_incident(sess, None, None, now=NOW)
    assert "started while this was running" in e.value.message and state["copies"] >= 2  # it really was mid-copy
    assert not (sess / "incident").exists() and not list(sess.glob(".*staging*"))
    assert evidence(sess) == before
    assert attempt(sess)["state"] == "deferred" and attempt(sess)["code"] == "deferred_capture_active"
    monkeypatch.setattr(export_mod, "copy_and_hash", real_copy)
    monkeypatch.setattr(inc, "find_active_captures", real_probe)
    assert inc.prepare_incident(sess, None, None, now=NOW).state == "prepared"
    assert inc.verify_incident_detailed(sess / "incident").state == "verified_now"


def test_a_capture_that_starts_during_the_scan_is_not_ignored_and_is_not_reported_as_corruption(tmp_path, monkeypatch):
    """The MCAP scan reads inside the mcap library. It must still be a checkpoint, and a yield raised from inside it
    must propagate as a deferral, never be swallowed into a 'corrupted recording' finding."""
    from tests.test_incident import _big_mcap
    import yaml
    prof = tmp_path / "p.yaml"
    prof.write_text(yaml.safe_dump({"profile_schema_version": "1.0", "profile_id": "big", "log_time_basis": "system_time",
                                    "required_metadata_keys": ["operator"], "topics": [{"name": "/big"}]}))
    big = tmp_path / "big.mcap"
    _big_mcap(big, 64)  # 64 MiB: the scan is the long, library-internal part
    scanned = {"bytes": 0}
    from physicore_collector import mcap_reader
    real_notify = mcap_reader.notify_io

    def counting(n):
        scanned["bytes"] += n
        real_notify(n)
    monkeypatch.setattr(mcap_reader, "notify_io", counting)
    monkeypatch.setattr(inc, "CHECK_INTERVAL_SECONDS", 0.0)
    real_probe = inc.find_active_captures
    monkeypatch.setattr(inc, "find_active_captures",
                        lambda dirs: [inc.ActiveCapture(tmp_path, "new")] if scanned["bytes"] > 8 * 1024 * 1024 else real_probe(dirs))
    copies = []
    monkeypatch.setattr(export_mod, "export_package", lambda *a, **k: copies.append(1))
    with pytest.raises(IncidentDeferredError):
        inc.prepare_incident(big, prof, tmp_path / "inc", now=NOW)
    assert scanned["bytes"] < 12 * 1024 * 1024, f"scanned {scanned['bytes']} B of 64 MiB after a capture appeared at 8 MiB"
    assert not (tmp_path / "inc").exists() and not copies and big.stat().st_size > 64 * 1024 * 1024


def test_a_recording_held_open_for_writing_is_never_packaged_even_without_any_sidecar(synth_profile, tmp_path, monkeypatch):
    """An unsupervised `ros2 bag record` has no capture-health record and no lock: only the open file gives it away."""
    bag = write_bag(tmp_path / "live" / "bagl")
    seg = next(bag.glob("*.mcap"))
    copies = []
    real_export = inc.export_package
    monkeypatch.setattr(inc, "export_package", lambda *a, **k: (copies.append(1), real_export(*a, **k))[1])
    p = subprocess.Popen([sys.executable, "-c", f"import time; f=open({str(seg)!r},'ab'); print('open', flush=True); time.sleep(60)"],
                         stdout=subprocess.PIPE, text=True)  # a second process: this one is excluded from its own scan
    try:
        assert p.stdout.readline().strip() == "open"
        (w,), _unreadable = inc.find_live_writers(bag)
        assert w.pid == p.pid and str(seg) in w.path
        before = evidence_of(bag)
        with pytest.raises(IncidentDeferredError) as e:
            inc.prepare_incident(bag, synth_profile, tmp_path / "inc", now=NOW)
        assert e.value.code == "deferred_recorder_alive" and str(p.pid) in e.value.message and "never stops it" in e.value.hint
        assert copies == [], "nothing may be copied from a recording that is still being written"
        assert not (tmp_path / "inc").exists() and evidence_of(bag) == before and p.poll() is None  # and we did not stop it
        with pytest.raises(IncidentDeferredError):  # the override is about COMPETITION, never about packaging a live file
            inc.prepare_incident(bag, synth_profile, tmp_path / "inc", now=NOW, respect_active_captures=False)
        assert copies == []
    finally:
        p.kill(); p.wait()
    assert inc.find_live_writers(bag)[0] == []
    assert inc.prepare_incident(bag, synth_profile, tmp_path / "inc", now=NOW).state == "prepared"


def test_a_writer_that_appears_while_the_bag_is_being_copied_discards_the_copy(synth_profile, tmp_path, monkeypatch):
    bag = write_bag(tmp_path / "late" / "bagw")
    real = inc.find_live_writers
    calls = {"n": 0}

    def appears_late(rec, **k):
        calls["n"] += 1
        return real(rec, **k) if calls["n"] == 1 else ([inc.LiveWriter(4242, "ros2 bag record", str(rec))], 0)
    monkeypatch.setattr(inc, "find_live_writers", appears_late)
    before = evidence_of(bag)
    with pytest.raises(IncidentDeferredError) as e:
        inc.prepare_incident(bag, synth_profile, tmp_path / "inc", now=NOW)
    assert e.value.code == "deferred_recorder_alive" and calls["n"] == 2  # the post-copy re-check is what caught it
    assert not (tmp_path / "inc").exists() and not staging_leftovers(tmp_path) and evidence_of(bag) == before


def staging_leftovers(parent: Path) -> list:
    return sorted(parent.glob(".*incident-staging-*"))


def evidence_of(bag: Path) -> dict:
    return {f.name: (f.stat().st_size, f.stat().st_mtime_ns, hashlib.sha256(f.read_bytes()).hexdigest()) for f in bag.iterdir()}


def test_read_only_openers_are_not_mistaken_for_writers(tmp_path):
    bag = write_bag(tmp_path / "ro" / "bagr")
    seg = next(bag.glob("*.mcap"))
    p = subprocess.Popen([sys.executable, "-c", f"import time; f=open({str(seg)!r},'rb'); print('open', flush=True); time.sleep(60)"],
                         stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "open"
        assert inc.find_live_writers(bag)[0] == []
    finally:
        p.kill(); p.wait()


def test_fdatasync_bounds_the_dirty_backlog_only_while_governed(tmp_path, monkeypatch):
    src = tmp_path / "s.bin"
    src.write_bytes(b"x" * (3 * 1024 * 1024))
    syncs = []
    real = os.fdatasync
    monkeypatch.setattr(os, "fdatasync", lambda fd: (syncs.append(1), real(fd))[1])
    monkeypatch.setattr(checksums, "SYNC_EVERY_BYTES", 1024 * 1024)
    checksums.copy_and_hash(src, tmp_path / "a.bin")
    assert syncs == []  # ungoverned: exactly the old behaviour
    with checksums.io_hook(lambda n: None):
        checksums.copy_and_hash(src, tmp_path / "b.bin")
    assert len(syncs) == 3


def test_allow_while_recording_is_an_explicit_override(tmp_path):
    sess = make_session(tmp_path)
    with Active(sess.parent):
        r = inc.prepare_incident(sess, None, None, now=NOW, respect_active_captures=False)
    assert r.state == "prepared"


def test_manual_preparation_elsewhere_is_deferred_by_a_guard_dir(fx, synth_profile, tmp_path):
    cap_dir = tmp_path / "caps"
    cap_dir.mkdir()
    with Active(cap_dir):
        with pytest.raises(IncidentDeferredError):
            inc.prepare_incident(fx / "valid.mcap", synth_profile, tmp_path / "inc", guard_dirs=[cap_dir])
        assert not (tmp_path / "inc").exists()
        inc.prepare_incident(fx / "valid.mcap", synth_profile, tmp_path / "inc", guard_dirs=[cap_dir], respect_active_captures=False)
    assert (tmp_path / "inc" / "incident.json").is_file()


def test_dry_run_is_also_deferred_because_it_reads_the_whole_recording(fx, synth_profile, tmp_path):
    with Active(tmp_path):
        with pytest.raises(IncidentDeferredError):
            inc.prepare_incident(fx / "valid.mcap", synth_profile, tmp_path / "inc", dry_run=True)


def test_auto_prepare_after_capture_reports_deferral_without_raising_or_touching_the_capture(tmp_path):
    sess = make_session(tmp_path)
    sidecar_before = (sess / "capture_health.json").read_bytes()
    with Active(sess.parent):
        out = inc.auto_prepare_after_capture(sess)
    assert out.state == "deferred" and out.code == "deferred_capture_active" and out.record is None
    assert (sess / "capture_health.json").read_bytes() == sidecar_before


# ------------------------------------------------------------------ a recorder that may still be writing


def test_orphaned_recorder_blocks_preparation_of_a_stale_session(tmp_path, live_orphan):
    """Day 4 bug fixed here: it prepared such a session while the recorder could still be appending to the bag."""
    sess = make_session(tmp_path)
    make_stale(sess)
    set_recorder(sess, live_orphan.pid)
    before = evidence(sess)
    with pytest.raises(IncidentDeferredError) as e:
        inc.prepare_incident(sess, None, None, now=NOW)
    assert e.value.code == "deferred_recorder_alive" and "capture recover" in e.value.hint and "--stop-orphan" in e.value.hint
    assert evidence(sess) == before
    assert live_orphan.poll() is None  # and it was NOT signalled
    live_orphan.kill()
    live_orphan.wait()
    r = inc.prepare_incident(sess, None, None, now=NOW)  # recorder gone: now it is safe, and honest about it
    assert r.record.capture_health.state == "interrupted" and r.record.outcome == "degraded"


def test_orphaned_recorder_of_an_already_ended_capture_also_blocks_and_is_never_signalled(tmp_path, live_orphan):
    sess = make_session(tmp_path)  # stopped_clean
    set_recorder(sess, live_orphan.pid)
    with pytest.raises(IncidentDeferredError) as e:
        inc.prepare_incident(sess, None, None, now=NOW)
    assert e.value.code == "deferred_recorder_alive" and "stop it yourself" in e.value.hint
    assert live_orphan.poll() is None


# ------------------------------------------------------------------ queue: durable, visible, restart-safe


def test_defer_session_and_register_unprepared_are_stat_only_and_idempotent(tmp_path, monkeypatch):
    done = make_session(tmp_path, sid="s1")
    running = make_session(tmp_path, sid="s2")
    make_stale(running)
    nobag = make_session(tmp_path, "start_fail", sid="s3")
    monkeypatch.setattr(checksums, "sha256_file", lambda *a, **k: pytest.fail("register_unprepared must not hash anything"))
    marked = inc.register_unprepared(done.parent, now=NOW)
    assert marked == [done]  # the stale `running` session and the never-recorded one are not queued
    assert attempt(done)["code"] == "deferred_stale_session" and attempt(done)["scheduled_by"] == "service"
    assert not (running / "incident_attempt.json").exists() and not (nobag / "incident_attempt.json").exists()
    assert inc.register_unprepared(done.parent, now=NOW) == []  # idempotent: already queued


def test_deferred_work_is_discoverable_from_a_fresh_process(tmp_path):
    """'After restart': a brand-new interpreter, knowing nothing, finds the durable queue entry."""
    sess = make_session(tmp_path)
    inc.defer_session(sess, code="deferred_service_mode", message="m", hint="h", now=NOW)
    r = subprocess.run([sys.executable, "-m", "physicore_collector", "incident", "pending", "list", str(sess.parent), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    (item,) = json.loads(r.stdout)
    assert item["kind"] == "deferred" and item["last_code"] == "deferred_service_mode" and item["blocked_by"] is None
    assert item["retry_eligible"] is True and not (sess / "incident").exists()


def test_pending_list_classifies_every_kind_and_what_blocks_it(tmp_path, live_orphan):
    deferred = make_session(tmp_path, sid="a-deferred")
    inc.defer_session(deferred, code="deferred_service_mode", message="m", now=NOW)
    failed_retry = make_session(tmp_path, sid="b-failed")
    inc._record_attempt(failed_retry, "failed", NOW, scheduled_by="auto", code="io_error", message="disk hiccup")
    failed_stuck = make_session(tmp_path, sid="c-stuck")
    inc._record_attempt(failed_stuck, "failed", NOW, scheduled_by="auto", code="target_exists", message="in the way")
    make_session(tmp_path, sid="d-unprepared")
    stale = make_session(tmp_path, sid="e-stale")
    make_stale(stale)
    orphan = make_session(tmp_path, sid="f-orphan")
    make_stale(orphan)
    set_recorder(orphan, live_orphan.pid)
    done = make_session(tmp_path, sid="g-done")
    inc.prepare_incident(done, None, None, now=NOW)
    items = {i.session_dir.name: i for i in inc.list_pending([tmp_path / "caps"])}
    assert set(items) == {"a-deferred", "b-failed", "c-stuck", "d-unprepared", "e-stale", "f-orphan"}  # prepared one is not pending
    assert items["a-deferred"].kind == "deferred" and items["b-failed"].kind == "failed" and items["b-failed"].retry_eligible
    assert not items["c-stuck"].retry_eligible and "fix the cause" in items["c-stuck"].action
    assert items["d-unprepared"].kind == "unprepared" and items["e-stale"].kind == "needs_reconcile"
    assert items["f-orphan"].blocked_by == "recorder_may_be_writing" and "--stop-orphan" in items["f-orphan"].action
    assert all(i.blocked_by is None for k, i in items.items() if k != "f-orphan")
    with Active(tmp_path / "caps"):
        again = {i.session_dir.name: i for i in inc.list_pending([tmp_path / "caps"])}
    assert again["a-deferred"].blocked_by == "capture_active" and again["f-orphan"].blocked_by == "recorder_may_be_writing"
    assert (tmp_path / "caps" / "d-unprepared" / "incident_attempt.json").exists() is False  # listing wrote nothing


def test_attempt_schema_1_0_files_from_day_4_are_still_read_and_unknown_versions_ignored(tmp_path):
    sess = make_session(tmp_path)
    day4 = {"attempt_schema_version": "1.0", "state": "failed", "attempted_at_utc": "2026-01-01T00:00:00Z", "incident": None,
            "code": "io_error", "message": "x", "hint": None, "retry_command": f"physicore-collector incident prepare {sess}"}
    (sess / "incident_attempt.json").write_text(json.dumps(day4))
    (item,) = inc.list_pending([sess.parent])
    assert item.kind == "failed" and item.last_code == "io_error" and item.attempt_count == 1
    inc.prepare_incident(sess, None, None, now=NOW)  # a successful retry upgrades it in place
    assert attempt(sess)["attempt_schema_version"] == "1.1" and attempt(sess)["attempt_count"] == 2
    (sess / "incident_attempt.json").write_text(json.dumps({**day4, "attempt_schema_version": "9.9"}))
    assert inc._read_attempt(sess) is None


# ------------------------------------------------------------------ draining the queue safely


def test_run_pending_recovers_a_stale_session_prepares_it_and_never_calls_it_complete(tmp_path):
    sess = make_session(tmp_path)
    make_stale(sess)
    (res,) = inc.run_pending([sess.parent], now=NOW)
    assert res.action == "prepared" and res.reconciled == "reconciled" and res.outcome == "degraded"
    rec, _ = inc.load_incident(sess)
    assert rec.capture_health.state == "interrupted" and rec.outcome == "degraded"
    assert rec.artifact_integrity.state == "verified"  # the bytes verify ...
    assert rec.session_status.value != "complete" or rec.capture_health.state == "interrupted"  # ... that never upgrades the capture
    assert any("INTERRUPTED" in x for x in rec.outcome_reasons)
    assert load_health(sess / "capture_health.json").health.state.value == "interrupted"  # recovery used the existing logic
    assert inc.run_pending([sess.parent], now=NOW) == []  # idempotent: nothing left


def test_run_pending_drains_oldest_first_and_continues_after_a_failure(tmp_path, monkeypatch):
    a, b, c = (make_session(tmp_path, sid=f"20260101T00000{i}Z-x") for i in range(3))
    real = export_mod.copy_and_hash

    def fail_for_a(src, dst, **k):
        if "T000000Z" in str(src):
            raise OSError(errno.EIO, "I/O error")
        return real(src, dst, **k)
    monkeypatch.setattr(export_mod, "copy_and_hash", fail_for_a)
    results = inc.run_pending([a.parent], now=NOW)
    assert [r.session_dir.name for r in results] == [a.name, b.name, c.name]  # oldest first
    assert [r.action for r in results] == ["failed", "prepared", "prepared"] and results[0].code == "io_error"
    assert attempt(a)["state"] == "failed" and not (a / "incident").exists() and not list(a.glob(".*staging*"))
    monkeypatch.setattr(export_mod, "copy_and_hash", real)
    (retry,) = inc.run_pending([a.parent], now=NOW)  # the failed one is retried, nothing else is touched
    assert retry.session_dir == a and retry.action == "prepared" and attempt(a)["attempt_count"] == 2


def test_run_pending_does_nothing_while_a_capture_is_active_and_writes_nothing(tmp_path):
    sess = make_session(tmp_path)
    before = snap(sess)
    with Active(sess.parent):
        (res,) = inc.run_pending([sess.parent], now=NOW)
    assert res.action == "deferred" and res.code == "deferred_capture_active"
    assert snap(sess) == before and not (sess / ".incident.prepare.lock").exists()


def test_run_pending_yields_when_a_capture_starts_mid_run_and_stops_the_whole_run(tmp_path, monkeypatch):
    s1, s2 = make_session(tmp_path, sid="20260101T000001Z-a"), make_session(tmp_path, sid="20260101T000002Z-b")
    _capture_starts_after_first_copy(monkeypatch, tmp_path / "caps")
    results = inc.run_pending([s1.parent], now=NOW)
    assert results[0].action == "deferred" and results[1].action == "skipped" and results[1].code == "deferred_capture_active"
    assert not (s1 / "incident").exists() and not (s2 / "incident").exists()
    assert not list(s1.glob(".*staging*")) and attempt(s1)["state"] == "deferred" and not (s2 / "incident_attempt.json").exists()


def test_run_pending_blocks_on_an_orphaned_recorder_and_stops_it_only_through_verified_recovery(tmp_path, live_orphan):
    sess = make_session(tmp_path)
    make_stale(sess)
    set_recorder(sess, live_orphan.pid)
    (r1,) = inc.run_pending([sess.parent], now=NOW)  # default: never signals
    assert r1.action == "deferred" and r1.code == "deferred_recorder_alive" and live_orphan.poll() is None
    assert attempt(sess)["state"] == "deferred" and not (sess / "incident").exists()
    (r2,) = inc.run_pending([sess.parent], stop_orphan=True, now=NOW)  # explicit opt-in, recovery's identity-checked path
    assert r2.action == "prepared" and r2.reconciled == "reconciled"
    assert live_orphan.wait(timeout=20) is not None
    rec, _ = inc.load_incident(sess)
    assert rec.capture_health.state == "interrupted" and rec.outcome == "degraded"


def test_an_active_capture_gates_everything_even_for_a_session_with_its_own_blocker(tmp_path, live_orphan):
    """Regression (found by the demo): the orphan's own blocker used to take precedence, so `pending run --stop-orphan`
    would have stopped a recorder and hashed its bag WHILE another capture was recording."""
    sess = make_session(tmp_path)
    make_stale(sess)
    set_recorder(sess, live_orphan.pid)
    before = evidence(sess)
    with Active(sess.parent):
        (item,) = inc.list_pending([sess.parent])
        assert item.blocked_by == "recorder_may_be_writing" and item.capture_active is True
        (res,) = inc.run_pending([sess.parent], stop_orphan=True, now=NOW)
    assert res.action == "deferred" and res.code == "deferred_capture_active" and res.reconciled is None
    assert live_orphan.poll() is None and evidence(sess) == before  # not signalled, not reconciled, not hashed
    assert load_health(sess / "capture_health.json").health.state.value == "running"


def test_run_pending_respects_max_and_needs_attention_and_no_recover(tmp_path):
    s1, s2 = make_session(tmp_path, sid="20260101T000001Z-a"), make_session(tmp_path, sid="20260101T000002Z-b")
    inc._record_attempt(s1, "failed", NOW, scheduled_by="auto", code="input_error", message="no profile")
    stale = make_session(tmp_path, sid="20260101T000003Z-c")
    make_stale(stale)
    res = {r.session_dir.name: r for r in inc.run_pending([s1.parent], recover=False, max_items=0, now=NOW)}
    assert all(r.action == "skipped" for r in res.values()) and res[s2.name].code == "max_items"
    res = {r.session_dir.name: r for r in inc.run_pending([s1.parent], recover=False, now=NOW)}
    assert res[s1.name].action == "skipped" and "needs attention" in res[s1.name].detail
    assert res[stale.name].action == "skipped" and "--no-recover" in res[stale.name].detail and res[s2.name].action == "prepared"
    assert load_health(stale / "capture_health.json").health.state.value == "running"  # --no-recover really did not recover


def test_source_that_changes_after_inspection_is_caught_and_leaves_nothing_behind(tmp_path, monkeypatch):
    sess = make_session(tmp_path)
    seg = next((sess / "bag").glob("*.mcap"))
    real = export_mod.copy_and_hash

    def mutate_then_copy(src, dst, **k):
        if str(src) == str(seg):
            seg.write_bytes(seg.read_bytes() + b"\0late-write")  # the recording changed after the initial inspection
        return real(src, dst, **k)
    monkeypatch.setattr(export_mod, "copy_and_hash", mutate_then_copy)
    out = inc.auto_prepare_after_capture(sess)
    assert out.state == "failed" and out.code == "checksum_mismatch"
    assert not (sess / "incident").exists() and not list(sess.glob(".*staging*"))
    assert attempt(sess)["state"] == "failed" and attempt(sess)["code"] == "checksum_mismatch"
    monkeypatch.setattr(export_mod, "copy_and_hash", real)
    r = inc.prepare_incident(sess, None, None, now=NOW)  # retry judges the file as it is NOW, and says so
    assert r.record.artifact_integrity.capture_end_digests == "mismatch" and r.record.outcome == "degraded"


def test_listing_never_calls_an_unchecked_package_verified_and_tells_tampered_from_unverifiable(tmp_path):
    sess = make_session(tmp_path, sid="s1")
    inc.prepare_incident(sess, None, None, now=NOW)
    (row,) = inc.list_incidents(sess.parent)
    assert row.state == "previously_prepared" and row.outcome == "success"  # stored result: no claim about NOW
    assert inc.list_incidents(sess.parent, verify=True)[0].state == "verified_now"
    seg = next((sess / "incident" / "package" / "recording").rglob("*.mcap"))
    original = seg.read_bytes()
    seg.write_bytes(original + b"x")
    assert inc.list_incidents(sess.parent)[0].state == "previously_prepared"  # still just the stored record ...
    (row,) = inc.list_incidents(sess.parent, verify=True)  # ... until it is actually checked
    assert row.state == "tampered" and "package" in row.detail
    seg.write_bytes(original)
    with Active(sess.parent):  # re-hashing everything would compete with a recording: skipped, and NOT a pass
        (row,) = inc.list_incidents(sess.parent, verify=True)
        assert row.state == "unverifiable" and "capture is active" in row.detail
        assert inc.list_incidents(sess.parent, verify=True, respect_active=False)[0].state == "verified_now"
    (sess / "incident" / "incident.json").write_text("{not json")
    assert inc.list_incidents(sess.parent, verify=True)[0].state == "unreadable"
    assert inc.verify_incident_detailed(sess / "incident").state == "unverifiable"


def test_stale_staging_next_to_a_session_is_recovered_by_the_next_run(tmp_path):
    sess = make_session(tmp_path)
    stale = sess / ".incident.incident-staging-1-deadbeef"
    (stale / "package").mkdir(parents=True)
    (stale / "package" / "half.mcap").write_bytes(b"x" * 4096)
    (res,) = inc.run_pending([sess.parent], now=NOW)
    assert res.action == "prepared" and not stale.exists()


def test_old_day1_to_day4_behaviour_is_unchanged_by_the_scheduling_gate(fx, synth_profile, tmp_path):
    """No capture anywhere: everything behaves exactly as in Day 4 (same package, same record shape)."""
    r = inc.prepare_incident(fx / "valid.mcap", synth_profile, tmp_path / "inc", now=NOW)
    assert r.state == "prepared" and r.record.outcome == "success" and inc.verify_incident(r.incident_dir) == []
    shutil.rmtree(r.incident_dir)
    r2 = inc.prepare_incident(fx / "valid.mcap", synth_profile, tmp_path / "inc", now=datetime(2026, 1, 2, tzinfo=timezone.utc),
                              respect_active_captures=False)
    assert r2.record.source_fingerprint == r.record.source_fingerprint
