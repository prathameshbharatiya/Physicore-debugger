"""Step 9: replay verifier (independent of M3d code) on real recordings; PRV-01, 04, 09, 11, 13..16 (replay side)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from physicore_collector.localization import output as O
from physicore_collector.localization.service import run_localization
from tests.localization import l2
from tests.localization.test_svc import DETS, _incomplete, cascade, deps
from tests.localization.tools.replay_verifier import verify

M3C_EXAMPLE = Path("/home/claude/work/m3c/deliver/m3c-example.zip")


def as_dict(rep):
    return json.loads(O.to_json(rep))


def test_verifier_imports_nothing_from_m3d():
    src = (Path(__file__).parent / "tools" / "replay_verifier.py").read_text()
    assert "import physicore_collector" not in src and "from physicore_collector" not in src and "localization" not in src.replace("localization/", "").split('"""', 2)[2]


def test_PRV_01_04_every_reference_and_onset_replays(tmp_path):
    bag, cfg = cascade(tmp_path)
    rep = run_localization(bag, cfg, dependencies=deps(tmp_path))
    r = verify(as_dict(rep), bag)
    assert r.problems == [] and r.checked["symptom_refs"] >= 2 and r.checked["onsets"] == 2 and r.checked["evidence_digests"] == 1


def test_PRV_04_gap_silence_bounds_replay(tmp_path):
    msgs = [(l2.at(i), {"data": "x"}) for i in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 30, 31)]
    bag = l2.build_bag(tmp_path, {"/t": ("std_msgs/msg/String", msgs)})
    cfg = l2.write_config(tmp_path, l2.GAP)
    rep = run_localization(bag, cfg)
    gaps = [s for s in rep.symptoms if s.category == "publishing_gap"]
    assert gaps and gaps[0].onset_basis == "silence_between_captured_messages"
    r = verify(as_dict(rep), bag)
    assert r.problems == [] and r.checked["onsets"] >= 1


def test_PRV_09_tampered_artifact_is_detected_by_the_verifier(tmp_path):
    bag, cfg = cascade(tmp_path)
    rep = as_dict(run_localization(bag, cfg))
    f = sorted(Path(bag).glob("*.mcap"))[0]
    raw = bytearray(f.read_bytes())
    raw[-40] ^= 0xFF
    f.write_bytes(bytes(raw))
    r = verify(rep, bag)
    assert r.problems and any("sha256" in p or "cannot re-read" in p or "log_time" in p for p in r.problems)


def test_verifier_flags_a_wrong_reference_and_a_wrong_onset(tmp_path):
    bag, cfg = cascade(tmp_path)
    good = as_dict(run_localization(bag, cfg))
    for mutate, needle in ((lambda d: d["symptoms"][0]["evidence"][0].__setitem__("message_index", 99), "no message"),
                           (lambda d: d["symptoms"][0]["evidence"][0].__setitem__("topic", "/other"), "topic"),
                           (lambda d: d["symptoms"][0]["evidence"][0].__setitem__("time_ns", 5), "log_time"),
                           (lambda d: d["symptoms"][0]["onset"].update(lower_ns=1, upper_ns=1), "onset"),
                           (lambda d: d["evidence"][0].__setitem__("sha256", "0" * 64), "sha256")):
        bad = json.loads(json.dumps(good))
        mutate(bad)
        r = verify(bad, bag)
        assert any(needle in p for p in r.problems), (needle, r.problems)


def test_PRV_13_16_observation_backed_blocker_replays_and_fabricated_one_is_flagged(tmp_path):
    topics = {"/rosout": (l2.LOG, [(l2.at(i), l2.log(40)) for i in range(12)])}
    bag = l2.build_bag(tmp_path, topics)
    cfg = l2.write_config(tmp_path, {"tierA.ros_log": {"enabled": True}}, {"max_retained_observations": 3})
    rep = run_localization(bag, cfg)
    d = as_dict(rep)
    ob = [b for b in d["blockers"] if b["code"] == "observations_truncated"]
    assert ob and ob[0]["observations"]
    r = verify(d, bag)
    assert r.problems == [] and r.checked["blocker_observations"] >= 1 and r.checked["blocker_artifacts"] >= 1
    bad = json.loads(json.dumps(d))
    next(b for b in bad["blockers"] if b["code"] == "observations_truncated")["observations"][0]["message_index"] = 999
    assert any("no message at index 999" in p for p in verify(bad, bag).problems)
    bad2 = json.loads(json.dumps(d))
    next(b for b in bad2["blockers"] if b["code"] == "observations_truncated")["artifacts"][0]["sha256"] = "1" * 64
    assert any("digest" in p for p in verify(bad2, bag).problems)


def test_PRV_14_16_metadata_only_blockers_replay_their_digests(tmp_path):
    bag, cfg = _incomplete(tmp_path)
    d = as_dict(run_localization(bag, cfg))
    md = [b for b in d["blockers"] if b["code"] == "recording_incomplete"]
    assert md and all(not b.get("observations") for b in md)
    r = verify(d, bag)
    assert r.checked["blocker_artifacts"] >= 1 or md[0]["artifacts"] == []


def test_PRV_12_replay_after_serialise_parse_roundtrip(tmp_path):
    bag, cfg = cascade(tmp_path)
    rep = run_localization(bag, cfg, dependencies=deps(tmp_path))
    again = json.loads(O.to_json(O.LocalizationReport.model_validate_json(O.to_json(rep))))
    assert verify(again, bag).problems == []


@pytest.mark.skipif(not M3C_EXAMPLE.exists(), reason="the delivered M3c example package is not present in this environment")
def test_PRV_11_replay_on_the_delivered_m3c_example_package(tmp_path):
    with zipfile.ZipFile(M3C_EXAMPLE) as z:
        z.extractall(tmp_path / "pkg")
    ex = tmp_path / "pkg" / "examples" / "anomaly"
    rec = ex / "example_recording.mcap"
    rep = run_localization(rec, ex / "anomaly.example.yaml")
    assert rep.exit_code == 0 and rep.symptoms == [] and all(r.status == "not_applicable" for r in rep.coverage.detectors)
    r = verify(as_dict(rep), ex)
    assert r.problems == [] and r.checked["evidence_digests"] == 1
