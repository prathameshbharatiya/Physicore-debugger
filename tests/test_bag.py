"""rosbag2 bag-directory support: discovery, metadata cross-checks, split recordings, failures."""

import os
import tracemalloc

import pytest
import yaml

from physicore_collector.bag import scan_bag
from physicore_collector.bag_metadata import parse_bag_metadata
from physicore_collector.errors import InputError
from physicore_collector.schemas import ArtifactReadability, CheckOutcome, SegmentState, SessionStatus
from physicore_collector.session import analyze_session, session_json
from tests import make_fixtures as mf
from tests.bagtools import (
    metadata_dict, standard_streams, three_way_split, write_bag,
)


def check(ctx, cid):
    return next(c for c in ctx.report.checks if c.check_id == cid)


def states(ctx):
    return {s.relative_path: s.state for s in ctx.bag.info.segments}


# ---------------------------------------------------------------- the happy paths


def test_standalone_mcap_is_unchanged_and_has_no_bag(fx, synth_profile):
    ctx = analyze_session(fx / "valid.mcap", synth_profile)
    assert ctx.bag is None and ctx.recording_kind == "mcap_file"
    assert ctx.report.status is SessionStatus.COMPLETE
    assert ctx.report.report_schema_version == "0.1"
    assert not any(c.check_id.startswith(("bag:", "capture:")) for c in ctx.report.checks)
    assert "bag" not in session_json(ctx) and "recording_kind" not in session_json(ctx)


def test_bag_with_one_mcap_is_complete_and_matches_standalone(tmp_path, fx, synth_profile):
    bag = write_bag(tmp_path / "bag1")
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.COMPLETE, [c.message for c in ctx.report.checks if c.outcome is not CheckOutcome.PASS]
    assert ctx.recording_kind == "rosbag2_directory"
    assert ctx.report.report_schema_version == "0.2"
    solo = analyze_session(fx / "valid.mcap", synth_profile)
    # same downstream evidence whichever input type it came from
    strip = lambda topics: [t.model_dump(exclude={"channel_id"}) for t in topics]  # ids are renumbered when merging
    assert strip(ctx.scan.inspection.topics) == strip(solo.scan.inspection.topics)
    assert ctx.scan.inspection.recording_window == solo.scan.inspection.recording_window
    assert [c.check_id for c in ctx.report.checks if not c.check_id.startswith("bag:")] == [c.check_id for c in solo.report.checks]
    assert all(c.outcome is CheckOutcome.PASS for c in ctx.report.checks if c.check_id.startswith("bag:"))


def test_split_bag_is_accounted_for_and_cadence_spans_boundaries(tmp_path, fx, synth_profile):
    bag = write_bag(tmp_path / "split", three_way_split())
    ctx = analyze_session(bag, synth_profile)
    assert [s.relative_path for s in ctx.bag.info.segments] == ["split_0.mcap", "split_1.mcap", "split_2.mcap"]
    assert ctx.bag.info.segment_order_basis == "metadata"
    assert all(s.state is SegmentState.READABLE for s in ctx.bag.info.segments)
    solo = analyze_session(fx / "valid.mcap", synth_profile)
    assert ctx.scan.inspection.total_message_count == solo.scan.inspection.total_message_count == 100 + 20 + 3
    assert ctx.report.status is SessionStatus.COMPLETE
    assert sum(s.observed_message_count for s in ctx.bag.info.segments) == 123
    assert len(ctx.bag.files) == 4  # 3 segments + metadata.yaml, each hashed


def test_gap_straddling_a_segment_boundary_is_still_seen(tmp_path, synth_profile):
    b = mf.BASE_NS
    js = mf.joint_states(skip=range(25, 50))  # 0.52 s hole at 0.48..1.00 s
    segs = __import__("tests.bagtools", fromlist=["split_by_time"]).split_by_time([js, mf.camera(), mf.diagnostics()], [b + 700_000_000])
    ctx = analyze_session(write_bag(tmp_path / "gap", segs), synth_profile)
    assert check(ctx, "cadence:/joint_states").outcome is CheckOutcome.FAIL
    assert ctx.report.status is SessionStatus.PARTIAL


def test_original_files_are_never_modified(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "orig", three_way_split())
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in bag.iterdir()}
    analyze_session(bag, synth_profile)
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in bag.iterdir()}
    assert before == after


# ---------------------------------------------------------------- metadata problems


def test_missing_metadata_is_partial_never_complete(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "nomd", three_way_split(), with_metadata=False)
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.PARTIAL
    assert ctx.report.artifact_readability is ArtifactReadability.PARTIALLY_READABLE
    assert check(ctx, "bag:metadata").outcome is CheckOutcome.FAIL
    assert "interrupted" in check(ctx, "bag:metadata").message and "does not prove" in check(ctx, "bag:metadata").message
    assert check(ctx, "bag:segments").outcome is CheckOutcome.INCONCLUSIVE  # completeness not establishable
    assert check(ctx, "bag:message_counts").outcome is CheckOutcome.NOT_APPLICABLE
    assert ctx.bag.info.segment_order_basis == "inferred_from_observed_log_time"
    # still safely inspectable
    assert ctx.scan.inspection.total_message_count == 123
    assert not ctx.bag.info.metadata_present


def test_no_metadata_order_comes_from_observed_time_not_file_names(tmp_path):
    bag = write_bag(tmp_path / "scrambled", three_way_split(), with_metadata=False)
    # make the NAME order the reverse of the TIME order
    os.rename(bag / "scrambled_0.mcap", bag / "c.mcap")
    os.rename(bag / "scrambled_1.mcap", bag / "b.mcap")
    os.rename(bag / "scrambled_2.mcap", bag / "a.mcap")
    scan, analysis = scan_bag(bag)
    assert [s.relative_path for s in analysis.info.segments] == ["c.mcap", "b.mcap", "a.mcap"]
    assert [s.source for s in analysis.info.segments] == ["discovered"] * 3


@pytest.mark.parametrize("text, fragment", [
    ("::: not yaml :::\n  - [", "not valid YAML"),
    ("just a string\n", "no 'rosbag2_bagfile_information' mapping"),
    ("", "empty"),
    ("rosbag2_bagfile_information: {}\n", "must be an integer"),
    ("a: 1\na: 2\n", "duplicate key"),
])
def test_malformed_metadata_is_reported_not_crashed(tmp_path, synth_profile, text, fragment):
    bag = write_bag(tmp_path / "bad", with_metadata=False)
    (bag / "metadata.yaml").write_text(text)
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.PARTIAL
    assert ctx.bag.info.metadata_present and not ctx.bag.info.metadata_valid
    assert fragment in " ".join(ctx.bag.info.metadata_errors)
    assert check(ctx, "bag:metadata").outcome is CheckOutcome.FAIL
    assert ctx.scan.inspection.total_message_count == 123  # segments are still inspected
    # the unusable metadata.yaml is still hashed so export can preserve it
    assert "metadata.yaml" in [f.relative_path for f in ctx.bag.files]


def test_metadata_wrong_field_types_name_the_field(tmp_path):
    md = metadata_dict([("x_0.mcap", standard_streams())])
    md["rosbag2_bagfile_information"]["message_count"] = "many"
    parsed, errors = parse_bag_metadata(yaml.safe_dump(md).encode())
    assert parsed is None
    assert any("message_count" in e for e in errors)


@pytest.mark.parametrize("version", [2, 10, 42])
def test_unsupported_metadata_version_is_explicit(version):
    md = metadata_dict([("x_0.mcap", standard_streams())], version=9)
    md["rosbag2_bagfile_information"]["version"] = version
    parsed, errors = parse_bag_metadata(yaml.safe_dump(md).encode())
    assert parsed is None and any("unsupported rosbag2 metadata version" in e for e in errors)


def test_old_metadata_version_without_files_list_is_accepted_with_a_note():
    md = metadata_dict([("x_0.mcap", standard_streams())], version=4)
    parsed, errors = parse_bag_metadata(yaml.safe_dump(md).encode())
    assert not errors and parsed.file_entries == ()
    assert any("per-file" in w for w in parsed.warnings)


# ---------------------------------------------------------------- segment problems


def test_metadata_references_missing_segment(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "miss", three_way_split())
    (bag / "miss_1.mcap").unlink()
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.PARTIAL
    assert states(ctx)["miss_1.mcap"] is SegmentState.MISSING
    assert states(ctx)["miss_0.mcap"] is SegmentState.READABLE
    seg = check(ctx, "bag:segments")
    assert seg.outcome is CheckOutcome.FAIL and "miss_1.mcap" in seg.message
    assert check(ctx, "bag:message_counts").outcome is CheckOutcome.INCONCLUSIVE  # not "mismatch", not "zero"
    # a missing segment must not turn into a *required topic is absent* failure: it is unknown
    assert all(c.outcome is not CheckOutcome.FAIL for c in ctx.report.checks if c.check_id.startswith("topic_present"))
    # nothing fabricated for the missing file
    miss = next(s for s in ctx.bag.info.segments if s.relative_path == "miss_1.mcap")
    assert miss.sha256 is None and miss.observed_message_count is None


def test_corrupted_segment_is_reported_and_others_still_inspected(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "corrupt", three_way_split())
    data = bytearray((bag / "corrupt_1.mcap").read_bytes())
    data[len(data) // 2] ^= 0xFF
    (bag / "corrupt_1.mcap").write_bytes(bytes(data))
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.PARTIAL
    assert states(ctx)["corrupt_1.mcap"] in (SegmentState.PARTIALLY_READABLE, SegmentState.UNREADABLE)
    assert states(ctx)["corrupt_0.mcap"] is SegmentState.READABLE
    assert check(ctx, "bag:segments").outcome is CheckOutcome.FAIL
    assert ctx.scan.inspection.readability is ArtifactReadability.PARTIALLY_READABLE


def test_truncated_and_non_mcap_segments(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "trunc", three_way_split())
    p = bag / "trunc_2.mcap"
    p.write_bytes(p.read_bytes()[:100])
    (bag / "trunc_1.mcap").write_bytes(b"not mcap at all")
    ctx = analyze_session(bag, synth_profile)
    assert states(ctx)["trunc_1.mcap"] is SegmentState.UNREADABLE
    assert states(ctx)["trunc_2.mcap"] in (SegmentState.PARTIALLY_READABLE, SegmentState.UNREADABLE)
    assert ctx.report.status is SessionStatus.PARTIAL  # trunc_0 still readable


def test_all_segments_unreadable_is_failed(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "dead")
    (bag / "dead_0.mcap").write_bytes(b"garbage")
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.FAILED
    assert ctx.report.artifact_readability is ArtifactReadability.UNREADABLE


def test_unsupported_storage_is_explicit_and_not_treated_as_valid(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "sql", storage="sqlite3")
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.FAILED
    assert states(ctx)["sql_0.db3"] is SegmentState.UNSUPPORTED
    sf = check(ctx, "bag:storage_format")
    assert sf.outcome is CheckOutcome.FAIL and "sqlite3" in sf.message
    assert ctx.scan.inspection.total_message_count == 0  # nothing was invented
    assert next(f for f in ctx.bag.files if f.relative_path == "sql_0.db3").sha256  # still preserved/hashed


def test_file_level_compression_is_unsupported_not_misread(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "zst", compression_mode="FILE", metadata_edit=lambda m: m.update(compression_format="zstd"))
    ctx = analyze_session(bag, synth_profile)
    assert ctx.report.status is SessionStatus.FAILED
    assert states(ctx)["zst_0.mcap"] is SegmentState.UNSUPPORTED
    assert "FILE" in check(ctx, "bag:storage_format").message


def test_message_compression_mode_with_mcap_is_accepted(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "msgc", compression_mode="MESSAGE", metadata_edit=lambda m: m.update(compression_format="zstd"))
    assert analyze_session(bag, synth_profile).report.status is SessionStatus.COMPLETE


def test_message_count_mismatch_is_flagged(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "cnt", three_way_split(), metadata_edit=lambda m: m.update(message_count=m["message_count"] + 5))
    ctx = analyze_session(bag, synth_profile)
    mc = check(ctx, "bag:message_counts")
    assert mc.outcome is CheckOutcome.FAIL and "128" in mc.message and "123" in mc.message
    assert "not which side is wrong" in mc.message
    assert ctx.report.status is SessionStatus.PARTIAL


def test_per_file_and_per_topic_mismatch(tmp_path, synth_profile):
    def edit(m):
        m["files"][0]["message_count"] += 1
        m["topics_with_message_count"][0]["message_count"] += 1
    ctx = analyze_session(write_bag(tmp_path / "pf", three_way_split(), metadata_edit=edit), synth_profile)
    msg = check(ctx, "bag:message_counts").message
    assert "pf_0.mcap" in msg and "topic /" in msg


# ---------------------------------------------------------------- duplicate / conflicting / unsafe references


def test_duplicate_segment_reference(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "dup", metadata_edit=lambda m: m["relative_file_paths"].append("./dup_0.mcap"))
    ctx = analyze_session(bag, synth_profile)
    assert any("more than once" in c for c in ctx.bag.info.conflicts)
    assert len(ctx.bag.info.segments) == 1  # inspected once, not double counted
    assert ctx.scan.inspection.total_message_count == 123
    assert ctx.report.status is SessionStatus.PARTIAL


def test_files_list_conflicts_with_relative_paths(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "conf", three_way_split())
    extra = bag / "conf_extra.mcap"
    extra.write_bytes((bag / "conf_2.mcap").read_bytes())
    def edit(m):
        m["files"].append({"path": "conf_extra.mcap", "starting_time": {"nanoseconds_since_epoch": 1}, "duration": {"nanoseconds": 1}, "message_count": 0})
    md = yaml.safe_load((bag / "metadata.yaml").read_text())
    edit(md["rosbag2_bagfile_information"])
    (bag / "metadata.yaml").write_text(yaml.safe_dump(md))
    ctx = analyze_session(bag, synth_profile)
    assert any("not in relative_file_paths" in c for c in ctx.bag.info.conflicts)
    assert "conf_extra.mcap" in states(ctx)  # still inspected: nothing silently dropped
    assert next(s for s in ctx.bag.info.segments if s.relative_path == "conf_extra.mcap").source == "metadata_files_only"
    assert ctx.report.status is SessionStatus.PARTIAL


@pytest.mark.parametrize("bad", ["../outside.mcap", "/etc/passwd", "sub/../../x.mcap", "a\\b.mcap"])
def test_unsafe_segment_paths_are_refused(tmp_path, synth_profile, bad):
    bag = write_bag(tmp_path / "unsafe", metadata_edit=lambda m: m["relative_file_paths"].append(bad))
    (tmp_path / "outside.mcap").write_bytes((bag / "unsafe_0.mcap").read_bytes())
    ctx = analyze_session(bag, synth_profile)
    seg = next(s for s in ctx.bag.info.segments if s.relative_path == bad)
    assert seg.state is SegmentState.UNSAFE_PATH and seg.sha256 is None
    assert ctx.report.status is SessionStatus.PARTIAL
    assert bad not in [f.relative_path for f in ctx.bag.files]  # never hashed, never copied


def test_symlink_escaping_the_bag_is_refused(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "sym")
    outside = tmp_path / "secret.mcap"
    outside.write_bytes((bag / "sym_0.mcap").read_bytes())
    os.symlink(outside, bag / "sym_link.mcap")
    md = yaml.safe_load((bag / "metadata.yaml").read_text())
    md["rosbag2_bagfile_information"]["relative_file_paths"].append("sym_link.mcap")
    (bag / "metadata.yaml").write_text(yaml.safe_dump(md))
    ctx = analyze_session(bag, synth_profile)
    assert states(ctx)["sym_link.mcap"] is SegmentState.UNSAFE_PATH


def test_listed_directory_is_not_a_file(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "dir", metadata_edit=lambda m: m["relative_file_paths"].append("subdir"))
    (bag / "subdir").mkdir()
    ctx = analyze_session(bag, synth_profile)
    assert states(ctx)["subdir"] is SegmentState.NOT_A_FILE


def test_unlisted_mcap_is_a_conflict_but_unlisted_notes_file_is_only_a_warning(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "extra")
    (bag / "stray.mcap").write_bytes((bag / "extra_0.mcap").read_bytes())
    ctx = analyze_session(bag, synth_profile)
    assert check(ctx, "bag:segments").outcome is CheckOutcome.FAIL and "stray.mcap" in check(ctx, "bag:segments").message
    assert ctx.report.status is SessionStatus.PARTIAL
    assert "stray.mcap" not in [f.relative_path for f in ctx.bag.files]  # not accounted => not exported

    bag2 = write_bag(tmp_path / "notes")
    (bag2 / "NOTES.txt").write_text("operator scribbles")
    ctx2 = analyze_session(bag2, synth_profile)
    assert check(ctx2, "bag:unaccounted_files").outcome is CheckOutcome.FAIL
    assert not check(ctx2, "bag:unaccounted_files").required
    assert ctx2.report.status is SessionStatus.COMPLETE  # warning only; still reported
    assert [u.name for u in ctx2.bag.info.unaccounted] == ["NOTES.txt"]


# ---------------------------------------------------------------- determinism


def test_discovery_and_ordering_are_deterministic(tmp_path, synth_profile):
    a = write_bag(tmp_path / "a" / "same", three_way_split(), with_metadata=False)
    b = tmp_path / "b" / "same"
    b.mkdir(parents=True)
    for name in ("same_2.mcap", "same_0.mcap", "same_1.mcap"):  # created in a different order
        (b / name).write_bytes((a / name).read_bytes())
    sa, aa = scan_bag(a)
    sb, ab = scan_bag(b)
    assert aa.info.model_dump_json() == ab.info.model_dump_json()
    assert sa.inspection.model_dump_json() == sb.inspection.model_dump_json()
    assert aa.digest == ab.digest
    assert scan_bag(a)[1].info.model_dump_json() == aa.info.model_dump_json()  # repeatable


def test_metadata_order_is_respected_over_file_names(tmp_path, synth_profile):
    bag = write_bag(tmp_path / "ord", three_way_split(), metadata_edit=lambda m: None)
    md = yaml.safe_load((bag / "metadata.yaml").read_text())
    md["rosbag2_bagfile_information"]["relative_file_paths"].reverse()
    md["rosbag2_bagfile_information"]["files"].reverse()
    (bag / "metadata.yaml").write_text(yaml.safe_dump(md))
    ctx = analyze_session(bag, synth_profile)
    assert [s.relative_path for s in ctx.bag.info.segments] == ["ord_2.mcap", "ord_1.mcap", "ord_0.mcap"]
    # the observed windows then go backwards: reported as a warning, not trusted silently
    assert check(ctx, "bag:segment_order").outcome is CheckOutcome.FAIL
    assert not check(ctx, "bag:segment_order").required


# ---------------------------------------------------------------- input errors


def test_nonexistent_and_file_in_place_of_directory(tmp_path):
    with pytest.raises(InputError):
        scan_bag(tmp_path / "nope")
    f = tmp_path / "f.mcap"
    f.write_bytes(b"x")
    with pytest.raises(InputError):
        scan_bag(f)


def test_empty_directory_is_failed_not_complete(tmp_path, synth_profile):
    (tmp_path / "empty").mkdir()
    ctx = analyze_session(tmp_path / "empty", synth_profile)
    assert ctx.report.status is SessionStatus.FAILED
    assert check(ctx, "bag:segments").outcome is CheckOutcome.FAIL


# ---------------------------------------------------------------- large recording


def test_large_segment_is_scanned_without_loading_it_into_memory(tmp_path, synth_profile):
    """~40 MB of payload scanned while Python-level peak allocation stays far below the file size."""
    from mcap.writer import CompressionType, Writer

    root = tmp_path / "big"
    root.mkdir()
    path = root / "big_0.mcap"
    n, payload = 10_000, b"x" * 4096
    with open(path, "wb") as f:
        w = Writer(f, chunk_size=1024 * 1024, compression=CompressionType.NONE, enable_crcs=True, enable_data_crcs=True)
        w.start(profile="", library="test")
        sch = w.register_schema(name="s", encoding="jsonschema", data=b"{}")
        ch = w.register_channel(topic="/joint_states", message_encoding="json", schema_id=sch)
        for i in range(n):
            t = mf.BASE_NS + i * 20_000_000
            w.add_message(channel_id=ch, log_time=t, publish_time=t, sequence=i, data=payload)
        w.finish()
    size = path.stat().st_size
    assert size > 40_000_000
    md = metadata_dict([("big_0.mcap", [mf.StreamSpec("/joint_states", tuple(mf.BASE_NS + i * 20_000_000 for i in range(n)))])])
    (root / "metadata.yaml").write_text(yaml.safe_dump(md))
    tracemalloc.start()
    scan, analysis = scan_bag(root)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert scan.inspection.total_message_count == n
    assert scan.inspection.readability is ArtifactReadability.READABLE
    assert peak < size / 4, f"peak Python allocation {peak} is not far below file size {size}"
