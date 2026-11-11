from importlib.metadata import version

import physicore_collector
from physicore_collector import versions


def test_tool_version_matches_package_metadata():
    assert versions.TOOL_VERSION == version("physicore-collector")
    assert physicore_collector.__version__ == versions.TOOL_VERSION


def test_schema_versions_are_nonempty():
    assert versions.MANIFEST_SCHEMA_VERSION
    assert versions.VALIDATION_REPORT_SCHEMA_VERSION
    assert "1.0" in versions.SUPPORTED_PROFILE_SCHEMA_VERSIONS
