import pytest

from rag_update_check import (
    is_remote_version_newer,
    parse_version_key,
    select_latest_release,
)


def test_parse_version_key_handles_beta_text_and_tags():
    assert parse_version_key("0.8.1 Beta") == (0, 8, 1, 0, 1)
    assert parse_version_key("v0.8.2-beta") == (0, 8, 2, 0, 1)


def test_remote_version_comparison():
    assert is_remote_version_newer("0.8.1 Beta", "v0.8.2-beta")
    assert not is_remote_version_newer("0.8.1 Beta", "v0.8.1-beta")
    assert not is_remote_version_newer("0.8.1 Beta", "v0.8.0-beta")


def test_select_latest_release_includes_beta_prereleases():
    release = select_latest_release(
        [
            {"tag_name": "v0.8.1-beta", "name": "0.8.1 Beta", "html_url": "old", "prerelease": True},
            {"tag_name": "v0.8.2-beta", "name": "0.8.2 Beta", "html_url": "new", "prerelease": True},
            {"tag_name": "v0.8.3-beta", "draft": True},
        ]
    )

    assert release.tag_name == "v0.8.2-beta"
    assert release.name == "0.8.2 Beta"
    assert release.html_url == "new"
    assert release.prerelease is True


def test_select_latest_release_rejects_empty_release_list():
    with pytest.raises(ValueError, match="No published versioned"):
        select_latest_release([])
