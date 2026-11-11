"""Exact status rules. Each test maps to a numbered rule in status.py."""
import pytest

from physicore_collector.schemas import (
    ArtifactReadability as R, CheckCategory, CheckOutcome as O, CheckResult, SessionStatus as S,
)
from physicore_collector.status import derive_status


def c(outcome, required=True, cid="c"):
    return CheckResult(check_id=cid, category=CheckCategory.OTHER, outcome=outcome,
                       required=required, message="m")


def status(readability=R.READABLE, checks=(), profile_valid=True):
    return derive_status(profile_valid=profile_valid, readability=readability, checks=list(checks))[0]


def test_rule1_invalid_profile_is_failed_even_if_everything_else_passes():
    assert status(checks=[c(O.PASS)], profile_valid=False) is S.FAILED


def test_rule2_unreadable_is_failed_never_complete_even_if_checks_pass():
    assert status(R.UNREADABLE, [c(O.PASS)]) is S.FAILED


def test_rule3_partially_readable_is_partial_never_complete():
    assert status(R.PARTIALLY_READABLE, [c(O.PASS)]) is S.PARTIAL


def test_rule4_no_checks_is_partial():
    s, reasons = derive_status(profile_valid=True, readability=R.READABLE, checks=[])
    assert s is S.PARTIAL and "no validation checks" in reasons[0]


def test_rule5_required_fail_is_partial():
    assert status(checks=[c(O.PASS), c(O.FAIL, cid="x")]) is S.PARTIAL


def test_rule6_required_inconclusive_is_partial():
    assert status(checks=[c(O.PASS), c(O.INCONCLUSIVE, cid="x")]) is S.PARTIAL


def test_rule7_all_required_pass_is_complete():
    assert status(checks=[c(O.PASS), c(O.NOT_APPLICABLE, cid="y")]) is S.COMPLETE


def test_nonrequired_problems_do_not_block_complete():
    assert status(checks=[c(O.PASS), c(O.FAIL, required=False, cid="w"),
                          c(O.INCONCLUSIVE, required=False, cid="v")]) is S.COMPLETE


def test_reasons_name_the_offending_checks():
    _, reasons = derive_status(profile_valid=True, readability=R.READABLE,
                               checks=[c(O.FAIL, cid="topic:/a"), c(O.INCONCLUSIVE, cid="trigger")])
    joined = " ".join(reasons)
    assert "topic:/a" in joined and "trigger" in joined


@pytest.mark.parametrize("readability", list(R))
@pytest.mark.parametrize("outcome", list(O))
@pytest.mark.parametrize("profile_valid", [True, False])
def test_complete_is_never_returned_unless_all_conditions_hold(readability, outcome, profile_valid):
    s = status(readability, [c(outcome)], profile_valid)
    if s is S.COMPLETE:
        assert profile_valid and readability is R.READABLE and outcome in (O.PASS, O.NOT_APPLICABLE)


def test_derived_status_is_always_accepted_by_report_schema():
    from physicore_collector.schemas import ValidationReport
    for readability in R:
        for outcome in O:
            checks = [c(outcome)]
            s, reasons = derive_status(profile_valid=True, readability=readability, checks=checks)
            ValidationReport(status=s, status_reasons=reasons, artifact_readability=readability, checks=checks)
