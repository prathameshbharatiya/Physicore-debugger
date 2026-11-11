"""Incident CLI contract and automatic preparation inside the REAL capture lifecycle.

Real here: subprocesses, signals, flock, the detached supervisor, the service-run process.
NOT real: the recorder is a stand-in script (tests/recorder_doubles.py), not rosbag2; systemd is not involved."""

import errno
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector import export as export_mod
from physicore_collector.capture_health import CaptureState, load_health
from physicore_collector.cli import app
from physicore_collector.service import ServiceSpec, render_unit
from tests.bagtools import three_way_split, write_bag
from tests.recorder_doubles import make_fake_ros2
from tests.test_supervisor import PROFILE_11

runner = CliRunner()
EXE = str(Path(sys.executable).parent / "physicore-collector")


def cli(*args):
    return runner.invoke(app, [str(a) for a in args])


@pytest.fixture
def world(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"; bindir.mkdir(); make_fake_ros2(bindir)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_ROS2_EARLY_BAG", "1")
    prof = tmp_path / "p.yaml"; prof.write_text(PROFILE_11)
    out = tmp_path / "caps"; out.mkdir()
    return type("W", (), {"tmp": tmp_path, "bin": bindir, "profile": prof, "out": out})


def wait_for(pred, timeout=30, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


# ------------------------------------------------------------------ prepare: exit codes and output


def test_prepare_existing_bag_success_exit_0_and_json(synth_profile, tmp_path):
    bag = write_bag(tmp_path / "b" / "bagx", three_way_split())
    r = cli("incident", "prepare", bag, "--profile", synth_profile, "--out", tmp_path / "inc")
    assert r.exit_code == 0, r.output
    assert "SUCCESS" in r.output and "Four separate statements" in r.output and "Capture health: UNKNOWN" in r.output
    j = cli("incident", "prepare", bag, "--profile", synth_profile, "--out", tmp_path / "inc", "--json")  # idempotent retry
    assert j.exit_code == 0, j.output
    payload = json.loads(j.stdout)
    assert payload["result"] == "success" and payload["state"] == "already_prepared"
    assert payload["incident"]["segments"][2]["order"] == 2


def test_prepare_degraded_exit_1_but_package_is_written(fx, synth_profile, tmp_path):
    r = cli("incident", "prepare", fx / "cadence_gap.mcap", "--profile", synth_profile, "--out", tmp_path / "inc")
    assert r.exit_code == 1 and "DEGRADED" in r.output and "Why this is DEGRADED" in r.output
    assert (tmp_path / "inc" / "package" / "manifest.json").is_file()
    assert cli("package", "verify", tmp_path / "inc" / "package").exit_code == 0


def test_prepare_failure_exit_2_with_actionable_message_and_json(fx, synth_profile, tmp_path):
    (tmp_path / "inc").mkdir()
    r = cli("incident", "prepare", fx / "valid.mcap", "--profile", synth_profile, "--out", tmp_path / "inc", "--json")
    assert r.exit_code == 2
    assert json.loads(r.stdout)["error"]["code"] == "target_exists" and "ERROR:" in r.output and "Hint:" in r.output
    r2 = cli("incident", "prepare", tmp_path / "missing.mcap", "--profile", synth_profile, "--out", tmp_path / "x")
    assert r2.exit_code == 2 and "not found" in r2.output
    r3 = cli("incident", "prepare", fx / "valid.mcap", "--out", tmp_path / "y")
    assert r3.exit_code == 2 and "No profile" in r3.output


def test_prepare_dry_run_and_space_prediction(fx, synth_profile, tmp_path, monkeypatch):
    r = cli("incident", "prepare", fx / "valid.mcap", "--profile", synth_profile, "--out", tmp_path / "inc", "--dry-run", "--json")
    assert r.exit_code == 0 and json.loads(r.stdout)["sufficient_space"] is True and not (tmp_path / "inc").exists()
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 90, 10))
    r2 = cli("incident", "prepare", fx / "valid.mcap", "--profile", synth_profile, "--out", tmp_path / "inc")
    assert r2.exit_code == 2 and "Not enough free space" in r2.output and not (tmp_path / "inc").exists()


def test_list_and_show_and_verify_detect_tampering(fx, synth_profile, tmp_path):
    root = tmp_path / "all"; root.mkdir()
    cli("incident", "prepare", fx / "valid.mcap", "--profile", synth_profile, "--out", root / "good")
    cli("incident", "prepare", fx / "cadence_gap.mcap", "--profile", synth_profile, "--out", root / "bad")
    r = cli("incident", "list", root)
    assert r.exit_code == 0 and "good  PREVIOUSLY PREPARED (not re-verified)  evidence=SUCCESS" in r.output
    assert "bad  PREVIOUSLY PREPARED (not re-verified)  evidence=DEGRADED" in r.output and "not a fresh check" in r.output
    assert "VERIFIED" not in r.output  # a stored result is never presented as verified
    rows = json.loads(cli("incident", "list", root, "--json").stdout)
    assert {x["name"]: (x["state"], x["outcome"]) for x in rows} == {"good": ("previously_prepared", "success"),
                                                                     "bad": ("previously_prepared", "degraded")}
    v = cli("incident", "list", root, "--verify")
    assert "good  VERIFIED NOW  evidence=SUCCESS" in v.output and "bad  VERIFIED NOW  evidence=DEGRADED" in v.output  # independent axes
    s = cli("incident", "show", root / "good")
    assert s.exit_code == 0 and "STORED RESULT: not re-verified now" in s.output
    s = cli("incident", "show", root / "good", "--verify")
    assert s.exit_code == 0 and "VERIFIED NOW" in s.output and "not show the data is true" in s.output
    j = json.loads(cli("incident", "show", root / "good", "--json", "--verify").stdout)["verification"]
    assert j["state"] == "verified_now" and j["verified"] is True
    assert json.loads(cli("incident", "show", root / "good", "--json").stdout)["verification"] == {"state": "not_checked"}
    seg = root / "good" / "package" / "recording" / "valid.mcap"
    seg.write_bytes(seg.read_bytes()[:-1] + b"X")
    t = cli("incident", "show", root / "good", "--verify")
    assert t.exit_code == 1 and "TAMPERED" in t.output
    assert "good  TAMPERED" in cli("incident", "list", root, "--verify").output
    assert "good  PREVIOUSLY PREPARED" in cli("incident", "list", root).output  # unchecked listing still cannot know
    assert cli("incident", "show", tmp_path).exit_code == 2
    assert cli("incident", "list", tmp_path / "nope").exit_code == 2


# ------------------------------------------------------------------ automatic preparation, real lifecycle


def test_capture_run_prepares_automatically_and_does_not_change_exit_code(world):
    r = cli("capture", "run", world.profile, "--out", world.out, "--duration", 1, "--session-id", "r1")
    assert r.exit_code == 0, r.output
    assert "Incident package prepared: SUCCESS" in r.output
    inc = world.out / "r1" / "incident"
    assert (inc / "incident.json").is_file() and (inc / "package" / "capture" / "capture_health.json").is_file()
    assert cli("incident", "show", world.out / "r1", "--verify").exit_code == 0


def test_no_auto_prepare_flag_disables_it(world):
    r = cli("capture", "run", world.profile, "--out", world.out, "--duration", 1, "--session-id", "r2", "--no-auto-prepare")
    assert r.exit_code == 0 and "disabled" in r.output
    assert sorted(os.listdir(world.out / "r2")) == sorted(
        x for x in os.listdir(world.out / "r2") if x not in ("incident", "incident_attempt.json"))
    assert not (world.out / "r2" / "incident").exists() and not (world.out / "r2" / "incident_attempt.json").exists()
    (row,) = json.loads(cli("incident", "list", world.out, "--json").stdout)
    assert row["state"] == "not_prepared"
    assert cli("incident", "prepare", world.out / "r2").exit_code == 0  # the manual path works afterwards


def test_failed_auto_preparation_is_loud_keeps_capture_exit_code_and_is_retryable(world, monkeypatch):
    monkeypatch.setattr(export_mod, "copy_and_hash", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left")))
    r = cli("capture", "run", world.profile, "--out", world.out, "--duration", 1, "--session-id", "r3")
    assert r.exit_code == 0  # the CAPTURE was clean: a preparation problem must not rewrite that
    assert "automatic incident preparation FAILED (insufficient_space)" in r.output and "incident prepare" in r.output
    sess = world.out / "r3"
    assert not (sess / "incident").exists() and load_health(sess / "capture_health.json").health.state is CaptureState.STOPPED_CLEAN
    monkeypatch.undo()
    monkeypatch.setenv("PATH", f"{world.bin}{os.pathsep}{os.environ['PATH']}")
    assert cli("incident", "prepare", sess).exit_code == 0
    assert json.loads((sess / "incident_attempt.json").read_text())["state"] == "prepared"


def test_unclean_capture_run_still_exits_1_and_the_incident_says_degraded(world, monkeypatch):
    monkeypatch.setenv("FAKE_ROS2_MODE", "crash")
    r = cli("capture", "run", world.profile, "--out", world.out, "--session-id", "r4")
    assert r.exit_code == 1  # unchanged capture semantics
    sess = world.out / "r4"
    if (sess / "bag").exists():
        rec = json.loads((sess / "incident" / "incident.json").read_text())
        assert rec["outcome"] == "degraded" and rec["capture_health"]["state"] in ("interrupted", "failed")
    else:
        assert "no recording was produced" in r.output and not (sess / "incident").exists()


def test_detached_capture_prepares_automatically_after_stop(world):
    sess = world.out / "d1"
    r = cli("capture", "start", world.profile, "--out", world.out, "--session-id", "d1")
    assert r.exit_code == 0, r.output
    assert "Incident package after stop" in r.output and "automatic" in r.output
    try:
        wait_for((sess / "bag").is_dir, what="recorder bag")
        s = cli("capture", "stop", sess)
        assert s.exit_code == 0, s.output
        inc = wait_for(lambda: (sess / "incident" / "incident.json").is_file(), what="automatic incident")
        assert inc
        wait_for(lambda: (sess / "incident_attempt.json").is_file(), what="attempt record")
        assert json.loads((sess / "incident_attempt.json").read_text())["state"] == "prepared"
        assert "Incident package prepared" in (sess / "supervisor.log").read_text()
        assert cli("incident", "show", sess, "--verify").exit_code == 0
    finally:
        h = load_health(sess / "capture_health.json").health
        for rec in (h.recorder.identity, h.supervisor.identity):
            if rec is not None:
                try:
                    os.kill(rec.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_detached_capture_with_no_auto_prepare_makes_no_incident(world):
    sess = world.out / "d2"
    assert cli("capture", "start", world.profile, "--out", world.out, "--session-id", "d2", "--no-auto-prepare").exit_code == 0
    wait_for((sess / "bag").is_dir, what="recorder bag")
    assert cli("capture", "stop", sess).exit_code == 0
    wait_for(lambda: "disabled" in (sess / "supervisor.log").read_text(), what="supervisor log note")
    assert not (sess / "incident").exists() and not (sess / "incident_attempt.json").exists()
    assert "disabled" in (sess / "supervisor.log").read_text()


# ------------------------------------------------------------------ service-run


def _service_setup(world, extra=""):
    setup = world.tmp / "setup.bash"
    setup.write_text(f'export PATH="{world.bin}:$PATH"\nexport ROS_DISTRO=testbot\nexport FAKE_ROS2_EARLY_BAG=1\n{extra}')
    return setup


def _run_service(world, setup, *extra):
    return subprocess.Popen([EXE, "capture", "service-run", "--name", "robot-a", "--profile", str(world.profile), "--out", str(world.out),
                             "--ros-setup", str(setup), *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={"PATH": os.environ["PATH"], "HOME": str(world.tmp)})


def _running_session(world):
    def find():
        for d in sorted(world.out.iterdir()):
            try:
                if (d / "bag").is_dir() and load_health(d / "capture_health.json").health.state is CaptureState.RUNNING:
                    return d
            except Exception:
                continue
    return wait_for(find, what="service running")


def test_service_run_never_prepares_inline_and_queues_the_session_after_a_clean_stop(world):
    """Day 5 (supersedes Day 4, which prepared inline here): a stop or `systemctl restart` must never wait for copying."""
    p = _run_service(world, _service_setup(world))
    sess = _running_session(world)
    time.sleep(0.3)
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, (out, err)
    assert "Incident preparation DEFERRED (service mode)" in out and not (sess / "incident").exists()
    att = json.loads((sess / "incident_attempt.json").read_text())
    assert att["state"] == "deferred" and att["code"] == "deferred_service_mode" and att["scheduled_by"] == "service"
    assert cli("incident", "pending", "run", world.out).exit_code == 0  # drained when idle
    assert (sess / "incident" / "incident.json").is_file()


def test_service_run_queues_an_unclean_end_without_delaying_the_restart(world):
    p = _run_service(world, _service_setup(world, "export FAKE_ROS2_MODE=crash\n"))
    out, err = p.communicate(timeout=60)
    assert p.returncode == 1, (out, err)  # systemd would restart it now: nothing waited for preparation
    assert "Incident preparation DEFERRED (service mode)" in out and "incident pending run" in out
    (sess,) = [d for d in world.out.iterdir() if (d / "capture_health.json").exists()]
    assert not (sess / "incident").exists()
    assert json.loads((sess / "incident_attempt.json").read_text())["state"] == "deferred"


def test_service_run_flag_and_unit_generation_for_opt_out(world):
    spec = ServiceSpec(name="robot-a", user="daemon", executable=EXE, profile=str(world.profile), out=str(world.out))
    on, off = render_unit(spec), render_unit(ServiceSpec(**{**spec.__dict__, "auto_prepare": False}))
    assert "--no-auto-prepare" not in on  # default unit text is unchanged
    exec_line = next(l for l in off.splitlines() if l.startswith("ExecStart="))
    assert exec_line.split()[-1] == "--no-auto-prepare"
    p = _run_service(world, _service_setup(world), "--no-auto-prepare")
    sess = _running_session(world)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 0 and "disabled" in out and not (sess / "incident").exists()
    assert not (sess / "incident_attempt.json").exists()  # opted out: not even queued


def test_install_service_dry_run_honours_no_auto_prepare(world):
    r = cli("capture", "install-service", "--name", "robot-a", "--user", "daemon", "--profile", world.profile, "--out", world.out,
            "--executable", EXE, "--dry-run", "--no-auto-prepare")
    assert r.exit_code == 0 and "--no-auto-prepare" in r.output
