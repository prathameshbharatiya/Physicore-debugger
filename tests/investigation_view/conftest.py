"""The scale tests of this package build large reports and views. Release them when each module finishes so that they do not slow the timing-ratio tests
of the accepted suites that run later in the same process (measured: those tests failed intermittently in a full run before this was added)."""
import gc

import pytest


@pytest.fixture(autouse=True, scope="module")
def _release_memory_after_each_module():
    yield
    gc.collect()
