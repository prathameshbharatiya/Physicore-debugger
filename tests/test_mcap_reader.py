import io

import pytest
from mcap.writer import CompressionType

from physicore_collector.errors import InputError
from physicore_collector.mcap_reader import MCAP_MAGIC, scan_recording
from physicore_collector.schemas import ArtifactReadability as R
from tests import make_fixtures as mf


def scan_bytes(tmp_path, data, name="x.mcap"):
    p = tmp_path / name
    p.write_bytes(data)
    return scan_recording(p)


# ---- inspection of the shipped fixtures ----
def test_valid_recording(fx):
    r = scan_recording(fx / "valid.mcap")
    i = r.inspection
    assert i.readability is R.READABLE and i.errors == []
    assert i.total_message_count == 123
    assert {t.topic: t.message_count for t in i.topics} == {"/joint_states": 100, "/camera/image": 20, "/diagnostics": 3}
    assert i.recording_window.min_log_time_ns == mf.BASE_NS
    assert i.recording_window.max_log_time_ns == mf.BASE_NS + 2 * mf.NS
    assert i.integrity.end_magic_valid and i.integrity.footer_present and i.integrity.data_end_present
    assert i.integrity.statistics_message_count == 123
    assert i.metadata_records[0].values["operator"] == "alice"
    assert i.not_verified  # limits always stated
    assert i.file_name == "valid.mcap"


def test_per_channel_first_last_times(fx):
    t = {x.topic: x for x in scan_recording(fx / "valid.mcap").inspection.topics}
    assert t["/camera/image"].first_log_time_ns == mf.BASE_NS
    assert t["/camera/image"].last_log_time_ns == mf.BASE_NS + 1_900_000_000
    assert t["/joint_states"].schema_name == "synthetic/Sample"
    assert t["/joint_states"].message_encoding == "json"


def test_topics_sorted_deterministically(fx):
    names = [t.topic for t in scan_recording(fx / "valid.mcap").inspection.topics]
    assert names == sorted(names)


def test_truncated_is_partially_readable_with_recovered_data(fx):
    i = scan_recording(fx / "truncated.mcap").inspection
    assert i.readability is R.PARTIALLY_READABLE
    assert 0 < i.total_message_count < 123
    assert not i.integrity.footer_present and not i.integrity.end_magic_valid
    assert "truncated" in i.errors[0]
    assert i.recording_window.max_log_time_ns < mf.BASE_NS + 2 * mf.NS


def test_corrupted_chunk_is_partially_readable_via_crc(fx):
    i = scan_recording(fx / "corrupted.mcap").inspection
    assert i.readability is R.PARTIALLY_READABLE
    assert "crc validation failed" in i.errors[0]
    assert 0 < i.total_message_count < 123


def test_not_mcap_is_unreadable(fx):
    i = scan_recording(fx / "not_mcap.mcap").inspection
    assert i.readability is R.UNREADABLE
    assert "start magic" in i.errors[0]
    assert i.total_message_count == 0 and i.recording_window is None


def test_empty_file_is_unreadable(tmp_path):
    i = scan_bytes(tmp_path, b"").inspection
    assert i.readability is R.UNREADABLE and "empty" in i.errors[0]


def test_magic_only_is_unreadable_no_header(tmp_path):
    i = scan_bytes(tmp_path, MCAP_MAGIC).inspection
    assert i.readability is R.UNREADABLE
    assert i.integrity.start_magic_valid and not i.integrity.header_present


def test_unreadable_is_distinct_from_partially_readable(fx):
    assert scan_recording(fx / "not_mcap.mcap").readability is not scan_recording(fx / "truncated.mcap").readability


# ---- truncation / corruption guarantees (the claims made in the module docstring) ----
def test_every_proper_prefix_of_a_valid_file_is_never_readable_and_never_raises(tmp_path):
    data = mf.valid_bytes()
    p = tmp_path / "prefix.mcap"
    for n in range(len(data)):
        p.write_bytes(data[:n])
        r = scan_recording(p)
        assert r.readability is not R.READABLE, f"prefix of {n}/{len(data)} bytes reported readable"
        assert r.errors


def test_full_file_is_readable_but_one_extra_byte_is_not(tmp_path):
    data = mf.valid_bytes()
    assert scan_bytes(tmp_path, data).readability is R.READABLE
    r = scan_bytes(tmp_path, data + b"\x00", "extra.mcap")
    assert r.readability is R.PARTIALLY_READABLE
    assert r.inspection.integrity.trailing_bytes == 1 and not r.inspection.integrity.end_magic_valid
    assert "unexpected byte" in r.errors[0]


def test_data_section_corruption_is_detected(tmp_path):
    data = mf.valid_bytes()
    # flip bytes throughout the data section (first 85% of the file is data; the rest is summary)
    detected = 0
    offsets = range(0, int(len(data) * 0.85), 7)
    for off in offsets:
        b = bytearray(data)
        b[off] ^= 0xFF
        r = scan_bytes(tmp_path, bytes(b))
        detected += r.readability is not R.READABLE
    assert detected == len(offsets)


def test_known_limit_summary_section_flips_can_go_undetected(tmp_path):
    """Documents the honest limitation: summary contents are not cross-validated."""
    data = mf.valid_bytes()
    undetected = 0
    for off in range(int(len(data) * 0.9), len(data) - 20):
        b = bytearray(data)
        b[off] ^= 0xFF
        undetected += scan_bytes(tmp_path, bytes(b)).readability is R.READABLE
    assert undetected > 0


# ---- format variants ----
@pytest.mark.parametrize("comp", [CompressionType.NONE, CompressionType.ZSTD, CompressionType.LZ4])
def test_compression_variants_read_identically(tmp_path, comp):
    data = mf.build_mcap([mf.joint_states(), mf.camera()], metadata=mf.DEFAULT_METADATA, compression=comp)
    r = scan_bytes(tmp_path, data)
    assert r.readability is R.READABLE and r.inspection.total_message_count == 120


@pytest.mark.parametrize("comp", [CompressionType.ZSTD, CompressionType.LZ4])
def test_truncated_compressed_files_are_never_readable(tmp_path, comp):
    data = mf.build_mcap([mf.joint_states()], compression=comp)
    for n in range(0, len(data), 3):
        assert scan_bytes(tmp_path, data[:n]).readability is not R.READABLE


def test_unchunked_file(tmp_path):
    data = mf.build_mcap([mf.joint_states(), mf.diagnostics()], use_chunking=False)
    r = scan_bytes(tmp_path, data)
    assert r.readability is R.READABLE and r.inspection.total_message_count == 103


def test_valid_file_with_zero_messages(tmp_path):
    r = scan_bytes(tmp_path, mf.build_mcap([mf.StreamSpec("/empty", ())]))
    assert r.readability is R.READABLE
    assert r.inspection.total_message_count == 0 and r.inspection.recording_window is None
    assert r.inspection.topics[0].message_count == 0 and r.inspection.topics[0].first_log_time_ns is None


def test_same_topic_on_two_channels_is_merged_for_analysis(tmp_path):
    a = mf.StreamSpec("/t", (10, 30), schema_name="A")
    b = mf.StreamSpec("/t", (20, 40), schema_name="B")
    r = scan_bytes(tmp_path, mf.build_mcap([a, b]))
    assert len(r.inspection.topics) == 2
    assert r.message_count_for_topic("/t") == 4
    assert r.log_times_for_topic("/t") == [10, 20, 30, 40]


def test_window_uses_min_and_max_regardless_of_order(tmp_path):
    r = scan_bytes(tmp_path, mf.build_mcap([mf.StreamSpec("/a", (500, 100, 900)), mf.StreamSpec("/b", (50, 2000))]))
    w = r.inspection.recording_window
    assert (w.min_log_time_ns, w.max_log_time_ns) == (50, 2000)


def test_statistics_mismatch_is_reported(tmp_path, monkeypatch):
    import mcap.records as rec
    import physicore_collector.mcap_reader as mr
    real = mr.Statistics
    data = mf.valid_bytes()
    orig_iter = mr.StreamReader.records.fget

    def patched(self):
        for r in orig_iter(self):
            if isinstance(r, real):
                r = rec.Statistics(**{**r.__dict__, "message_count": r.message_count + 5})
            yield r

    monkeypatch.setattr(mr.StreamReader, "records", property(patched))
    r = scan_bytes(tmp_path, data)
    assert r.readability is R.PARTIALLY_READABLE
    assert any("Statistics record claims 128" in e for e in r.errors)


# ---- access errors ----
def test_missing_file_raises_input_error(tmp_path):
    with pytest.raises(InputError, match="not found"):
        scan_recording(tmp_path / "nope.mcap")


def test_directory_raises_input_error(tmp_path):
    with pytest.raises(InputError, match="not a regular file"):
        scan_recording(tmp_path)


def test_io_error_while_reading_is_an_input_error_not_a_format_finding(tmp_path, monkeypatch):
    import physicore_collector.mcap_reader as mr

    def boom(*a, **k):
        raise OSError(5, "Input/output error")

    p = tmp_path / "x.mcap"
    p.write_bytes(mf.valid_bytes())
    monkeypatch.setattr(mr, "_scan_stream", boom)
    with pytest.raises(InputError, match="I/O error"):
        scan_recording(p)


def test_internal_bugs_are_not_swallowed(tmp_path, monkeypatch):
    import physicore_collector.mcap_reader as mr

    def bug(self):
        raise TypeError("programming error")
        yield  # pragma: no cover

    monkeypatch.setattr(mr.StreamReader, "records", property(bug))
    p = tmp_path / "x.mcap"
    p.write_bytes(mf.valid_bytes())
    with pytest.raises(TypeError):
        scan_recording(p)


def test_regression_huge_length_prefix_does_not_crash_or_allocate(tmp_path):
    """A corrupted string length (here 4 GiB in the Header) once raised MemoryError."""
    b = bytearray(mf.valid_bytes())
    b[17:21] = b"\xff\xff\xff\xff"  # Header.profile string length prefix
    r = scan_bytes(tmp_path, bytes(b))
    assert r.readability is R.UNREADABLE
    assert "claims" in r.errors[0] or "unexpected end" in r.errors[0]
