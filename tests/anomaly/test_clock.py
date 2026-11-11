import pytest

from physicore_collector.anomaly.clock import LOG_TIME_DOMAIN, ClockDomainError, ClockedValue, payload_stamp_domain
from physicore_collector.investigator.observations import LOG_TIME_DOMAIN as ACCEPTED_LOG_DOMAIN


def test_log_domain_is_the_accepted_name():
    assert LOG_TIME_DOMAIN == ACCEPTED_LOG_DOMAIN == "mcap_log_time"


def test_payload_domain_name_matches_engine_naming():
    assert payload_stamp_domain("/scan", "header.stamp") == "payload_stamp:/scan:header.stamp"


def test_same_domain_relations_work():
    a, b = ClockedValue(5, LOG_TIME_DOMAIN), ClockedValue(9, LOG_TIME_DOMAIN)
    assert b.delta_ns(a) == 4 and a.before(b) and not b.before(a) and a.same_instant(ClockedValue(5, LOG_TIME_DOMAIN))


@pytest.mark.parametrize("op", ["delta_ns", "before", "same_instant"])
def test_STAMP_17_cross_domain_relation_raises(op):
    log = ClockedValue(5, LOG_TIME_DOMAIN)
    stamp = ClockedValue(5, payload_stamp_domain("/t", "header.stamp"))
    with pytest.raises(ClockDomainError):
        getattr(log, op)(stamp)
    with pytest.raises(ClockDomainError):
        getattr(stamp, op)(log)


def test_two_different_payload_domains_are_not_related():
    with pytest.raises(ClockDomainError):
        ClockedValue(1, payload_stamp_domain("/a", "header.stamp")).before(ClockedValue(2, payload_stamp_domain("/b", "header.stamp")))
    with pytest.raises(ClockDomainError):
        ClockedValue(1, payload_stamp_domain("/a", "header.stamp")).before(ClockedValue(2, payload_stamp_domain("/a", "other.stamp")))


def test_no_ordering_operators_and_exact_ints_only():
    a, b = ClockedValue(1, LOG_TIME_DOMAIN), ClockedValue(2, LOG_TIME_DOMAIN)
    with pytest.raises(TypeError):
        a < b  # noqa: B015
    for bad in (1.5, True, "3"):
        with pytest.raises(TypeError):
            ClockedValue(bad, LOG_TIME_DOMAIN)
    with pytest.raises(ValueError):
        ClockedValue(1, "")


def test_LATE_00_no_cross_domain_conversion_helpers_exist():
    import inspect
    from physicore_collector.anomaly import clock
    names = [n for n, o in inspect.getmembers(clock, inspect.isfunction)]
    assert not [n for n in names if any(w in n.lower() for w in ("convert", "offset", "translate", "to_log", "to_stamp"))]
