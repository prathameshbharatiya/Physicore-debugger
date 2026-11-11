"""CLI behaviour for Day 2: bag inputs, session directories, package verify, capture run/status/stop."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app
from tests.bagtools import three_way_split, write_bag
from tests.recorder_doubles import make_fake_ros2
from tests.test_supervisor import PROFILE_11

runner = CliRunner()


def invoke(*args):
    return runner.invoke(app, [str(a) for a in args])


# ---------------------------------------------------------------- inspect / validate / export on bags


def test_inspect_bag_directory_text_and_json(tmp_path):
    bag = write_bag(tmp_path / "b", three_way_split())
    r = invoke("session", "inspect", bag)
    assert r.exit_code == 0, r.output
    assert "rosbag2 bag directory: b" in r.output and "b_1.mcap  [readable]" in r.output and "Messages read: 123" in r.output
    j = json.loads(invoke("session", "inspect", bag, "--json").output)
    assert j["recording_kind"] == "rosbag2_directory" and len(j["bag"]["segments"]) == 3 and j["total_message_count"] == 123


def test_inspect_single_mcap_json_is_unchanged(fx):
    j = json.loads(invoke("session", "inspect", fx / "valid.mcap", "--json").output)
    assert "bag" not in j and "recording_kind" not in j and j["readability"] == "readable"


def test_inspect_exit_codes_for_bags(tmp_path):
    bag = write_bag(tmp_path / "b", three_way_split(), with_metadata=False)
    assert invoke("session", "inspect", bag).exit_code == 1  # partially inspectable
    (tmp_path / "e").mkdir()
    assert invoke("session", "inspect", tmp_path / "e").exit_code == 2  # empty directory: nothing inspectable
    dead = write_bag(tmp_path / "d")
    (dead / "d_0.mcap").write_bytes(b"junk")
    assert invoke("session", "inspect", dead).exit_code == 2  # unreadable
    assert invoke("session", "inspect", tmp_path / "nope").exit_code == 2


def test_validate_bag_exit_codes(tmp_path, synth_profile):
    good = write_bag(tmp_path / "g", three_way_split())
    r = invoke("session", "validate", good, "--profile", synth_profile)
    assert r.exit_code == 0 and "STATUS: COMPLETE" in r.output and "bag:segments" in r.output
    nomd = write_bag(tmp_path / "n", three_way_split(), with_metadata=False)
    r = invoke("session", "validate", nomd, "--profile", synth_profile)
    assert r.exit_code == 1 and "STATUS: PARTIAL" in r.output and "MISSING" in r.output
    j = json.loads(invoke("session", "validate", good, "--profile", synth_profile, "--json").output)
    assert j["recording_kind"] == "rosbag2_directory" and j["validation"]["report_schema_version"] == "0.2"


def test_export_and_verify_roundtrip_via_cli(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "b", three_way_split())
    r = invoke("export", bag, "--profile", synth_profile, "--out", tmp_path / "pkg")
    assert r.exit_code == 0, r.output
    v = invoke("package", "verify", tmp_path / "pkg")
    assert v.exit_code == 0 and "VERIFIED: 6 artifact(s)" in v.output and "does not show the data is true" in v.output
    assert json.loads(invoke("package", "verify", tmp_path / "pkg", "--json").output)["verified"] is True
    assert invoke("export", bag, "--profile", synth_profile, "--out", tmp_path / "pkg").exit_code == 2  # never overwrites
    seg = tmp_path / "pkg" / "recording" / "b" / "b_1.mcap"
    seg.write_bytes(seg.read_bytes()[:-1] + b"\x01")
    v = invoke("package", "verify", tmp_path / "pkg")
    assert v.exit_code == 1 and "VERIFICATION FAILED" in v.output and "b_1.mcap" in v.output


def test_package_verify_errors(tmp_path):
    assert invoke("package", "verify", tmp_path / "nope").exit_code == 2
    (tmp_path / "empty").mkdir()
    r = invoke("package", "verify", tmp_path / "empty")
    assert r.exit_code == 1 and "manifest.json is missing" in r.output


def test_package_verify_works_on_the_day1_package():
    r = invoke("package", "verify", Path(__file__).parent / "compat" / "day1_package")
    assert r.exit_code == 0 and "manifest schema 0.1" in r.output


def test_help_for_every_new_command():
    for cmd in (["package", "verify"], ["capture", "run"], ["capture", "status"], ["capture", "stop"]):
        r = invoke(*cmd, "--help")
        assert r.exit_code == 0 and "Exit codes" in " ".join(r.output.split()), cmd


# ---------------------------------------------------------------- capture, real subprocess + stand-in ros2


@pytest.fixture
def env_with_fake_ros2(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"; bindir.mkdir()
    make_fake_ros2(bindir)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir


@pytest.fixture
def profile11(tmp_path):
    p = tmp_path / "p.yaml"; p.write_text(PROFILE_11)
    return p


def test_capture_run_duration_then_validate_export_verify(tmp_path, env_with_fake_ros2, profile11):
    out = tmp_path / "caps"; out.mkdir()
    r = invoke("capture", "run", profile11, "--out", out, "--duration", "0.6", "--session-id", "s1")
    assert r.exit_code == 0, r.output
    assert "state: STOPPED_CLEAN" in r.output and "appears finalized: yes" in r.output and "Next:" in r.output
    # session directory is accepted directly by validate/export (bag/ + sidecar found automatically)
    v = invoke("session", "validate", out / "s1", "--profile", profile11)
    assert v.exit_code == 0, v.output
    assert "capture:lifecycle" in v.output and "capture:output_unchanged" in v.output
    e = invoke("export", out / "s1", "--profile", profile11, "--out", tmp_path / "pkg")
    assert e.exit_code == 0, e.output
    assert invoke("package", "verify", tmp_path / "pkg").exit_code == 0
    st = invoke("capture", "status", out / "s1")
    assert st.exit_code == 0 and "STOPPED_CLEAN" in st.output
    assert json.loads(invoke("capture", "status", out / "s1", "--json").output)["state"] == "stopped_clean"


def test_capture_run_refuses_existing_session(tmp_path, env_with_fake_ros2, profile11):
    out = tmp_path / "caps"; (out / "s1").mkdir(parents=True)
    r = invoke("capture", "run", profile11, "--out", out, "--duration", "0.2", "--session-id", "s1")
    assert r.exit_code == 2 and "never overwrites" in r.output


def test_capture_run_without_ros2_is_exit_2_with_actionable_error(tmp_path, monkeypatch, profile11):
    monkeypatch.setenv("PATH", "/nonexistent")
    out = tmp_path / "caps"; out.mkdir()
    r = invoke("capture", "run", profile11, "--out", out, "--duration", "0.2", "--session-id", "s1")
    assert r.exit_code == 2 and "Source your ROS 2 setup" in r.output
    st = invoke("capture", "status", out / "s1")  # the failed attempt is on record
    assert st.exit_code == 1 and "FAILED_TO_START" in st.output


def test_capture_run_invalid_profile_is_exit_2(tmp_path):
    p = tmp_path / "bad.yaml"; p.write_text('profile_schema_version: "1.0"\nprofile_id: x\ntopics: [{name: /a}]\ncapture: {}\n')
    r = invoke("capture", "run", p, "--out", tmp_path, "--duration", "1")
    assert r.exit_code == 2 and "requires profile_schema_version" in r.output


def test_capture_crash_exit_code_1(tmp_path, env_with_fake_ros2, profile11, monkeypatch):
    monkeypatch.setenv("FAKE_ROS2_MODE", "crash")
    out = tmp_path / "caps"; out.mkdir()
    r = invoke("capture", "run", profile11, "--out", out, "--duration", "5", "--session-id", "s1")
    assert r.exit_code == 1 and "EXITED_UNEXPECTEDLY" in r.output
    assert not (out / "s1" / "bag").exists()  # crashed before writing anything
    st = invoke("capture", "status", out / "s1")
    assert st.exit_code == 1 and "EXITED_UNEXPECTEDLY" in st.output and "expected output missing" in st.output


def test_capture_status_of_dead_supervisor_is_interrupted(tmp_path, env_with_fake_ros2, profile11):
    out = tmp_path / "caps"; out.mkdir()
    assert invoke("capture", "run", profile11, "--out", out, "--duration", "0.3", "--session-id", "s1").exit_code == 0
    sc = out / "s1" / "capture_health.json"
    d = json.loads(sc.read_text()); d["state"] = "running"; d["supervisor"]["pid"] = 2**22 + 12345
    d["bag_finalization"] = {"appears_finalized": None, "basis": "x"}
    sc.write_text(json.dumps(d))
    st = invoke("capture", "status", out / "s1")
    assert st.exit_code == 1 and "INTERRUPTED" in st.output and "never recorded an outcome" in st.output
    sp = invoke("capture", "stop", out / "s1")
    assert sp.exit_code == 1 and "interrupted" in sp.output
    v = invoke("session", "validate", out / "s1", "--profile", profile11)
    assert v.exit_code == 1 and "known to be interrupted" in v.output


def test_capture_status_without_record(tmp_path):
    r = invoke("capture", "status", tmp_path)
    assert r.exit_code == 2 and "No capture-health record" in r.output


def test_capture_stop_signals_a_real_running_supervisor(tmp_path, env_with_fake_ros2, profile11):
    """Two real processes: `capture run` in the background, then `capture status` and `capture stop`."""
    out = tmp_path / "caps"; out.mkdir()
    ready = tmp_path / "recorder.ready"
    os.environ["FAKE_ROS2_READY_FILE"] = str(ready)  # inherited by the child; removed in finally
    exe = [str(Path(sys.executable).parent / "physicore-collector")]
    proc = subprocess.Popen(exe + ["capture", "run", str(profile11), "--out", str(out), "--session-id", "live"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=os.environ.copy())
    try:
        sidecar = out / "live" / "capture_health.json"
        deadline = time.time() + 15
        while time.time() < deadline:
            if sidecar.exists() and json.loads(sidecar.read_text())["state"] == "running":
                break
            time.sleep(0.1)
        else:
            pytest.fail("capture never reached the running state")
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)  # the recorder must have installed its SIGINT handler before we stop it
        assert ready.exists(), "stand-in recorder never became ready"
        st = invoke("capture", "status", out / "live")
        assert st.exit_code == 0 and "RUNNING" in st.output and "is running" in st.output
        sp = invoke("capture", "stop", out / "live", "--wait", "20")
        assert sp.exit_code == 0, sp.output
        assert "STOPPED_CLEAN" in sp.output
        assert proc.wait(timeout=20) == 0
    finally:
        os.environ.pop("FAKE_ROS2_READY_FILE", None)
        if proc.poll() is None:
            proc.kill()
    v = invoke("session", "validate", out / "live", "--profile", profile11)
    assert v.exit_code == 0, v.output
