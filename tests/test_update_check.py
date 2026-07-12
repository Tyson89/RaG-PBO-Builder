import pytest

from rag_update_check import (
    is_remote_version_newer,
    parse_checksum,
    parse_version_key,
    select_latest_release,
    select_latest_update,
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



def test_select_latest_update_requires_newer_installer_asset():
    update = select_latest_update(
        [
            {"tag_name": "v0.8.5-beta", "assets": [{"name": "RaG_PBO_Tools_Setup.exe", "browser_download_url": "old"}]},
            {"tag_name": "v0.8.6-beta", "name": "0.8.6 Beta", "body": "notes", "html_url": "release", "assets": [
                {"name": "RaG_PBO_Tools_Setup.exe", "browser_download_url": "new", "digest": "sha256:" + "a" * 64},
            ]},
        ],
        "0.8.5 Beta",
    )

    assert update["version"] == "v0.8.6-beta"
    assert update["name"] == "0.8.6 Beta"
    assert update["installer"]["browser_download_url"] == "new"


def test_parse_checksum_prefers_matching_installer_name():
    checksum = parse_checksum(
        """
1111111111111111111111111111111111111111111111111111111111111111  other.exe
2222222222222222222222222222222222222222222222222222222222222222  RaG_PBO_Tools_Setup.exe
""",
        "RaG_PBO_Tools_Setup.exe",
    )

    assert checksum == "2" * 64
