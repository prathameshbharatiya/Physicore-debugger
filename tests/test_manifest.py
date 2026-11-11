import json

import pytest

from physicore_collector.manifest import LOSS_STATEMENT, build_known_gaps, build_loss_coverage
from physicore_collector.schemas import SessionStatus as S
from physicore_collector.session import analyze_session, session_json


def ctx_for(fx, profile, name="valid.mcap", **kw):
    return analyze_session(fx / name, profile, **kw)


def codes(ctx):
    return {g.code for g in build_known_gaps(ctx)}


def test_known_gaps_always_state_the_limits_of_the_evidence(fx, synth_profile):
    c = codes(ctx_for(fx, synth_profile))
    assert {"no_sequence_or_dds_loss_analysis", "cadence_is_timing_evidence_only",
            "cadence_edges_not_evaluated", "summary_section_not_verified"} <= c


def test_loss_coverage_never_claims_no_loss_even_for_a_complete_session(fx, synth_profile):
    ctx = ctx_for(fx, synth_profile)
    assert ctx.report.status is S.COMPLETE
    cov = build_loss_coverage(ctx)
    assert cov.no_loss_claim_permitted is False
    assert cov.dds_level_loss_detection == "not_performed" and cov.sequence_counter_analysis == "not_performed"
    assert cov.statement == LOSS_STATEMENT and "NOT evidence that no data was lost" in cov.statement


def test_coverage_lists_checked_and_unchecked_topics(fx, synth_profile):
    cov = build_loss_coverage(ctx_for(fx, synth_profile))
    assert cov.cadence_checked_topics == ["/camera/image", "/joint_states"]
    assert cov.topics_without_cadence_rules == ["/diagnostics"]


def test_unrecorded_topic_not_in_checked_list(fx, synth_profile):
    cov = build_loss_coverage(ctx_for(fx, synth_profile, "missing_topic.mcap"))
    assert cov.cadence_checked_topics == ["/joint_states"]


def test_log_time_basis_gap_depends_on_profile(fx, synth_profile, example_profile):
    assert "log_time_basis_declared_not_verified" in codes(ctx_for(fx, synth_profile))
    assert "log_time_basis_unknown" in codes(ctx_for(fx, example_profile))


def test_truncated_recording_adds_incomplete_gap_with_first_error(fx, synth_profile):
    g = {g.code: g for g in build_known_gaps(ctx_for(fx, synth_profile, "truncated.mcap"))}
    assert "recording_incomplete" in g and "truncated" in g["recording_incomplete"].description


def test_trigger_gap_added_only_when_comparison_not_established(fx, synth_profile):
    t = fx / "triggers"
    assert "trigger_comparison_not_established" in codes(ctx_for(fx, synth_profile, trigger_path=t / "trigger_unknown_basis.json"))
    assert "trigger_comparison_not_established" not in codes(ctx_for(fx, synth_profile, trigger_path=t / "trigger_attested_within.json"))
    assert "trigger_comparison_not_established" not in codes(ctx_for(fx, synth_profile))


def test_inconclusive_cadence_is_listed_as_a_gap(tmp_path, synth_profile):
    from tests import make_fixtures as mf
    p = tmp_path / "x.mcap"
    p.write_bytes(mf.build_mcap([mf.StreamSpec("/joint_states", (mf.BASE_NS,)), mf.camera()], metadata=mf.DEFAULT_METADATA))
    g = {g.code: g for g in build_known_gaps(analyze_session(p, synth_profile))}
    assert "/joint_states" in g["cadence_check_not_completed"].description


def test_session_json_shape_and_honesty(fx, synth_profile):
    j = session_json(ctx_for(fx, synth_profile, "cadence_gap.mcap"))
    json.dumps(j)  # serialisable
    assert j["validation"]["status"] == "partial"
    assert j["loss_detection_coverage"]["no_loss_claim_permitted"] is False
    assert j["recording"]["file_name"] == "cadence_gap.mcap" and len(j["recording"]["sha256"]) == 64
    assert j["inspection"]["recording_window"]["min_log_time_ns"] > 0
    assert j["known_gaps"]
    assert "/" not in j["recording"]["file_name"]  # no absolute paths leak
