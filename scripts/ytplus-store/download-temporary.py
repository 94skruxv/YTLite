#!/usr/bin/env python3
"""Download one staged original IPA without exposing its secret URL."""

from __future__ import annotations

import hashlib
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from workflow_status import write_status

MAX_IPA = 1024 * 1024 * 1024
DOWNLOAD_HOST = "temp.sh"
DOWNLOAD_PATH = re.compile(r"^/[A-Za-z0-9]{5,64}/youtube\.ipa$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        raise urllib.error.HTTPError("", 400, "redirect rejected", {}, None)


def validated_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("temporary IPA URL has an invalid port") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != DOWNLOAD_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not DOWNLOAD_PATH.fullmatch(parsed.path)
    ):
        raise ValueError("temporary IPA URL is not trusted")
    return value


def download(url: str, destination: Path, expected_sha256: str) -> None:
    request = urllib.request.Request(
        url,
        data=b"",
        headers={"User-Agent": "ytplus-store/1"},
        method="POST",
    )
    opener = urllib.request.build_opener(NoRedirect())
    temporary = destination.with_suffix(".download")
    digest = hashlib.sha256()
    written = 0
    try:
        with opener.open(request, timeout=180) as response, temporary.open("xb") as handle:
            if response.status != 200:
                raise RuntimeError("temporary IPA download returned a non-success response")
            while chunk := response.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_IPA:
                    raise RuntimeError("temporary IPA download exceeds size limit")
                digest.update(chunk)
                handle.write(chunk)
        if written < 10 * 1024 * 1024:
            raise RuntimeError("temporary IPA download is unexpectedly small")
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError("temporary IPA SHA-256 does not match")
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("temporary IPA ZIP integrity check failed")
        temporary.replace(destination)
    except urllib.error.URLError:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("temporary IPA download request failed") from None
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def run() -> None:
    url = validated_url(os.environ.get("YOUTUBE_IPA_URL", "").strip())
    expected_sha256 = os.environ.get("ORIGINAL_SHA256", "").strip()
    if not SHA256.fullmatch(expected_sha256):
        raise ValueError("temporary IPA SHA-256 is missing or malformed")
    destination = Path("work/youtube-original.ipa")
    destination.parent.mkdir(exist_ok=True)
    download(url, destination, expected_sha256)


def main() -> None:
    try:
        run()
    except Exception as error:
        write_status(Path("work/status.json"), "download_failed", str(error)[:500])
        print(f"temporary IPA download failed: {error}", file=os.sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
