import pytest

from physicore_collector.anomaly.exitcodes import decide_exit

# unit-level rows of the DESIGN 5.5 table (X-19); the CLI-level rows are added with the command in step 9


@pytest.mark.parametrize("kw,expected", [
    (dict(config_valid=True, inventory_code=0, finding_count=3, fail_on_findings=False), (0, [])),                                    # X-19a
    (dict(config_valid=True, inventory_code=0, finding_count=3, fail_on_findings=True), (1, ["findings_with_fail_on_findings"])),     # X-19b
    (dict(config_valid=True, inventory_code=0, finding_count=0, fail_on_findings=True), (0, [])),                                     # X-19c
    (dict(config_valid=False, inventory_code=None, finding_count=0, fail_on_findings=True), (3, ["invalid_config"])),                 # X-19d
    (dict(config_valid=True, inventory_code=0, finding_count=0, fail_on_findings=False), (0, [])),                                    # X-19e / X-21
    (dict(config_valid=True, inventory_code=2, finding_count=0, fail_on_findings=True), (2, ["evidence_not_inspectable"])),           # X-19f
    (dict(config_valid=True, inventory_code=1, finding_count=0, fail_on_findings=False), (1, ["evidence_integrity_or_completeness"])),  # X-19g
    (dict(config_valid=True, inventory_code=1, finding_count=2, fail_on_findings=True),
     (1, ["evidence_integrity_or_completeness", "findings_with_fail_on_findings"])),                                                  # X-19h
])
def test_X_19_exit_table(kw, expected):
    assert decide_exit(**kw) == expected


def test_invalid_config_wins_over_everything():
    assert decide_exit(config_valid=False, inventory_code=2, finding_count=9, fail_on_findings=True) == (3, ["invalid_config"])


def test_reasons_are_all_in_the_closed_vocabulary():
    from physicore_collector.anomaly.findings import EXIT_REASONS
    for inv in (None, 0, 1, 2):
        for flag in (False, True):
            for n in (0, 2):
                for ok in (True, False):
                    code, reasons = decide_exit(config_valid=ok, inventory_code=inv, finding_count=n, fail_on_findings=flag)
                    assert set(reasons) <= set(EXIT_REASONS) and (code == 0) == (not reasons)
