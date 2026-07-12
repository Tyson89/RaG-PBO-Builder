from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_RELEASES_URL = "https://api.github.com/repos/Tyson89/RaG-PBO-Builder/releases?per_page=20"
USER_AGENT = "RaG-PBO-Builder"
INSTALLER_PREFIX = "rag_pbo_tools_setup"


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    name: str
    html_url: str
    body: str
    prerelease: bool = False


def parse_version_key(version_text: str) -> tuple[int, int, int, int, int]:
    text = str(version_text or "").strip().lower()
    match = re.search(r"(\d+(?:\.\d+){0,3})", text)
    if not match:
        return (0, 0, 0, 0, 0)

    parts = [int(part) for part in match.group(1).split(".")]
    parts = (parts + [0, 0, 0, 0])[:4]

    if "alpha" in text:
        release_rank = 0
    elif "beta" in text:
        release_rank = 1
    elif re.search(r"\brc\b|release-candidate", text):
        release_rank = 2
    else:
        release_rank = 3

    return (*parts, release_rank)


def is_remote_version_newer(local_version: str, remote_tag: str) -> bool:
    return parse_version_key(remote_tag) > parse_version_key(local_version)


def select_latest_release(releases: list[dict]) -> ReleaseInfo:
    candidates = []
    for release in releases:
        if release.get("draft"):
            continue
        tag_name = str(release.get("tag_name") or "").strip()
        if parse_version_key(tag_name) == (0, 0, 0, 0, 0):
            continue
        candidates.append(release)

    if not candidates:
        raise ValueError("No published versioned GitHub releases were found.")

    latest = max(candidates, key=lambda item: parse_version_key(str(item.get("tag_name") or "")))
    tag_name = str(latest.get("tag_name") or "").strip()
    return ReleaseInfo(
        tag_name=tag_name,
        name=str(latest.get("name") or tag_name),
        html_url=str(latest.get("html_url") or ""),
        body=str(latest.get("body") or ""),
        prerelease=bool(latest.get("prerelease")),
    )


def github_request(url: str, accept: str = "application/vnd.github+json", timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Update repository is not publicly accessible.") from exc
        raise UpdateError(f"GitHub request failed: HTTP {exc.code} {exc.reason}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise UpdateError(f"GitHub request failed: {exc}") from exc


def fetch_latest_release(url: str = DEFAULT_RELEASES_URL, timeout: int = 8) -> ReleaseInfo:
    try:
        payload = github_request(url, timeout=timeout).decode("utf-8", errors="replace")
    except UpdateError as exc:
        raise RuntimeError(str(exc)) from exc

    try:
        releases = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub returned an unreadable release response.") from exc

    if not isinstance(releases, list):
        message = "GitHub returned an unexpected release response."
        if isinstance(releases, dict) and releases.get("message"):
            message = f"{message} {releases['message']}"
        raise RuntimeError(message)

    return select_latest_release(releases)


def select_installer_asset(release: dict):
    assets = release.get("assets", []) if isinstance(release, dict) else []
    installers = [asset for asset in assets if str(asset.get("name", "")).casefold().endswith(".exe")]
    for asset in installers:
        if str(asset.get("name", "")).casefold().startswith(INSTALLER_PREFIX):
            return asset
    for asset in installers:
        if "setup" in str(asset.get("name", "")).casefold():
            return asset
    return None


def select_checksum_asset(release: dict, installer_name: str):
    assets = release.get("assets", []) if isinstance(release, dict) else []
    expected_names = {
        f"{installer_name}.sha256".casefold(),
        f"{Path(installer_name).stem}.sha256".casefold(),
        "sha256sums.txt",
        "checksums.txt",
    }
    for asset in assets:
        if str(asset.get("name", "")).casefold() in expected_names:
            return asset
    return None


def check_for_update(current_version: str):
    try:
        releases = json.loads(github_request(DEFAULT_RELEASES_URL).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned invalid release data.") from exc
    if not isinstance(releases, list):
        raise UpdateError("GitHub returned an unexpected release response.")
    return select_latest_update(releases, current_version)


def select_latest_update(releases: list[dict], current_version: str):
    candidates = []
    for release in releases if isinstance(releases, list) else []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        version = str(release.get("tag_name") or release.get("name") or "").strip()
        installer = select_installer_asset(release)
        if version and installer and is_remote_version_newer(current_version, version):
            candidates.append((parse_version_key(version), version, release, installer))
    if not candidates:
        return None
    _parts, version, release, installer = max(candidates, key=lambda item: item[0])
    return {
        "version": version,
        "name": str(release.get("name") or version),
        "notes": str(release.get("body") or "").strip(),
        "release_url": str(release.get("html_url") or ""),
        "installer": installer,
        "checksum": select_checksum_asset(release, installer.get("name", "")),
    }


def parse_checksum(text: str, installer_name: str) -> str:
    fallback = ""
    for line in str(text).splitlines():
        match = re.search(r"\b([0-9a-fA-F]{64})\b(?:\s+[* ]?(.+))?", line.strip())
        if not match:
            continue
        digest = match.group(1).casefold()
        filename = (match.group(2) or "").strip()
        if filename and Path(filename).name.casefold() == Path(installer_name).name.casefold():
            return digest
        if not fallback:
            fallback = digest
    return fallback


def expected_installer_digest(update: dict) -> str:
    installer = update["installer"]
    digest = str(installer.get("digest") or "")
    if digest.casefold().startswith("sha256:"):
        value = digest.split(":", 1)[1].strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    checksum_asset = update.get("checksum")
    if checksum_asset:
        checksum_url = str(checksum_asset.get("browser_download_url") or "")
        if checksum_url:
            checksum_text = github_request(checksum_url, accept="application/octet-stream", timeout=30).decode("utf-8", errors="replace")
            return parse_checksum(checksum_text, installer.get("name", ""))
    return ""


def download_update(update: dict, output_dir=None) -> Path:
    installer = update["installer"]
    installer_name = Path(str(installer.get("name") or "RaG_PBO_Tools_Setup.exe")).name
    download_url = str(installer.get("browser_download_url") or "")
    if not download_url:
        raise UpdateError("Release installer has no download URL.")
    expected_digest = expected_installer_digest(update)
    if not expected_digest:
        raise UpdateError("Release installer has no SHA-256 digest or checksum asset.")

    target_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "RaG PBO Builder Updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / installer_name
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(download_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Installer download failed: {exc}") from exc

    actual_digest = digest.hexdigest().casefold()
    if actual_digest != expected_digest.casefold():
        partial.unlink(missing_ok=True)
        raise UpdateError("Downloaded installer failed SHA-256 verification.")
    os.replace(partial, target)
    return target
