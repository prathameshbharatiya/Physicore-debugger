import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from physicore_collector.cli import app

runner = CliRunner()


def run(*args):
    return runner.invoke(app, [str(a) for a in args])


def leftovers(parent):
    return [p.name for p in Path(parent).iterdir() if ".staging-" in p.name]


# ---------------- session inspect ----------------
def test_inspect_valid_exit_0(fx):
    r = run("session", "inspect", fx / "valid.mcap")
    assert r.exit_code == 0, r.output
    assert "Readability: READABLE" in r.stdout and "Messages read: 123" in r.stdout
    assert "log_time min: 1700000000000000000 ns" in r.stdout
    assert "/joint_states" in r.stdout and "messages=100" in r.stdout
    assert "Not verified:" in r.stdout


def test_inspect_json_is_parseable(fx):
    r = run("session", "inspect", fx / "valid.mcap", "--json")
    assert r.exit_code == 0
    j = json.loads(r.stdout)
    assert j["readability"] == "readable" and j["total_message_count"] == 123
    assert j["recording_window"] == {"min_log_time_ns": 1_700_000_000_000_000_000, "max_log_time_ns": 1_700_000_002_000_000_000}
    assert {t["topic"] for t in j["topics"]} == {"/joint_states", "/camera/image", "/diagnostics"}


def test_inspect_truncated_exit_1_shows_recovered_data(fx):
    r = run("session", "inspect", fx / "truncated.mcap")
    assert r.exit_code == 1
    assert "PARTIALLY READABLE" in r.stdout and "truncated" in r.stdout and "end magic NOT CONFIRMED" in r.stdout
    assert "not read and is unknown" in r.stdout


def test_inspect_unreadable_exit_2_even_with_json(fx):
    r = run("session", "inspect", fx / "not_mcap.mcap", "--json")
    assert r.exit_code == 2 and json.loads(r.stdout)["readability"] == "unreadable"


def test_inspect_missing_file_exit_2_error_on_stderr(tmp_path):
    r = run("session", "inspect", tmp_path / "nope.mcap")
    assert r.exit_code == 2 and r.stdout == "" and "ERROR: Recording not found" in r.stderr


# ---------------- session validate ----------------
def test_validate_complete_exit_0(fx, synth_profile):
    r = run("session", "validate", fx / "valid.mcap", "--profile", synth_profile)
    assert r.exit_code == 0, r.output
    assert "STATUS: COMPLETE" in r.stdout
    assert "Loss detection: NOT PERFORMED" in r.stdout and "NOT evidence that no data was lost" in r.stdout
    assert hashlib.sha256((fx / "valid.mcap").read_bytes()).hexdigest() in r.stdout


def test_validate_cadence_gap_exit_1_with_findings_and_disclaimer(fx, synth_profile):
    r = run("session", "validate", fx / "cadence_gap.mcap", "--profile", synth_profile)
    assert r.exit_code == 1
    assert "STATUS: PARTIAL" in r.stdout
    assert "0.520000 s between log_time 1700000000480000000 and 1700000001000000000 (threshold 0.04 s)" in r.stdout
    assert "NOT proof of message loss" in r.stdout


def test_validate_missing_topic_exit_1(fx, synth_profile):
    r = run("session", "validate", fx / "missing_topic.mcap", "--profile", synth_profile)
    assert r.exit_code == 1 and "[FAIL] (required) topic_present:/camera/image" in r.stdout


def test_validate_truncated_exit_1_partial(fx, synth_profile):
    r = run("session", "validate", fx / "truncated.mcap", "--profile", synth_profile)
    assert r.exit_code == 1 and "STATUS: PARTIAL" in r.stdout


def test_validate_malformed_is_failed_exit_1_not_a_crash(fx, synth_profile):
    r = run("session", "validate", fx / "not_mcap.mcap", "--profile", synth_profile)
    assert r.exit_code == 1 and "STATUS: FAILED" in r.stdout


@pytest.mark.parametrize("trig,fragment", [
    ("trigger_unknown_basis.json", "Trigger comparison: unavailable"),
    ("trigger_labels_match_unattested.json", "Trigger comparison: inconclusive"),
    ("trigger_declared_incompatible.json", "Trigger comparison: unavailable"),
])
def test_validate_trigger_with_unestablished_basis_exit_1(fx, synth_profile, trig, fragment):
    r = run("session", "validate", fx / "valid.mcap", "--profile", synth_profile, "--trigger", fx / "triggers" / trig)
    assert r.exit_code == 1 and fragment in r.stdout and "STATUS: PARTIAL" in r.stdout


def test_validate_attested_trigger_within_window_exit_0(fx, synth_profile):
    r = run("session", "validate", fx / "valid.mcap", "--profile", synth_profile, "--trigger", fx / "triggers/trigger_attested_within.json")
    assert r.exit_code == 0 and "within_recording_window" in r.stdout


def test_validate_json_output(fx, synth_profile):
    r = run("session", "validate", fx / "cadence_gap.mcap", "--profile", synth_profile, "--json")
    assert r.exit_code == 1
    j = json.loads(r.stdout)
    assert j["validation"]["status"] == "partial" and j["validation"]["cadence_findings"][0]["kind"] == "observed_log_time_gap"
    assert j["loss_detection_coverage"]["no_loss_claim_permitted"] is False


def test_validate_metadata_option_satisfies_required_keys(tmp_path, fx, synth_profile):
    from tests import make_fixtures as mf
    rec = tmp_path / "nometa.mcap"
    rec.write_bytes(mf.build_mcap([mf.joint_states(), mf.camera()]))  # no MCAP metadata record
    bad = run("session", "validate", rec, "--profile", synth_profile)
    assert bad.exit_code == 1 and "metadata:operator" in bad.stdout and "[FAIL]" in bad.stdout
    good = run("session", "validate", rec, "--profile", synth_profile, "--metadata", fx / "metadata.json")
    assert good.exit_code == 0


def test_validate_input_errors_exit_2(fx, synth_profile, tmp_path):
    assert run("session", "validate", tmp_path / "nope.mcap", "--profile", synth_profile).exit_code == 2
    assert run("session", "validate", fx / "valid.mcap", "--profile", tmp_path / "nope.yaml").exit_code == 2
    r = run("session", "validate", fx / "valid.mcap", "--profile", synth_profile, "--trigger", tmp_path / "nope.json")
    assert r.exit_code == 2 and "Trigger file not found" in r.stderr
    bad = tmp_path / "t.json"
    bad.write_text('{"trigger_id": "x"}')
    r = run("session", "validate", fx / "valid.mcap", "--profile", synth_profile, "--trigger", bad)
    assert r.exit_code == 2 and "timestamp_basis: Field required" in r.stderr
    r = run("session", "validate", fx / "valid.mcap", "--profile", synth_profile, "--metadata", tmp_path / "nope.json")
    assert r.exit_code == 2 and "Metadata file not found" in r.stderr


def test_validate_requires_profile_option(fx):
    assert run("session", "validate", fx / "valid.mcap").exit_code == 2


def test_validate_invalid_profile_exit_2_with_field_path(fx, tmp_path):
    p = tmp_path / "p.yaml"
    p.write_text('profile_schema_version: "1.0"\nprofile_id: x\ntopics: [{name: /a, expected_hz: -1}]\n')
    r = run("session", "validate", fx / "valid.mcap", "--profile", p)
    assert r.exit_code == 2 and "topics[0].expected_hz" in r.stderr


# ---------------- export ----------------
def test_export_complete_exit_0_and_byte_identical(fx, synth_profile, tmp_path):
    out = tmp_path / "pkg"
    r = run("export", fx / "valid.mcap", "--profile", synth_profile, "--out", out,
            "--metadata", fx / "metadata.json", "--trigger", fx / "triggers/trigger_attested_within.json")
    assert r.exit_code == 0, r.output
    assert "STATUS: COMPLETE" in r.stdout and "byte-for-byte" in r.stdout
    assert "does not contain its own checksum" in r.stdout and "Loss detection: NOT PERFORMED" in r.stdout
    assert (out / "recording/valid.mcap").read_bytes() == (fx / "valid.mcap").read_bytes()
    m = json.loads((out / "manifest.json").read_text())
    rec = next(a for a in m["artifacts"] if a["role"] == "recording")
    assert rec["sha256"] in r.stdout
    assert rec["sha256"] == hashlib.sha256((fx / "valid.mcap").read_bytes()).hexdigest()
    assert leftovers(tmp_path) == []


def test_export_partial_session_writes_package_and_exits_1(fx, synth_profile, tmp_path):
    r = run("export", fx / "cadence_gap.mcap", "--profile", synth_profile, "--out", tmp_path / "pkg")
    assert r.exit_code == 1 and "STATUS: PARTIAL" in r.stdout
    assert json.loads((tmp_path / "pkg/manifest.json").read_text())["session_status"] == "partial"


def test_export_failed_session_writes_package_and_exits_1(fx, synth_profile, tmp_path):
    r = run("export", fx / "not_mcap.mcap", "--profile", synth_profile, "--out", tmp_path / "pkg")
    assert r.exit_code == 1 and "STATUS: FAILED" in r.stdout
    assert (tmp_path / "pkg/recording/not_mcap.mcap").read_bytes() == (fx / "not_mcap.mcap").read_bytes()


def test_export_refuses_existing_target_exit_2_and_does_not_touch_it(fx, synth_profile, tmp_path):
    out = tmp_path / "pkg"
    out.mkdir()
    (out / "keep.txt").write_text("keep")
    r = run("export", fx / "valid.mcap", "--profile", synth_profile, "--out", out)
    assert r.exit_code == 2 and "already exists" in r.stderr and "never overwrites" in r.stderr
    assert [p.name for p in out.iterdir()] == ["keep.txt"]


def test_export_target_check_happens_before_reading_inputs(tmp_path, synth_profile):
    out = tmp_path / "pkg"
    out.mkdir()
    r = run("export", tmp_path / "does_not_exist.mcap", "--profile", synth_profile, "--out", out)
    assert r.exit_code == 2 and "already exists" in r.stderr  # fails fast on the target first


def test_export_has_no_force_option(fx, synth_profile, tmp_path):
    r = run("export", fx / "valid.mcap", "--profile", synth_profile, "--out", tmp_path / "p", "--force")
    assert r.exit_code == 2 and not (tmp_path / "p").exists()


def test_export_input_errors_exit_2_and_leave_nothing(fx, synth_profile, tmp_path):
    out = tmp_path / "pkg"
    assert run("export", tmp_path / "nope.mcap", "--profile", synth_profile, "--out", out).exit_code == 2
    assert run("export", fx / "valid.mcap", "--profile", tmp_path / "nope.yaml", "--out", out).exit_code == 2
    assert run("export", fx / "valid.mcap", "--profile", synth_profile, "--out", out, "--trigger", tmp_path / "x.json").exit_code == 2
    assert list(tmp_path.iterdir()) == []


def test_export_missing_parent_exit_2(fx, synth_profile, tmp_path):
    r = run("export", fx / "valid.mcap", "--profile", synth_profile, "--out", tmp_path / "a" / "b")
    assert r.exit_code == 2 and "Parent directory" in r.stderr and not (tmp_path / "a").exists()


def test_help_for_every_command(fx):
    for args in (["--help"], ["session", "inspect", "--help"], ["session", "validate", "--help"], ["export", "--help"], ["profile", "validate", "--help"]):
        assert run(*args).exit_code == 0


def test_export_write_failure_is_clean_error_exit_2_not_traceback_and_leaves_nothing(fx, synth_profile, tmp_path, monkeypatch):
    import physicore_collector.export as ex

    def disk_full(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ex, "_write_new", disk_full)
    r = run("export", fx / "valid.mcap", "--profile", synth_profile, "--out", tmp_path / "pkg")
    assert r.exit_code == 2  # NOT 1: exit 1 is reserved for "session did not pass"
    assert "ERROR: Export failed while writing the package: No space left on device" in r.stderr
    assert "Traceback" not in r.output
    assert list(tmp_path.iterdir()) == []
