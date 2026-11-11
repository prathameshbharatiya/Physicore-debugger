"""X-12 (unit part): the Tier B timing store costs 16 bytes per message (DESIGN 7.2). The end-to-end RSS measurement is a benchmark (step 10)."""
import tracemalloc

from physicore_collector.anomaly.detectors.timing import TopicTimes


class StubView:
    artifact_ref, sha256, order = "artifact:x", "0" * 64, 0

    def __init__(self, i):
        self.log_time_ns, self.message_index = 1_700_000_000_000_000_000 + i * 1000, i


def test_sixteen_bytes_per_message():
    tt = TopicTimes()
    assert tt.t.itemsize + tt.k.itemsize == 16
    n = 200_000
    views = [StubView(i) for i in range(1)]
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    v = StubView(0)
    for i in range(n):
        v.log_time_ns, v.message_index = 1_700_000_000_000_000_000 + i * 1000, i
        tt.add(v)
    used = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    assert len(tt.t) == n and used / n <= 20, used / n          # array over-allocation allowance, no per-message Python objects


def test_packed_key_round_trips():
    tt = TopicTimes()
    v = StubView(0)
    v.order, v.message_index = 7, 123456
    tt.add(v)
    o = tt.occ(0, "/t")
    assert (o.order, o.index) == (7, 123456)
