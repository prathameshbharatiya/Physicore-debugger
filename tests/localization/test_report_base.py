"""RPT-02..05 on the Step-1 primitives, WRD-01/02 on synthetic strings, statement-model rules (PR-03). Record-level RPT tests are repeated in Step 8."""
import json

import pytest
from pydantic import ValidationError

from physicore_collector.localization import report as R
from physicore_collector.localization import vocabulary as V

H = "a" * 64


def test_RPT_02_unknown_field_rejected_on_each_base_model():
    with pytest.raises(ValidationError):
        R.ObservedStatement(source_kind="m3c_finding", source_id="f-1", extra=1)
    with pytest.raises(ValidationError):
        R.IntervalModel(domain="d", lower_ns=1, upper_ns=2, extra=1)
    with pytest.raises(ValidationError):
        R.DeclaredStatement(dependencies_sha256=H, semantic_hash=H, edge_id="e1", declared_by="me", extra=1)
    with pytest.raises(ValidationError):
        R.DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=R.IntervalModel(domain="d", lower_ns=1, upper_ns=1),
                                later=R.IntervalModel(domain="d", lower_ns=2, upper_ns=2), relation="before", extra=1)


def test_statement_kinds_closed():
    assert V.STATEMENT_KINDS == ("observed", "derived_order", "declared")
    with pytest.raises(ValidationError):
        R.ObservedStatement(kind="inferred", source_kind="m3c_finding", source_id="f")


def test_PRV_07_wrong_source_kind_rejected():
    with pytest.raises(ValidationError):
        R.ObservedStatement(source_kind="declared", source_id="x")


def test_derived_order_checks_its_own_relation():
    a, b = R.IntervalModel(domain="d", lower_ns=1, upper_ns=3), R.IntervalModel(domain="d", lower_ns=3, upper_ns=5)
    with pytest.raises(ValidationError):
        R.DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=a, later=b, relation="before")   # touching is not before
    R.DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=a, later=b, relation="unordered")
    c = R.IntervalModel(domain="d", lower_ns=4, upper_ns=5)
    R.DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=a, later=c, relation="before")
    with pytest.raises(ValidationError):
        R.DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=a, later=c, relation="unordered")


def test_derived_order_rejects_cross_domain_and_unknown_rule():
    a, b = R.IntervalModel(domain="d1", lower_ns=1, upper_ns=1), R.IntervalModel(domain="d2", lower_ns=5, upper_ns=5)
    with pytest.raises(ValidationError):
        R.DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=a, later=b, relation="before")
    with pytest.raises(ValidationError):
        R.DerivedOrderStatement(rule_id="made_up_rule", earlier=a, later=a, relation="unordered")


def test_declared_statement_requires_digests_and_names():
    R.DeclaredStatement(dependencies_sha256=H, semantic_hash=H, edge_id="e1", declared_by="team")
    for kw in ({"dependencies_sha256": "xyz"}, {"semantic_hash": H.upper()}, {"edge_id": " "}, {"declared_by": ""}):
        base = dict(dependencies_sha256=H, semantic_hash=H, edge_id="e1", declared_by="team")
        base.update(kw)
        with pytest.raises(ValidationError):
            R.DeclaredStatement(**base)


def test_interval_model_rejects_bad_bounds():
    with pytest.raises(ValidationError):
        R.IntervalModel(domain="d", lower_ns=5, upper_ns=4)
    with pytest.raises(ValidationError):
        R.IntervalModel(domain="d", lower_ns=1.5, upper_ns=2)
    with pytest.raises(ValidationError):
        R.IntervalModel(domain="", lower_ns=1, upper_ns=2)


def test_RPT_04_floats_found_and_refused():
    assert R.find_floats({"a": [1, {"b": 2.5}]}) == ["$.a[1].b"]
    with pytest.raises(TypeError):
        R.canonical_json({"a": 1.0})
    assert R.find_floats({"a": 1, "b": [True, "x", None]}) == []


def test_RPT_05_canonical_json_sorted_and_stable():
    a = R.canonical_json({"b": 1, "a": {"d": 2, "c": [3, 1]}})
    assert a == '{"a":{"c":[3,1],"d":2},"b":1}'
    m = R.IntervalModel(domain="d", lower_ns=1, upper_ns=2)
    out = R.report_json(m)
    assert out.endswith("\n") and json.loads(out) == {"domain": "d", "lower_ns": 1, "upper_ns": 2}
    assert R.report_json(m) == out


def test_RPT_03_optional_absent_omitted():
    class M(R.Strict):
        a: int
        b: int | None = None
    assert json.loads(R.report_json(M(a=1))) == {"a": 1}


def test_walk_is_iterative_on_deep_structures():
    deep = cur = {}
    for _ in range(20000):
        cur["k"] = {}
        cur = cur["k"]
    cur["v"] = 1.5
    assert len(R.find_floats(deep)) == 1


@pytest.mark.parametrize("key", ["confidence", "probability", "score", "rank", "root_cause", "likelihood", "weight",
                                 "confidence_pct", "link_score", "rank_order", "root_cause_candidate"])
def test_UNC_06_forbidden_keys_detected(key):
    assert R.find_forbidden_keys({"x": {key: 1}}) == [f"$.x.{key}"]


@pytest.mark.parametrize("key", ["symptom_id", "weightless", "crankshaft", "onset_interval_width", "lower_ns", "ranked"])
def test_forbidden_keys_not_overmatching(key):
    assert R.find_forbidden_keys({key: 1}) == []


def test_forbidden_key_word_level():
    assert V.forbidden_key_words("ranked") == [] and V.forbidden_key_words("rank") == ["rank"]


TERMS = ["root cause", "origin", "originated", "triggered", "culprit", "responsible", "blame", "fault in", "explains", "therefore", "cause",
         "because", "due to", "led to", "likelihood", "most likely", "failed because"]


@pytest.mark.parametrize("term", TERMS)
def test_WRD_01_forbidden_terms_detected_case_insensitive(term):
    assert V.find_forbidden(f"The {term.upper()} here") == [term.lower()] or term.lower() in V.find_forbidden(f"The {term.upper()} here")
    assert term.lower() in V.find_forbidden(f"a {term} b")


@pytest.mark.parametrize("ok", ["originate", "originalities", "irresponsible", "blameless", "caused by" if False else "causal-free", "result"])
def test_WRD_01_word_boundaries(ok):
    assert V.find_forbidden(ok) == []


def test_WRD_02_propagation_only_inside_the_label_token():
    assert V.find_forbidden("label consistent_with_propagation here") == []
    assert V.find_forbidden("the propagation of a fault") == ["propagation"]
    assert V.find_forbidden("consistent_with_propagation and propagation") == ["propagation"]
    assert V.find_forbidden("Propagating") == ["propagation"]


def test_forbidden_terms_include_all_m3c_terms():
    from physicore_collector.anomaly.findings import FORBIDDEN_TERMS as M3C
    assert set(M3C) <= set(V.FORBIDDEN_TERMS)


def test_fixed_sentences_pass_their_own_wording_check():
    for s in (V.SCOPE_SENTENCE, V.NOT_COMPARED_ACROSS_LANES, V.SEVERITY_DISCLAIMER, *V.DISCRIMINATING_EVIDENCE):
        assert V.find_forbidden(s) == [], s


def test_vocabularies_are_closed_tuples_without_duplicates():
    for name in ("STATEMENT_KINDS", "LINK_LABELS", "DEFENCE_STATES", "BLOCKER_CODES", "CAVEAT_CODES", "HYPOTHESIS_TEMPLATES", "HYPOTHESIS_STATUSES",
                 "ONSET_BASES", "M3D_UNCERTAINTY", "DISCRIMINATING_EVIDENCE"):
        t = getattr(V, name)
        assert isinstance(t, tuple) and len(t) == len(set(t)), name
    assert len(V.HYPOTHESIS_TEMPLATES) == 3                      # D10
    assert set(V.BLOCKER_CODES).isdisjoint(V.CAVEAT_CODES)
    assert V.DEFAULT_LIMITS["path_depth"] == 32 and V.DEFAULT_LIMITS["chains_reported"] == 1000 and V.DEFAULT_LIMITS["paths_per_pair"] == 10000
    assert V.DEFAULT_LIMITS["symptoms"] == 100000 and V.DEFAULT_LIMITS["links"] == 200000 and V.DEFAULT_LIMITS["dependency_edges"] == 5000


def test_derived_order_relation_vocabulary_closed():
    a, b = R.IntervalModel(domain="d", lower_ns=1, upper_ns=1), R.IntervalModel(domain="d", lower_ns=5, upper_ns=5)
    with pytest.raises(ValidationError):
        R.DerivedOrderStatement(rule_id="interval_order.strict_precedence", earlier=a, later=b, relation="after")
