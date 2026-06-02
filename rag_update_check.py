from __future__ import annotations

from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.request


DEFAULT_RELEASES_URL = "https://api.github.com/repos/Tyson89/RaG-PBO-Builder/releases?per_page=20"
USER_AGENT = "RaG-PBO-Builder"


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


def fetch_latest_release(url: str = DEFAULT_RELEASES_URL, timeout: int = 8) -> ReleaseInfo:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"Could not check GitHub releases: {reason}") from exc

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
