#!/usr/bin/env python3
"""Download immutable YTPlus and Safari-extension inputs from pinned sources."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from workflow_status import write_status

API_ROOT = "https://api.github.com"
MAX_JSON = 4 * 1024 * 1024
MAX_DEB = 100 * 1024 * 1024
MAX_EXTENSION_ARCHIVE = 100 * 1024 * 1024
EXTENSION_COMMIT = "c87b5d5551b406477dca883158572f803251f52c"
ARCHIVE_EXTENSION_NAME = "OpenYouTubeSafariExtension.appex"


def api_json(path: str) -> dict:
    request = urllib.request.Request(f"{API_ROOT}{path}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "ytplus-store-builder")
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read(MAX_JSON + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"GitHub API returned HTTP {error.code}") from error
    if len(body) > MAX_JSON:
        raise RuntimeError("GitHub API response exceeded size limit")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API returned an unexpected payload")
    return payload


def download_public(url: str, destination: Path, maximum: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ytplus-store-builder"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            with destination.open("wb") as output:
                copied = 0
                while chunk := response.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > maximum:
                        raise RuntimeError("download exceeded size limit")
                    output.write(chunk)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"public download returned HTTP {error.code}") from error


def extract_extension(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 5_000:
            raise RuntimeError("extension archive contains too many entries")
        info_candidates = [
            member.filename
            for member in members
            if member.filename.endswith(f"/{ARCHIVE_EXTENSION_NAME}/Info.plist")
        ]
        if len(info_candidates) != 1:
            raise RuntimeError("pinned extension archive does not contain one expected appex")
        root = info_candidates[0][: -len("Info.plist")]
        selected = [member for member in members if member.filename.startswith(root)]
        if sum(member.file_size for member in selected) > MAX_EXTENSION_ARCHIVE:
            raise RuntimeError("extension contents exceed size limit")

        destination.mkdir(parents=True, exist_ok=False)
        for member in selected:
            relative = PurePosixPath(member.filename.removeprefix(root))
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("extension archive contains an unsafe path")
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError("extension archive contains a symbolic link")
            target = destination.joinpath(*relative.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if mode:
                target.chmod(mode & 0o777)


def run() -> None:
    tag = os.environ["UPSTREAM_TAG"]
    version = os.environ["YTPLUS_VERSION"]
    release = api_json(f"/repos/dayanch96/YTLite/releases/tags/{tag}")
    if release.get("tag_name") != tag:
        raise RuntimeError("release tag mismatch")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise RuntimeError("release is not stable")
    release_id = release.get("id")
    if not isinstance(release_id, int) or release_id <= 0:
        raise RuntimeError("release ID is missing")

    expected = re.compile(rf"^com\.dvntm\.ytlite_{re.escape(version)}_iphoneos-arm\.deb$")
    candidates = [asset for asset in release.get("assets", []) if expected.fullmatch(str(asset.get("name", "")))]
    if len(candidates) != 1:
        raise RuntimeError("release does not contain one unambiguous iphoneos-arm package")
    asset_url = candidates[0].get("browser_download_url")
    if not isinstance(asset_url, str) or not asset_url.startswith("https://github.com/dayanch96/YTLite/releases/download/"):
        raise RuntimeError("release asset URL is unexpected")

    work = Path("work")
    deb_path = work / "ytplus.deb"
    download_public(asset_url, deb_path, MAX_DEB)
    if deb_path.read_bytes()[:8] != b"!<arch>\n":
        raise RuntimeError("downloaded YTPlus asset is not a Debian package")

    descriptor, archive_name = tempfile.mkstemp(prefix="extension-", suffix=".zip", dir=work)
    os.close(descriptor)
    archive_path = Path(archive_name)
    try:
        download_public(
            f"https://github.com/BillyCurtis/OpenYouTubeSafariExtension/archive/{EXTENSION_COMMIT}.zip",
            archive_path,
            MAX_EXTENSION_ARCHIVE,
        )
        extract_extension(archive_path, work / "OpenYoutubeSafariExtension.appex")
    finally:
        archive_path.unlink(missing_ok=True)

    (work / "upstream.json").write_text(
        json.dumps(
            {
                "upstream_repository": "dayanch96/YTLite",
                "upstream_tag": tag,
                "upstream_release_id": release_id,
                "ytplus_asset": candidates[0]["name"],
                "extension_commit": EXTENSION_COMMIT,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    try:
        run()
    except Exception as error:
        write_status(Path("work/status.json"), "upstream_asset_missing", str(error))
        print(f"upstream asset resolution failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
