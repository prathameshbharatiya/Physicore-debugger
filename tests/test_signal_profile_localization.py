from physicore_collector.localization.symptoms import from_signal_profile


def test_profile_event_becomes_hypothesis_visible_observed_symptom():
    report = {
        "evidence_complete": True,
        "incomplete_reasons": [],
        "events": [{
            "event_id": "sp-000001", "rule": "stuck", "start_ns": 10, "end_ns": 30,
            "clock_domain": "mcap_log_time", "persistent": True,
            "source_refs": [
                {"artifact_ref": "artifact-1", "sha256": "a" * 64, "topic": "/joint_states",
                 "message_index": 4, "time_ns": 10},
                {"artifact_ref": "artifact-1", "sha256": "a" * 64, "topic": "/joint_states",
                 "message_index": 5, "time_ns": 30},
            ],
        }],
    }
    normalized = from_signal_profile(report)
    assert len(normalized.symptoms) == 1
    symptom = normalized.symptoms[0]
    assert symptom.source == "signal_profile_event"
    assert symptom.category == "signal_stuck"
    assert symptom.topic == "/joint_states"
    assert symptom.onset.domain == "mcap_log_time"
    assert len(symptom.evidence) == 2
    assert symptom.source_coverage.topic_coverage == "complete"
