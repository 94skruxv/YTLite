#!/usr/bin/env python3
"""Fail closed on workflow_dispatch input before using it in paths or commands."""

from __future__ import annotations

import json
import os
import plistlib
import re
import sys
from pathlib import Path

from workflow_status import write_status

VERSION = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
BUILD_ID = re.compile(r"^ytp-[0-9]+\.[0-9]+(?:\.[0-9]+)?-yt-[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


def version_parts(value: str) -> tuple[int, int, int]:
    if not VERSION.fullmatch(value):
        raise ValueError(f"invalid numeric version {value!r}")
    parts = [int(component) for component in value.split(".")]
    parts.extend([0] * (3 - len(parts)))
    return parts[0], parts[1], parts[2]


def store_version(youtube: str, ytplus: str) -> str:
    yt_major, yt_minor, yt_patch = version_parts(youtube)
    plus_major, plus_minor, plus_patch = version_parts(ytplus)
    if yt_minor >= 1000 or yt_patch >= 1000:
        raise ValueError("YouTube minor and patch must be below 1000")
    if plus_minor >= 100 or plus_patch >= 100:
        raise ValueError("YTPlus minor and patch must be below 100")
    return f"{yt_major}.{yt_minor * 1000 + yt_patch}.{plus_major * 10000 + plus_minor * 100 + plus_patch}"


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def run() -> None:
    upstream_tag = required_env("UPSTREAM_TAG")
    ytplus = required_env("YTPLUS_VERSION")
    youtube = required_env("YOUTUBE_VERSION")
    requested_store_version = required_env("STORE_VERSION")
    build_id = required_env("BUILD_ID")
    display_name = required_env("DISPLAY_NAME")
    bundle_id = required_env("BUNDLE_IDENTIFIER")
    run_number = required_env("GITHUB_RUN_NUMBER")
    ipa_source = required_env("IPA_SOURCE")
    original_sha256 = os.environ.get("ORIGINAL_SHA256", "").strip()

    version_parts(ytplus)
    version_parts(youtube)
    requested_store_parts = version_parts(requested_store_version)
    if upstream_tag not in (ytplus, f"v{ytplus}"):
        raise ValueError("upstream tag must exactly equal ytplus_version with an optional v prefix")
    if not BUILD_ID.fullmatch(build_id) or build_id != f"ytp-{ytplus}-yt-{youtube}":
        raise ValueError("build_id does not exactly identify the requested versions")
    if not BUNDLE_ID.fullmatch(bundle_id) or len(bundle_id) > 255:
        raise ValueError("invalid bundle identifier")
    if len(display_name) > 64 or any(ord(character) < 32 for character in display_name):
        raise ValueError("invalid display name")
    if not run_number.isdecimal() or int(run_number) <= 0:
        raise ValueError("invalid GitHub run number")
    if ipa_source not in ("armconverter", "temporary"):
        raise ValueError("invalid IPA source")
    if ipa_source == "temporary":
        if not SHA256.fullmatch(original_sha256):
            raise ValueError("temporary IPA source requires a SHA-256 digest")
    elif original_sha256:
        raise ValueError("ARMConverter source must not provide an original SHA-256")

    computed = store_version(youtube, ytplus)
    if requested_store_parts < version_parts(computed):
        raise ValueError("store version is older than the immutable input versions")
    work = Path("work")
    work.mkdir(exist_ok=True)
    (work / "inputs.json").write_text(
        json.dumps(
            {
                "upstream_tag": upstream_tag,
                "ytplus_version": ytplus,
                "youtube_version": youtube,
                "build_id": build_id,
                "display_name": display_name,
                "bundle_identifier": bundle_id,
                "store_version": requested_store_version,
                "bundle_version": run_number,
                "ipa_source": ipa_source,
                "original_sha256": original_sha256,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with (work / "metadata.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleVersion": run_number,
                "YTPlusOriginalYouTubeVersion": youtube,
                "YTPlusVersion": ytplus,
                "YTPlusBuildID": build_id,
            },
            handle,
            fmt=plistlib.FMT_XML,
            sort_keys=True,
        )


def main() -> None:
    try:
        run()
    except Exception as error:
        write_status(Path("work/status.json"), "validation_failed", str(error))
        print(f"input validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
