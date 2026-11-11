import hashlib
from pathlib import Path

from tests import make_fixtures as mf

COMMITTED = Path(__file__).parent.parent / "examples" / "fixtures"


def digest(files):
    return {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}


def test_generation_is_deterministic():
    assert digest(mf.all_fixture_files()) == digest(mf.all_fixture_files())


def test_committed_fixtures_match_generator():
    for rel, data in mf.all_fixture_files().items():
        assert (COMMITTED / rel).read_bytes() == data, f"{rel} is stale; rerun python -m tests.make_fixtures examples/fixtures"


def test_no_unexpected_files_in_committed_fixtures():
    on_disk = {p.relative_to(COMMITTED).as_posix() for p in COMMITTED.rglob("*") if p.is_file()}
    assert on_disk == set(mf.all_fixture_files())


def test_cadence_gap_is_objectively_defined():
    # exactly one jump: last message at +0.48 s, next at +1.00 s on a 50 Hz topic
    ts = mf.joint_states(skip=range(25, 50)).log_times_ns
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    assert sorted(set(gaps)) == [20_000_000, 520_000_000] and gaps.count(520_000_000) == 1
