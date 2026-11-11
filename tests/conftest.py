from pathlib import Path

import pytest

from tests import make_fixtures as mf

ROOT = Path(__file__).parent.parent
PROFILES = ROOT / "examples" / "profiles"


@pytest.fixture(scope="session")
def fx(tmp_path_factory) -> Path:
    """Freshly generated synthetic fixtures (not the committed copies)."""
    d = tmp_path_factory.mktemp("fixtures")
    mf.write_fixtures(d)
    return d


@pytest.fixture(scope="session")
def synth_profile() -> Path:
    return PROFILES / "synthetic_fixture.profile.yaml"  # declares log_time_basis: system_time


@pytest.fixture(scope="session")
def example_profile() -> Path:
    return PROFILES / "example_robot.profile.yaml"  # log_time_basis: unknown
