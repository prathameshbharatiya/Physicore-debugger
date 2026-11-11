"""VR: verification of evidence references against the recording (FR-VR-*): exact statuses, read-only, never crashes."""
import copy
import hashlib
import shutil
from pathlib import Path

import pytest

from physicore_collector.investigation_view.service import run_render
from physicore_collector.investigation_view.verify import verify_view

from .fx import EXAMPLE_BAG, EXAMPLE_JSON, example_dict, to_bytes, view_of


def tree_hash(p: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p.rglob("*")) if p.is_dir() else [p]:
        if f.is_file():
            h.update(str(f.relative_to(p) if p.is_dir() else f.name).encode()); h.update(f.read_bytes())
    return h.hexdigest()


@pytest.fixture()
def bag(tmp_path):
    dst = tmp_path / "bag"
    shutil.copytree(EXAMPLE_BAG, dst)
    return dst


def test_VR_01_everything_verifies_against_the_original(bag):
    r = verify_view(view_of(), bag)
    assert r["summary"] == {"verified": 3, "mismatch": 0, "not_checked": 0}
    assert all(x["status"] == "verified" for x in r["refs"].values()) and all(x["status"] == "verified" for x in r["artifacts"].values())


def test_VR_02_a_single_file_path_works(bag):
    assert verify_view(view_of(), bag / "bag_0.mcap")["summary"]["verified"] == 3


def test_VR_03_a_single_file_with_another_name_is_not_checked(bag):
    other = bag / "other.mcap"; shutil.copy(bag / "bag_0.mcap", other)
    r = verify_view(view_of(), other)
    assert r["summary"]["verified"] == 0 and r["summary"]["not_checked"] == 3


def test_VR_04_a_changed_byte_is_a_digest_mismatch(bag):
    f = bag / "bag_0.mcap"; b = bytearray(f.read_bytes()); b[len(b) // 2] ^= 1; f.write_bytes(bytes(b))
    r = verify_view(view_of(), bag)
    assert r["artifacts"]["artifact:bag_0.mcap"] == {"status": "mismatch", "detail": "the SHA-256 of the file differs from the report"}
    assert all(x["status"] == "mismatch" and "SHA-256" in x["detail"] for x in r["refs"].values())


def test_VR_05_a_truncated_artifact_is_a_mismatch(bag):
    f = bag / "bag_0.mcap"; f.write_bytes(f.read_bytes()[:100])
    assert verify_view(view_of(), bag)["summary"]["mismatch"] == 3


def test_VR_06_a_missing_artifact_is_not_checked(bag):
    (bag / "bag_0.mcap").unlink()
    r = verify_view(view_of(), bag)
    assert r["summary"] == {"verified": 0, "mismatch": 0, "not_checked": 3}
    assert "not present" in r["artifacts"]["artifact:bag_0.mcap"]["detail"]


def test_VR_07_a_wrong_message_index_is_a_mismatch(bag):
    v = view_of(); v["evidence_refs"][0]["message_index"] = 999
    r = verify_view(v, bag)
    assert r["refs"]["E1"] == {"status": "mismatch", "detail": "there is no message at this position"} and r["refs"]["E2"]["status"] == "verified"


def test_VR_08_a_wrong_topic_is_a_mismatch(bag):
    v = view_of(); v["evidence_refs"][0]["topic"] = "/other"
    assert verify_view(v, bag)["refs"]["E1"] == {"status": "mismatch", "detail": "the topic at this position differs"}


def test_VR_09_a_wrong_time_is_a_mismatch(bag):
    v = view_of(); v["evidence_refs"][1]["time_ns"] += 1
    assert verify_view(v, bag)["refs"]["E2"] == {"status": "mismatch", "detail": "the log time at this position differs"}


def test_VR_10_a_reference_digest_that_differs_is_a_mismatch(bag):
    v = view_of(); v["evidence_refs"][0]["sha256"] = "0" * 64
    assert verify_view(v, bag)["refs"]["E1"]["status"] == "mismatch"


def test_VR_11_a_reference_to_an_unlisted_artifact_is_not_checked(bag):
    v = view_of(); v["evidence_refs"][0]["artifact_ref"] = "artifact:other.mcap"
    r = verify_view(v, bag)
    assert r["refs"]["E1"]["status"] == "not_checked" and "not listed" in r["refs"]["E1"]["detail"]


@pytest.mark.parametrize("ref", ["artifact:../x.mcap", "artifact:/etc/passwd", "artifact:a/../../x", "file:bag_0.mcap", "artifact:", "bag_0.mcap"])
def test_VR_12_references_that_leave_the_evidence_location_are_never_opened(bag, ref):
    v = view_of()
    v["header"]["evidence"] = [{"artifact_ref": ref, "sha256": "0" * 64, "verification": None}]
    v["evidence_refs"][0]["artifact_ref"] = ref
    r = verify_view(v, bag)
    assert r["artifacts"][ref]["status"] == "not_checked" and r["refs"]["E1"]["status"] == "not_checked"


def test_VR_13_a_symlink_that_leaves_the_directory_is_not_followed(bag, tmp_path):
    outside = tmp_path / "outside.mcap"; shutil.copy(bag / "bag_0.mcap", outside)
    (bag / "bag_0.mcap").unlink(); (bag / "bag_0.mcap").symlink_to(outside)
    r = verify_view(view_of(), bag)
    assert r["summary"]["verified"] == 0 and r["artifacts"]["artifact:bag_0.mcap"]["status"] == "not_checked"


def test_VR_14_an_unreadable_container_with_a_matching_digest_is_not_checked(bag):
    f = bag / "bag_0.mcap"; f.write_bytes(b"not an mcap file at all" * 10)
    v = view_of(); v["header"]["evidence"][0]["sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
    for r in v["evidence_refs"]:
        r["sha256"] = v["header"]["evidence"][0]["sha256"]
    r = verify_view(v, bag)
    assert r["artifacts"]["artifact:bag_0.mcap"]["status"] == "verified"
    assert all(x["status"] == "not_checked" and "cannot read" in x["detail"] for x in r["refs"].values())


def test_VR_15_the_recording_is_never_modified(bag):
    before = tree_hash(bag)
    verify_view(view_of(), bag); verify_view(view_of(), bag / "bag_0.mcap")
    assert tree_hash(bag) == before


def test_VR_16_a_non_log_time_domain_is_verified_without_comparing_time(bag):
    v = view_of(); v["evidence_refs"][0]["clock_domain"] = "payload_stamp"; v["evidence_refs"][0]["time_ns"] += 5
    r = verify_view(v, bag)["refs"]["E1"]
    assert r["status"] == "verified" and "not compared" in r["detail"]


def test_VR_17_a_reference_without_time_or_topic_checks_position_and_digest_only(bag):
    v = view_of(); v["evidence_refs"][0]["time_ns"] = None; v["evidence_refs"][0]["topic"] = None
    assert verify_view(v, bag)["refs"]["E1"]["status"] == "verified"


def test_VR_18_summary_counts_refs_and_artifacts(bag):
    r = verify_view(view_of(), bag)
    assert sum(r["summary"].values()) == len(r["refs"]) + len(r["artifacts"])


def test_VR_19_the_path_is_shown_cleaned(bag):
    assert verify_view(view_of(), bag)["path"] == str(bag)


def test_VR_20_exit_code_1_only_on_a_mismatch(bag, tmp_path):
    rep = tmp_path / "r.json"; rep.write_bytes(EXAMPLE_JSON.read_bytes())
    assert run_render(rep, "markdown", bag).exit_code == 0
    (bag / "bag_0.mcap").unlink()
    res = run_render(rep, "markdown", bag)
    assert res.exit_code == 0 and "not_checked" in res.text
    shutil.copy(EXAMPLE_BAG / "bag_0.mcap", bag / "bag_0.mcap"); f = bag / "bag_0.mcap"; b = bytearray(f.read_bytes()); b[10] ^= 1; f.write_bytes(bytes(b))
    res = run_render(rep, "markdown", bag)
    assert res.exit_code == 1 and "mismatch" in res.text


def test_VR_21_verification_results_appear_in_both_formats(bag, tmp_path):
    rep = tmp_path / "r.json"; rep.write_bytes(EXAMPLE_JSON.read_bytes())
    for fmt in ("markdown", "html"):
        t = run_render(rep, fmt, bag).text
        assert "3 verified, 0 mismatch, 0 not checked" in t and "verified" in t


def test_VR_22_a_report_with_many_references_to_one_artifact_scans_once_per_artifact(bag, monkeypatch):
    import physicore_collector.investigation_view.verify as V
    calls = []
    orig = V._scan
    monkeypatch.setattr(V, "_scan", lambda p, w: (calls.append(p), orig(p, w))[1])
    V.verify_view(view_of(), bag)
    assert len(calls) == 1
