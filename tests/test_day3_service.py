"""Unattended capture: ROS environment loading, systemd unit generation/installation, and the
`service-run` process that the unit executes.

REAL here: `systemd-analyze verify` on generated units (when present), bash sourcing of setup
scripts, `service-run` as an actual process with real signals/locks.
NOT TESTED (no systemd running in the dev environment): real `systemctl start/stop/restart`,
boot ordering, sandboxing against a real DDS stack. See DAY3_IMPLEMENTATION_REPORT.md."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector import service as svc
from physicore_collector.capture_health import CaptureState, load_health
from physicore_collector.cli import app
from physicore_collector.errors import CaptureConfigError
from physicore_collector.ros_env import load_ros_environment, parse_env_assignments, validate_setup_file
from physicore_collector.service import ServiceSpec, render_unit, validate_spec
from tests.recorder_doubles import make_fake_ros2
from tests.test_supervisor import PROFILE_11

runner = CliRunner()
EXE = str(Path(sys.executable).parent / "physicore-collector")


def cli(*args):
    return runner.invoke(app, [str(a) for a in args])


@pytest.fixture
def world(tmp_path):
    bindir = tmp_path / "bin"; bindir.mkdir(); make_fake_ros2(bindir)
    setup = tmp_path / "setup.bash"
    setup.write_text(f'export PATH="{bindir}:$PATH"\nexport ROS_DISTRO=testbot\nexport FAKE_ROS2_EARLY_BAG=1\n'
                     f'export FAKE_ROS2_ENV_FILE="{tmp_path}/recorder_env.json"\n')
    prof = tmp_path / "p.yaml"; prof.write_text(PROFILE_11)
    out = tmp_path / "caps"; out.mkdir()
    units = tmp_path / "units"; units.mkdir()
    return type("W", (), {"tmp": tmp_path, "bin": bindir, "setup": setup, "profile": prof, "out": out, "units": units})


def spec(w, **kw):
    base = dict(name="robot-a", user="daemon", executable=EXE, profile=str(w.profile), out=str(w.out), ros_setups=(str(w.setup),))
    base.update(kw)
    return ServiceSpec(**base)


# ------------------------------------------------------------------ ROS environment


def test_environment_comes_from_the_named_setup_scripts_not_from_the_parent(world, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    monkeypatch.setenv("ROS_DISTRO", "parent-value")
    env, exe = load_ros_environment([str(world.setup)])
    assert env["ROS_DISTRO"] == "testbot" and exe == str(world.bin / "ros2")
    assert "SECRET_TOKEN" not in env  # the parent's environment is NOT forwarded
    assert not {"_", "SHLVL", "PWD", "OLDPWD"} & set(env)


def test_forwarding_is_an_allow_list_with_safe_values(world):
    assert parse_env_assignments(["ROS_DOMAIN_ID=7", "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"]) == {
        "ROS_DOMAIN_ID": "7", "RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp"}
    for bad in ("PATH=/tmp", "LD_PRELOAD=/x.so", "HOME=/root", "ROS_DOMAIN_ID", "ROS_DOMAIN_ID=1;id", "ROS_DOMAIN_ID=$(id)", "ROS_DOMAIN_ID=a b"):
        with pytest.raises(CaptureConfigError):
            parse_env_assignments([bad])
    with pytest.raises(CaptureConfigError, match="more than once"):
        parse_env_assignments(["ROS_DOMAIN_ID=1", "ROS_DOMAIN_ID=2"])
    env, _ = load_ros_environment([str(world.setup)], {"ROS_DOMAIN_ID": "42"})
    assert env["ROS_DOMAIN_ID"] == "42"


@pytest.mark.parametrize("bad", ["relative/setup.bash", "/tmp/a b/setup.bash", "/tmp/x;rm -rf $HOME", "/tmp/$(id)/s.bash", "/tmp/s`id`.bash", "", "/tmp/a\nb"])
def test_setup_paths_that_could_inject_into_a_shell_are_refused(bad):
    with pytest.raises(CaptureConfigError, match="must be absolute|does not exist"):
        validate_setup_file(bad)


def test_injection_through_a_crafted_file_name_does_not_execute(world):
    """Even a path that passes the character filter is passed as a positional argument, never
    interpolated: the constant script is the only thing bash parses."""
    marker = world.tmp / "pwned"
    evil = world.tmp / "x@+-.bash"
    evil.write_text("export OK=1\n")
    env, _ = (lambda: load_ros_environment([str(world.setup), str(evil)]))()
    assert env["OK"] == "1" and not marker.exists()


def test_world_writable_or_foreign_owned_setup_scripts_are_refused(world):
    os.chmod(world.setup, 0o666)
    with pytest.raises(CaptureConfigError, match="writable by any user"):
        validate_setup_file(str(world.setup))
    os.chmod(world.setup, 0o644)
    validate_setup_file(str(world.setup))
    os.chown(world.setup, 4242, 4242)
    with pytest.raises(CaptureConfigError, match="neither root nor the service user"):
        validate_setup_file(str(world.setup), trusted_uids=set()) if os.getuid() != 4242 else None
    os.chown(world.setup, 0, 0)


def test_failing_or_incomplete_setup_is_an_error_not_a_fallback(world):
    bad = world.tmp / "bad.bash"; bad.write_text("false\n")
    with pytest.raises(CaptureConfigError, match="Sourcing the ROS setup scripts failed"):
        load_ros_environment([str(bad)])
    empty = world.tmp / "empty.bash"; empty.write_text("export FOO=1\n")
    with pytest.raises(CaptureConfigError, match="`ros2` is not on PATH after sourcing"):
        load_ros_environment([str(empty)])
    with pytest.raises(CaptureConfigError, match="does not exist"):
        load_ros_environment([str(world.tmp / "missing.bash")])


def test_multiline_and_odd_values_survive_sourcing(world):
    s = world.tmp / "odd.bash"
    s.write_text(f'export PATH="{world.bin}:$PATH"\nexport MULTI="line1\nline2 = x"\nexport EMPTY=""\n')
    env, _ = load_ros_environment([str(s)])
    assert env["MULTI"] == "line1\nline2 = x" and env["EMPTY"] == ""


# ------------------------------------------------------------------ unit rendering


def test_rendered_unit_has_the_required_safety_and_lifecycle_properties(world):
    text = render_unit(spec(world, env={"ROS_DOMAIN_ID": "7"}, stop_timeout_seconds=20))
    lines = text.splitlines()
    assert lines[0].startswith("# Managed by physicore-collector")
    assert "User=daemon" in lines and "Restart=on-failure" in lines and "RestartPreventExitStatus=78" in lines
    assert "NoNewPrivileges=yes" in lines and "ProtectSystem=strict" in lines and "KillMode=mixed" in lines
    assert "TimeoutStopSec=35" in lines  # profile stop timeout + margin: systemd never kills before we have tried
    exec_line = next(l for l in lines if l.startswith("ExecStart="))
    tokens = exec_line.split()
    assert tokens[0] == f"ExecStart={EXE}"  # the collector itself is executed ...
    assert not any(Path(t).name in ("bash", "sh", "dash", "zsh") or t == "-c" for t in tokens)  # ... never a shell
    assert f"--ros-setup {world.setup}" in exec_line and "--env ROS_DOMAIN_ID=7" in exec_line
    assert f"ReadWritePaths={world.out} /tmp" in lines
    assert "WantedBy=multi-user.target" in lines
    assert text == render_unit(spec(world, env={"ROS_DOMAIN_ID": "7"}, stop_timeout_seconds=20))  # deterministic


def test_restart_no_is_honoured(world):
    assert "Restart=no" in render_unit(spec(world, restart="no")).splitlines()


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze not available")
def test_generated_unit_passes_systemd_analyze_verify(world):
    r = svc.install_service(spec(world), world.units, systemctl=False)
    p = subprocess.run(["systemd-analyze", "verify", "--man=no", str(r.unit_path)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert [l for l in p.stderr.splitlines() if l.strip()] == [] or all("Special user" in l for l in p.stderr.splitlines() if l.strip())


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze not available")
def test_systemd_analyze_actually_rejects_a_broken_unit(world, tmp_path):
    """Guard against a vacuous verify test: the same tool must fail on a deliberately broken unit."""
    bad = tmp_path / "broken.service"
    bad.write_text("[Service]\nExecStart=/nonexistent/path/xyz\nType=bogus\n")
    p = subprocess.run(["systemd-analyze", "verify", "--man=no", str(bad)], capture_output=True, text=True)
    assert p.returncode != 0 or p.stderr.strip()


@pytest.mark.parametrize("field, value, frag", [
    ("name", "Bad Name", "Invalid service name"), ("name", "../x", "Invalid service name"), ("name", "1abc", "Invalid service name"),
    ("user", "root", "Refusing to run the capture service as root"), ("user", "no-such-user-xyz", "does not exist"),
    ("user", "a b", "Invalid user name"), ("restart", "always", "--restart must be"),
    ("executable", "relative/exe", "absolute path"), ("executable", "/bin/ls with space", "absolute path"),
    ("profile", "/tmp/p$HOME.yaml", "absolute path"), ("out", "/tmp/o%u", "absolute path"),
])
def test_unsafe_or_invalid_specs_are_refused(world, field, value, frag):
    with pytest.raises(CaptureConfigError, match=frag):
        validate_spec(spec(world, **{field: value}))


def test_nonexistent_paths_world_writable_exe_and_bad_profile_are_refused(world):
    with pytest.raises(CaptureConfigError, match="does not exist"):
        validate_spec(spec(world, out=str(world.tmp / "nope")))
    with pytest.raises(CaptureConfigError, match="does not exist"):
        validate_spec(spec(world, profile=str(world.tmp / "nope.yaml")))
    bad = world.tmp / "bad.yaml"; bad.write_text("profile_schema_version: '9'\n")
    with pytest.raises(Exception, match="(?i)profile|schema"):
        validate_spec(spec(world, profile=str(bad)))
    exe = world.tmp / "collector"; exe.write_text("#!/bin/sh\n"); os.chmod(exe, 0o777)
    with pytest.raises(CaptureConfigError, match="writable by any user"):
        validate_spec(spec(world, executable=str(exe)))
    with pytest.raises(CaptureConfigError, match="Cannot forward"):
        validate_spec(spec(world, env={"LD_PRELOAD": "/x.so"}))


# ------------------------------------------------------------------ install / uninstall (files only)


def test_install_does_not_enable_or_start_unless_asked(world, monkeypatch):
    calls = []
    monkeypatch.setattr(svc, "_systemctl", lambda *a: (calls.append(a), (True, ""))[1])
    r = svc.install_service(spec(world), world.units)
    assert calls == [("daemon-reload",)]  # no enable, no start: installing is not opting in to boot recording
    assert any("NOT enabled at boot" in n for n in r.notes)
    calls.clear()
    svc.uninstall_service("robot-a", world.units)
    svc.install_service(spec(world), world.units, enable=True, start=True)
    assert ("enable", "physicore-collector-robot-a.service") in calls and ("start", "physicore-collector-robot-a.service") in calls


def test_install_refuses_to_overwrite_and_never_touches_foreign_units(world):
    svc.install_service(spec(world), world.units, systemctl=False)
    with pytest.raises(CaptureConfigError, match="already exists"):
        svc.install_service(spec(world), world.units, systemctl=False)
    svc.install_service(spec(world, restart="no"), world.units, systemctl=False, force=True)
    assert "Restart=no" in (world.units / "physicore-collector-robot-a.service").read_text()
    foreign = world.units / "physicore-collector-robot-b.service"; foreign.write_text("[Service]\nExecStart=/bin/true\n")
    with pytest.raises(CaptureConfigError, match="not written by physicore-collector"):
        svc.install_service(spec(world, name="robot-b"), world.units, systemctl=False, force=True)
    assert foreign.read_text() == "[Service]\nExecStart=/bin/true\n"


def test_uninstall_removes_only_our_unit_and_never_recordings(world):
    svc.install_service(spec(world), world.units, systemctl=False)
    session = world.out / "sess"; session.mkdir(); (session / "keep.mcap").write_bytes(b"evidence")
    notes = svc.uninstall_service("robot-a", world.units, systemctl=False)
    assert not (world.units / "physicore-collector-robot-a.service").exists()
    assert (session / "keep.mcap").read_bytes() == b"evidence" and any("not touched" in n for n in notes)
    with pytest.raises(CaptureConfigError, match="No unit"):
        svc.uninstall_service("robot-a", world.units, systemctl=False)
    f = world.units / "physicore-collector-x.service"; f.write_text("[Service]\n")
    with pytest.raises(CaptureConfigError, match="refusing to remove"):
        svc.uninstall_service("x", world.units, systemctl=False)
    assert f.exists()


def test_cli_install_dry_run_writes_nothing_and_cli_roundtrip(world):
    r = cli("capture", "install-service", "--name", "robot-a", "--user", "daemon", "--profile", world.profile, "--out", world.out,
            "--ros-setup", world.setup, "--executable", EXE, "--dest-dir", world.units, "--dry-run")
    assert r.exit_code == 0 and "ExecStart=" in r.output and not list(world.units.iterdir())
    r = cli("capture", "install-service", "--name", "robot-a", "--user", "daemon", "--profile", world.profile, "--out", world.out,
            "--ros-setup", world.setup, "--executable", EXE, "--dest-dir", world.units, "--no-systemctl")
    assert r.exit_code == 0 and "Installed" in r.output and (world.units / "physicore-collector-robot-a.service").is_file()
    r = cli("capture", "install-service", "--name", "robot-a", "--user", "root", "--profile", world.profile, "--out", world.out,
            "--executable", EXE, "--dest-dir", world.units, "--no-systemctl")
    assert r.exit_code == 2 and "root" in r.output
    r = cli("capture", "uninstall-service", "--name", "robot-a", "--dest-dir", world.units, "--no-systemctl")
    assert r.exit_code == 0 and "recordings and capture sessions were not touched" in r.output


def test_install_into_an_unwritable_directory_gives_actionable_guidance(world):
    if os.getuid() == 0:
        pytest.skip("root can write anywhere")
    os.chmod(world.units, 0o555)
    with pytest.raises(CaptureConfigError, match="permission denied"):
        svc.install_service(spec(world), world.units, systemctl=False)


# ------------------------------------------------------------------ service-run as a real process


def run_service(w, *extra, **kw):
    return subprocess.Popen([EXE, "capture", "service-run", "--name", "robot-a", "--profile", str(w.profile), "--out", str(w.out),
                             "--ros-setup", str(w.setup), *extra], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env={"PATH": os.environ["PATH"], "HOME": str(w.tmp)}, **kw)


def sessions(w):
    return sorted(d for d in w.out.iterdir() if d.is_dir() and (d / "capture_health.json").exists())


def wait_running(w, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for d in sessions(w):
            try:
                h = load_health(d / "capture_health.json").health
            except Exception:
                continue
            if h.state is CaptureState.RUNNING and (d / "bag").is_dir():
                return d
        time.sleep(0.05)
    pytest.fail("service never reached running")


def test_service_run_records_until_sigterm_then_stops_gracefully_with_exit_0(world):
    p = run_service(world)
    sess = wait_running(world)
    h = load_health(sess / "capture_health.json").health
    assert h.supervisor.launch_mode == "service" and h.supervisor.service_name == "robot-a"
    time.sleep(0.3)
    t0 = time.time()
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=30)
    assert p.returncode == 0 and time.time() - t0 < 15, (out, err)
    h = load_health(sess / "capture_health.json").health
    assert h.state is CaptureState.STOPPED_CLEAN and h.bag_finalization.appears_finalized is True
    assert h.timing.stop_reason == "signal SIGTERM"
    assert h.environment == {"ROS_DISTRO": "testbot"}  # recorded from the recorder's real environment
    # the recorder ran with the environment built from the setup script ONLY
    renv = json.loads((world.tmp / "recorder_env.json").read_text())
    assert renv["ROS_DISTRO"] == "testbot" and "PYTHONPATH" not in renv and "SECRET" not in renv


def test_a_second_service_instance_cannot_start_while_one_is_running(world):
    p1 = run_service(world)
    wait_running(world)
    p2 = run_service(world)
    out2, err2 = p2.communicate(timeout=30)
    assert p2.returncode == 78 and "already running" in err2  # EX_CONFIG: systemd will NOT restart it
    assert len(sessions(world)) == 1  # no second session, no second recorder
    p1.send_signal(signal.SIGTERM); p1.communicate(timeout=30)
    assert p1.returncode == 0


def test_service_startup_settles_a_stale_session_and_stops_its_orphan_recorder(world):
    """Crash recovery: a previous supervisor was SIGKILLed while its recorder kept going."""
    env = os.environ.copy(); env.update(PATH=f"{world.bin}{os.pathsep}{env['PATH']}", FAKE_ROS2_EARLY_BAG="1")
    r = subprocess.run([sys.executable, "-m", "physicore_collector", "capture", "start", str(world.profile), "--out", str(world.out),
                        "--session-id", "old"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    old = world.out / "old"
    h = load_health(old / "capture_health.json").health
    os.kill(h.supervisor.identity.pid, signal.SIGKILL)
    deadline = time.time() + 10
    while time.time() < deadline:
        from physicore_collector.liveness import supervisor_state
        if supervisor_state(old) == "dead":
            break
        time.sleep(0.05)
    from physicore_collector import procident as pi
    from physicore_collector.procident import ProcessIdentity
    rid = h.recorder.identity
    assert pi.check_identity(ProcessIdentity(rid.pid, rid.starttime_ticks, rid.boot_id)) is pi.IdentityMatch.SAME  # orphan alive
    p = run_service(world)
    deadline = time.time() + 20
    new = None
    while time.time() < deadline and new is None:
        cands = [d for d in sessions(world) if d.name != "old"]
        if cands and (cands[0] / "bag").is_dir() and load_health(cands[0] / "capture_health.json").health.state is CaptureState.RUNNING:
            new = cands[0]  # 'bag exists' == the stand-in installed its SIGINT handler: it is ready to be stopped
        time.sleep(0.05)
    assert new is not None, "service did not start a new session"
    old_h = load_health(old / "capture_health.json").health
    assert old_h.state is CaptureState.INTERRUPTED and old_h.reconciliation.orphan_recorder_action == "stopped_gracefully"
    assert pi.check_identity(ProcessIdentity(rid.pid, rid.starttime_ticks, rid.boot_id)) is not pi.IdentityMatch.SAME  # orphan gone
    assert new.name != "old" and new.resolve() != old.resolve()  # a NEW session: the old one is never resumed or overwritten
    assert (old / "bag" / "metadata.yaml").is_file()  # the orphan was stopped gracefully, so its bag was properly closed
    p.send_signal(signal.SIGTERM); out, err = p.communicate(timeout=30)
    states = {d.name: load_health(d / "capture_health.json").health.state.value for d in sessions(world)}
    assert p.returncode == 0 and "startup reconciliation: old: reconciled" in out, (p.returncode, out, err[-800:], states)


def test_service_run_configuration_errors_exit_78_so_they_are_not_restarted(world):
    r = subprocess.run([EXE, "capture", "service-run", "--name", "robot-a", "--profile", str(world.profile), "--out", str(world.out),
                        "--ros-setup", str(world.tmp / "missing.bash")], capture_output=True, text=True)
    assert r.returncode == 78 and "does not exist" in r.stderr
    r = subprocess.run([EXE, "capture", "service-run", "--name", "robot-a", "--profile", str(world.tmp / "nope.yaml"), "--out", str(world.out)],
                       capture_output=True, text=True)
    assert r.returncode == 78
    assert sessions(world) == []  # nothing was started, nothing was created


def test_recorder_crash_makes_service_run_exit_1_so_systemd_restarts_with_a_new_session(world):
    s = world.setup.read_text() + 'export FAKE_ROS2_MODE=crash\n'
    world.setup.write_text(s)
    p = run_service(world)
    out, err = p.communicate(timeout=30)
    assert p.returncode == 1, (out, err)
    (sess,) = sessions(world)
    assert load_health(sess / "capture_health.json").health.state is CaptureState.EXITED_UNEXPECTEDLY
    p2 = run_service(world)  # what a systemd restart does: a NEW session, the failed one preserved
    p2.communicate(timeout=30)
    assert len(sessions(world)) == 2


def test_missing_ros2_after_sourcing_is_a_config_error(world):
    only_env = world.tmp / "noros.bash"; only_env.write_text("export FOO=1\n")
    r = subprocess.run([EXE, "capture", "service-run", "--name", "robot-a", "--profile", str(world.profile), "--out", str(world.out),
                        "--ros-setup", str(only_env)], capture_output=True, text=True)
    assert r.returncode == 78 and "`ros2` is not on PATH after sourcing" in r.stderr


@pytest.mark.parametrize("name", ["has space.bash", "semi;colon.bash", "dollar$HOME.bash", "back`tick`.bash", "quote'q.bash", "pct%u.bash"])
def test_existing_setup_scripts_with_shell_significant_names_are_still_refused(world, name):
    """The character filter must reject paths that EXIST (a nonexistent path would fail anyway, which
    would hide a missing filter)."""
    odd = world.tmp / name
    odd.write_text("export OK=1\n")
    assert odd.is_file()
    with pytest.raises(CaptureConfigError, match="must be absolute and use only"):
        validate_setup_file(str(odd))
    with pytest.raises(CaptureConfigError, match="must be absolute and use only"):
        load_ros_environment([str(world.setup), str(odd)])
