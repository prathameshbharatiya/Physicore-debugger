import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from physicore_collector.anomaly import findings as F
from physicore_collector.anomaly.clock import LOG_TIME_DOMAIN
from physicore_collector.anomaly.findings import (AnomalyReport, ClockedTime, SourceRef, derive_finding_id, find_forbidden, hash_parameters,
                                                  report_json)
from physicore_collector.anomaly.report import new_report
from tests.anomaly.helpers import SHA_A, SHA_B, SHA_C, make_finding


def _id(**over):
    kw = dict(detector_id="tierA.ros_log", detector_version="1.0.0", parameters_hash=hash_parameters({"p": 1}),
              contributing_artifacts=[("s0", SHA_A), ("s1", SHA_B)], topic="/rosout", field_path_template="level", group_key=["nav", 40, "x"],
              first_artifact_ref="s0", first_message_index=3)
    kw.update(over)
    return derive_finding_id(**kw)


def test_CS_01_unknown_field_rejected():
    d = make_finding().model_dump()
    d["surprise"] = 1
    with pytest.raises(ValidationError):
        F.Finding(**d)


def test_CS_02_absent_optionals_are_omitted_from_json():
    out = json.loads(report_json(new_report(tool_version="x", exit_code=0, exit_reasons=[], findings=[make_finding()])))
    f = out["findings"][0]
    for k in ("last_observed_at", "expected_value", "expected_source", "threshold", "field_path_template_x"):
        assert k not in f
    assert "config_hash" not in out and "config_problems" not in out
    assert "bit_info" not in f["observations"][0] and "identity_state" not in f["observations"][0]


def test_CS_03_finding_id_is_stable_and_hashseed_independent():
    assert _id() == _id() and _id().startswith("f-") and len(_id()) == 18
    code = ("from physicore_collector.anomaly.findings import derive_finding_id,hash_parameters;"
            "print(derive_finding_id(detector_id='tierA.ros_log',detector_version='1.0.0',parameters_hash=hash_parameters({'p':1}),"
            f"contributing_artifacts=[('s1','{SHA_B}'),('s0','{SHA_A}')],topic='/rosout',field_path_template='level',group_key=['nav',40,'x'],"
            "first_artifact_ref='s0',first_message_index=3))")
    outs = set()
    for seed in ("0", "1", "random"):
        outs.add(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed}, check=True).stdout.strip())
    assert outs == {_id()}


def test_CS_04_id_changes_with_version_and_parameters():
    base = _id()
    assert _id(detector_version="1.0.1") != base
    assert _id(parameters_hash=hash_parameters({"p": 2})) != base
    assert _id(group_key=["nav", 30, "x"]) != base and _id(topic="/other") != base and _id(first_message_index=4) != base


def test_CS_04f_id_independent_of_artifact_discovery_order():
    assert _id(contributing_artifacts=[("s1", SHA_B), ("s0", SHA_A)]) == _id(contributing_artifacts=[("s0", SHA_A), ("s1", SHA_B), ("s0", SHA_A)])


def test_CS_04g_different_contributing_artifact_sets_differ():
    assert _id(contributing_artifacts=[("s0", SHA_A)]) != _id(contributing_artifacts=[("s0", SHA_A), ("s1", SHA_B)])
    assert _id(contributing_artifacts=[("s0", SHA_A), ("s1", SHA_B)]) != _id(contributing_artifacts=[("s0", SHA_A), ("s1", SHA_C)])


def test_CS_04h_index_is_meaningful_only_with_its_artifact():
    assert _id(first_artifact_ref="s0") != _id(first_artifact_ref="s1")


def test_identity_refuses_empty_artifacts_and_floats():
    with pytest.raises(ValueError):
        _id(contributing_artifacts=[])
    with pytest.raises(TypeError):
        _id(group_key=[1.5])


def test_CS_05_duplicate_finding_ids_make_the_report_fail_loudly():
    f = make_finding()
    with pytest.raises(ValidationError, match="same finding_id"):
        new_report(tool_version="x", exit_code=0, exit_reasons=[], findings=[f, f])


def test_source_must_list_all_artifacts_sorted_unique():
    two = [SourceRef(artifact_ref="s1", sha256=SHA_B), SourceRef(artifact_ref="s0", sha256=SHA_A)]
    with pytest.raises(ValidationError, match="sorted"):
        make_finding(source=two)
    make_finding(source=sorted(two, key=lambda s: (s.artifact_ref, s.sha256)))
    with pytest.raises(ValidationError):
        make_finding(source=[], finding_id="f-" + "0" * 16)


def test_clock_domain_consistency_is_enforced():
    with pytest.raises(ValidationError):
        make_finding(first_observed_at=ClockedTime(value_ns=1, clock_domain="payload_stamp:/t:header.stamp"))
    with pytest.raises(ValidationError):
        make_finding(timestamp_basis="payload_stamp")
    make_finding(timestamp_basis="payload_stamp", clock_domain="payload_stamp:/rosout:header.stamp",
                 first_observed_at=ClockedTime(value_ns=1, clock_domain="payload_stamp:/rosout:header.stamp"))


@pytest.mark.parametrize("field,bad", [("severity", "fatal"), ("severity_basis", "calibrated"), ("anomaly_category", "late_message"),
                                       ("detector_id", "tierB.late_message"), ("detector_version", "1.0"), ("finding_id", "f-123"),
                                       ("occurrence_count", 0)])
def test_closed_vocabularies(field, bad):
    with pytest.raises(ValidationError):
        make_finding(**{field: bad})


def test_CS_07_no_confidence_or_score_field_in_any_model():
    import inspect
    models = [o for _, o in inspect.getmembers(F, inspect.isclass) if issubclass(o, F._Strict) and o is not F._Strict]
    assert models
    for m in models:
        names = set(m.model_fields)
        assert not {n for n in names if any(w in n.lower() for w in ("confidence", "score", "probab", "likelihood"))}, m


def test_explanation_must_be_non_causal():
    for bad in ("The node crashed", "This was caused by X", "because the driver", "root cause: x", "Stopped publishing at 5"):
        with pytest.raises(ValidationError):
            make_finding(explanation=bad)
    assert find_forbidden("causeway and decause") == []     # whole words only
    assert find_forbidden("Due to X") == ["due to"]


def test_uncertainty_vocabulary_is_closed():
    from physicore_collector.anomaly.findings import UncertaintyNote
    UncertaintyNote(code="identity_unknown")
    for gone in ("explicit_clock_equivalence_asserted_by_user", "stamp_type_unverified", "made_up"):
        with pytest.raises(ValidationError):
            UncertaintyNote(code=gone)


def test_strict_types_no_coercion():
    with pytest.raises(ValidationError):
        make_finding(occurrence_count="1")
    with pytest.raises(ValidationError):
        make_finding(observed_value=1.5)


def test_report_json_is_deterministic_and_newline_terminated():
    r = new_report(tool_version="x", exit_code=0, exit_reasons=[], findings=[make_finding()])
    a, b = report_json(r), report_json(r)
    assert a == b and a.endswith("}\n")
    assert json.loads(a)["findings"][0]["finding_id"] == r.findings[0].finding_id


def test_report_json_keys_are_sorted_at_every_level():
    r = new_report(tool_version="x", exit_code=0, exit_reasons=[], findings=[make_finding()])

    def check(pairs):
        keys = [k for k, _ in pairs]
        assert keys == sorted(keys), keys
        return dict(pairs)
    json.loads(report_json(r), object_pairs_hook=check)
