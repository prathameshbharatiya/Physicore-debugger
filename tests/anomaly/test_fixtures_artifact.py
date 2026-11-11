"""CS-04z: the FX-ART alternate artifacts differ from the originals ONLY in byte identity (TEST_PLAN 1)."""
import hashlib

import pytest

from tests.anomaly.fixtures import ALT_CHUNK, DEFAULT_CHUNK, file_digests, fingerprint, load, log_stream, segment_digests, write_bag

TOPIC = "/rosout"


def _two_segments():
    return [{TOPIC: log_stream(6, tag="a")}, {TOPIC: log_stream(6, start=10_000_000_000_000 + 1_700_000_000_000_000_000, tag="b")}]


@pytest.mark.parametrize("alt", [{0}, {1}, {0, 1}])
def test_CS_04z_alternate_differs_only_in_bytes(tmp_path, alt):
    segs = _two_segments()
    orig = write_bag(tmp_path / "orig", segs)
    other = write_bag(tmp_path / "other", segs, alternate=alt)
    inv_o, ev_o = load(orig, {TOPIC})
    inv_a, ev_a = load(other, {TOPIC})
    od, ad = file_digests(orig), file_digests(other)
    names = sorted(od)
    assert len(names) == 2
    for i, name in enumerate(names):
        n2 = name.replace("orig", "other")
        if i in alt:
            assert od[name] != ad[n2], "alternate must have genuinely different bytes"
            assert (orig / name).read_bytes() != (other / n2).read_bytes()
        else:
            assert od[name] == ad[n2]
    # digests the loader reports are the real sha256 of the files, recomputed from bytes
    for ev, bag in ((ev_o, orig), (ev_a, other)):
        for ref, sha in segment_digests(ev, TOPIC).items():
            assert sha in file_digests(bag).values()
    assert set(segment_digests(ev_o, TOPIC).values()) != set(segment_digests(ev_a, TOPIC).values())
    # evidence is intact and complete on both sides; messages are identical
    assert ev_o.complete and ev_a.complete and inv_o.inspection.state == inv_a.inspection.state == "complete"
    assert fingerprint(ev_o, TOPIC) == fingerprint(ev_a, TOPIC)
    assert len(fingerprint(ev_o, TOPIC)) == 12


def test_alternate_chunk_sizes_really_differ():
    assert ALT_CHUNK != DEFAULT_CHUNK


def test_integrity_is_not_relaxed_a_changed_byte_after_inventory_is_not_read(tmp_path):
    """The accepted loader binds bytes to the digest the inventory carries: bytes changed after inventorying are not read."""
    from physicore_collector.inventory import inspect_evidence
    from physicore_collector.investigator.observations import load_evidence
    bag = write_bag(tmp_path / "b", _two_segments())
    inv = inspect_evidence(bag)
    assert load_evidence(inv, {TOPIC}).complete
    seg = next(bag.glob("*_0.mcap"))
    seg.write_bytes(seg.read_bytes() + b"\x00")   # a real byte change, no metadata edited
    ev = load_evidence(inv, {TOPIC})
    assert not ev.complete
    refs = {m.artifact_ref for m in ev.messages.get(TOPIC, [])}
    assert len(refs) == 1          # only the untouched segment was read
