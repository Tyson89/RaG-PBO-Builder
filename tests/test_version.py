from rag_pbo_builder_gui import APP_VERSION as BUILDER_VERSION
from rag_pbo_inspector_gui import APP_VERSION as INSPECTOR_VERSION
from rag_preflight import APP_VERSION as PREFLIGHT_VERSION
from rag_version import APP_VERSION


def test_apps_share_single_version_source():
    assert BUILDER_VERSION == APP_VERSION
    assert INSPECTOR_VERSION == APP_VERSION
    assert PREFLIGHT_VERSION == APP_VERSION
