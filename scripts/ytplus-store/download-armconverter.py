#!/usr/bin/env python3
"""Download one exact ARMConverter version without exposing session or download tokens."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from workflow_status import write_status

ROOT = "https://armconverter.com/decryptedappstore"
MAX_JSON = 4 * 1024 * 1024
MAX_IPA = 1024 * 1024 * 1024


class AuthFailure(RuntimeError):
    pass


class WaitingForIPA(RuntimeError):
    pass


def session_value() -> str:
    value = os.environ.get("ARMCONVERTER_SESSION", "").strip()
    if value.startswith("session="):
        value = value.removeprefix("session=")
    if not value or len(value) > 4096 or any(character in value for character in "\r\n"):
        raise AuthFailure("ARMConverter session is missing or malformed")
    return value


def json_request(path: str, session: str, method: str = "GET") -> dict:
    request = urllib.request.Request(f"{ROOT}{path}", method=method, data=b"" if method == "POST" else None)
    request.add_header("Cookie", f"session={session}")
    request.add_header("Referer", ROOT)
    request.add_header("User-Agent", "ytplus-store-builder")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read(MAX_JSON + 1)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AuthFailure("ARMConverter rejected the session") from error
        raise RuntimeError(f"ARMConverter returned HTTP {error.code}") from error
    if len(body) > MAX_JSON:
        raise RuntimeError("ARMConverter response exceeded size limit")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError("ARMConverter returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("ARMConverter returned an unexpected payload")
    if payload.get("loginRequired") is True:
        raise AuthFailure("ARMConverter session has expired")
    return payload


def download_ipa(app_id: str, bundle_id: str, version: str, token: str, destination: Path) -> None:
    safe_version = urllib.parse.quote(version, safe="")
    safe_token = urllib.parse.quote(token, safe="")
    url = f"{ROOT}/download/{app_id}/{bundle_id}/{safe_version}?token={safe_token}"
    request = urllib.request.Request(url, headers={"Referer": ROOT, "User-Agent": "ytplus-store-builder"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            with destination.open("wb") as output:
                copied = 0
                while chunk := response.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > MAX_IPA:
                        raise RuntimeError("IPA download exceeded one-GiB limit")
                    output.write(chunk)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"IPA download returned HTTP {error.code}") from error


def validate_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > 100_000:
                raise RuntimeError("IPA contains too many entries")
            if archive.testzip() is not None:
                raise RuntimeError("IPA ZIP integrity test failed")
    except zipfile.BadZipFile as error:
        raise RuntimeError("downloaded file is not an IPA ZIP") from error


def run() -> None:
    session = session_value()
    version = os.environ["YOUTUBE_VERSION"]
    app_id = os.environ.get("APPSTORE_ID", "544007664")
    bundle_id = os.environ.get("ORIGINAL_BUNDLE_IDENTIFIER", "com.google.ios.youtube")

    versions_payload = json_request(f"/versions/{app_id}/0?country=us", session, method="POST")
    versions = versions_payload.get("versions")
    if not isinstance(versions, list):
        raise RuntimeError("ARMConverter version list is missing")
    matches = [entry for entry in versions if isinstance(entry, dict) and entry.get("ver") == version]
    if len(matches) != 1 or matches[0].get("exists") is not True:
        raise WaitingForIPA(f"YouTube {version} is not listed as downloadable")

    quoted_version = urllib.parse.quote(version, safe="")
    info = json_request(f"/download/{app_id}/{bundle_id}/{quoted_version}/info", session)
    if info.get("available") is not True:
        raise WaitingForIPA(f"YouTube {version} is listed but not available")

    prepared = json_request(f"/download/{app_id}/{bundle_id}/{quoted_version}/prepare", session, method="POST")
    token = prepared.get("token")
    if not isinstance(token, str) or not token:
        error = prepared.get("error")
        if isinstance(error, str) and "login" in error.lower():
            raise AuthFailure("ARMConverter session has expired")
        raise RuntimeError("ARMConverter did not issue a download token")

    destination = Path("work/youtube-original.ipa")
    download_ipa(app_id, bundle_id, version, token, destination)
    validate_zip(destination)


def main() -> None:
    try:
        run()
    except WaitingForIPA as error:
        write_status(Path("work/status.json"), "waiting_for_ipa", str(error))
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    except AuthFailure as error:
        write_status(Path("work/status.json"), "armconverter_auth_failed", str(error))
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    except Exception as error:
        write_status(Path("work/status.json"), "download_failed", str(error))
        print(f"ARMConverter download failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
