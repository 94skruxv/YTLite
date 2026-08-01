#!/usr/bin/env python3
"""Inspect original and injected IPAs with bounded ZIP and plist handling."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from workflow_status import write_status

MAX_IPA = 1024 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_UNCOMPRESSED = 4 * 1024 * 1024 * 1024
MAX_PLIST = 4 * 1024 * 1024
MAX_EXECUTABLE = 512 * 1024 * 1024
YTPLUS_METADATA_KEYS = {
    "YTPlusOriginalYouTubeVersion",
    "YTPlusVersion",
    "YTPlusBuildID",
}


def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise ValueError("IPA contains too many entries")
    names: set[str] = set()
    total = 0
    for member in members:
        path = PurePosixPath(member.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("IPA contains an unsafe path")
        if member.filename in names:
            raise ValueError("IPA contains duplicate paths")
        names.add(member.filename)
        total += member.file_size
        if total > MAX_UNCOMPRESSED:
            raise ValueError("IPA uncompressed size exceeds limit")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("IPA contains a symbolic link")
    return members


def plist_string(info: dict, key: str) -> str:
    value = info.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Info.plist is missing {key}")
    return value


def contains_ytplus_marker(value: str) -> bool:
    lowered = value.lower()
    return "ytlite" in lowered or "ytplus" in lowered


def run_otool(executable: Path, *options: str) -> str:
    process = subprocess.run(
        ["otool", *options, str(executable)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if process.returncode != 0:
        rendered_options = " ".join(options)
        raise ValueError(f"otool {rendered_options} could not inspect the primary executable")
    return process.stdout


def imported_library_lines(output: str) -> list[str]:
    lines = output.splitlines()
    if len(lines) < 2:
        return []
    return [line.strip() for line in lines[1:] if line.strip()]


def validate_ios_arm64_executable(header: str, loads: str) -> None:
    if not re.search(r"\bARM64(?:E)?\b", header, re.IGNORECASE):
        raise ValueError("original primary executable has no arm64 slice")
    if not re.search(r"\bEXECUTE\b", header, re.IGNORECASE):
        raise ValueError("original primary Mach-O is not an executable")

    targets_ios = "LC_VERSION_MIN_IPHONEOS" in loads
    if not targets_ios:
        for block in re.split(r"(?=Load command [0-9]+)", loads):
            if "LC_BUILD_VERSION" not in block:
                continue
            if re.search(r"\bplatform\s+(?:IOS|2)\b", block, re.IGNORECASE):
                targets_ios = True
                break
    if not targets_ios:
        raise ValueError("original primary executable has no iOS platform load command")


def validate_original_macho(header: str, loads: str, libraries: list[str]) -> None:
    validate_ios_arm64_executable(header, loads)
    if any(contains_ytplus_marker(library) for library in libraries):
        raise ValueError("original primary executable loads a YTPlus library")


def extract_executable(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> Path:
    if member.file_size > MAX_EXECUTABLE:
        raise ValueError("primary executable exceeds size limit")
    executable_temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="ipa-executable-", delete=False) as handle:
            executable_temp = Path(handle.name)
            with archive.open(member) as source:
                written = 0
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_EXECUTABLE:
                        raise ValueError("primary executable exceeds size limit")
                    handle.write(chunk)
        return executable_temp
    except BaseException:
        if executable_temp is not None:
            executable_temp.unlink(missing_ok=True)
        raise


def inspect(args: argparse.Namespace) -> dict:
    size = args.ipa.stat().st_size
    if size < 10 * 1024 * 1024 or size > MAX_IPA:
        raise ValueError(f"IPA size {size} is outside the accepted range")

    try:
        archive = zipfile.ZipFile(args.ipa)
    except zipfile.BadZipFile as error:
        raise ValueError("IPA is not a valid ZIP") from error
    with archive:
        members = safe_members(archive)
        bad_member = archive.testzip()
        if bad_member is not None:
            raise ValueError("IPA ZIP integrity test failed")
        primary = [
            member
            for member in members
            if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", member.filename)
        ]
        if len(primary) != 1:
            raise ValueError(f"IPA must contain exactly one primary app, found {len(primary)}")
        if primary[0].file_size > MAX_PLIST:
            raise ValueError("primary Info.plist exceeds size limit")
        info = plistlib.loads(archive.read(primary[0]))
        if not isinstance(info, dict):
            raise ValueError("primary Info.plist is not a dictionary")

        app_root = primary[0].filename.removesuffix("Info.plist")
        executable = plist_string(info, "CFBundleExecutable")
        if "/" in executable or executable in (".", ".."):
            raise ValueError("invalid primary executable name")
        executable_path = app_root + executable
        executable_members = [member for member in members if member.filename == executable_path]
        if len(executable_members) != 1 or executable_members[0].file_size <= 0:
            raise ValueError("primary executable is missing or empty")

        bundle_id = plist_string(info, "CFBundleIdentifier")
        version = plist_string(info, "CFBundleShortVersionString")
        if bundle_id != args.expected_bundle:
            raise ValueError(f"bundle ID {bundle_id!r} does not match expected value")
        if version != args.expected_version:
            raise ValueError(f"version {version!r} does not match expected value")

        result = {
            "bundle_identifier": bundle_id,
            "version": version,
            "size": size,
            "executable": executable,
        }
        if args.mode == "original":
            if any(key in info for key in YTPLUS_METADATA_KEYS):
                raise ValueError("original IPA contains YTPlus metadata")
            if any(
                contains_ytplus_marker(member.filename)
                for member in members
            ):
                raise ValueError("original IPA contains a YTPlus path marker")
            if any(
                member.filename.startswith(app_root)
                and member.filename.endswith("/OpenYoutubeSafariExtension.appex/Info.plist")
                for member in members
            ):
                raise ValueError("original IPA contains OpenYoutubeSafariExtension.appex")

            executable_temp = extract_executable(archive, executable_members[0])
            try:
                header = run_otool(executable_temp, "-arch", "arm64", "-hv")
                loads = run_otool(executable_temp, "-arch", "arm64", "-l")
                libraries = imported_library_lines(run_otool(executable_temp, "-L"))
                validate_original_macho(header, loads, libraries)
            finally:
                executable_temp.unlink(missing_ok=True)
            result["original_youtube_version"] = version
            result["arm64_ios_executable"] = True
            return result

        display_name = info.get("CFBundleDisplayName", info.get("CFBundleName"))
        if display_name != args.expected_display_name:
            raise ValueError(f"display name {display_name!r} does not match expected value")
        checks = {
            "CFBundleVersion": args.expected_bundle_version,
            "YTPlusOriginalYouTubeVersion": args.expected_youtube_version,
            "YTPlusVersion": args.expected_ytplus_version,
            "YTPlusBuildID": args.expected_build_id,
        }
        for key, expected in checks.items():
            if info.get(key) != expected:
                raise ValueError(f"{key} does not match expected value")

        extensions = [
            member
            for member in members
            if member.filename.startswith(app_root)
            and re.search(r"/[^/]+\.appex/Info\.plist$", member.filename)
        ]
        if not any("OpenYoutubeSafariExtension.appex/Info.plist" in member.filename for member in extensions):
            raise ValueError("OpenYoutubeSafariExtension.appex is missing")

        executable_temp = extract_executable(archive, executable_members[0])
        try:
            header = run_otool(executable_temp, "-arch", "arm64", "-hv")
            loads = run_otool(executable_temp, "-arch", "arm64", "-l")
            validate_ios_arm64_executable(header, loads)
            libraries = imported_library_lines(run_otool(executable_temp, "-arch", "arm64", "-L"))
        finally:
            executable_temp.unlink(missing_ok=True)
        if not any(contains_ytplus_marker(library) for library in libraries):
            raise ValueError("primary executable does not load a YTPlus library")

        result.update(
            {
                "display_name": display_name,
                "bundle_version": info["CFBundleVersion"],
                "youtube_version": info["YTPlusOriginalYouTubeVersion"],
                "ytplus_version": info["YTPlusVersion"],
                "build_id": info["YTPlusBuildID"],
                "safari_extension": True,
                "ytplus_loaded": True,
            }
        )
        return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--ipa", type=Path, required=True)
    result.add_argument("--mode", choices=("original", "final"), required=True)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--expected-bundle", required=True)
    result.add_argument("--expected-version", required=True)
    result.add_argument("--expected-display-name")
    result.add_argument("--expected-youtube-version")
    result.add_argument("--expected-ytplus-version")
    result.add_argument("--expected-build-id")
    result.add_argument("--expected-bundle-version")
    return result


def main() -> None:
    args = parser().parse_args()
    if args.mode == "final":
        required = (
            args.expected_display_name,
            args.expected_youtube_version,
            args.expected_ytplus_version,
            args.expected_build_id,
            args.expected_bundle_version,
        )
        if any(value is None for value in required):
            raise SystemExit("final inspection requires all final expected values")
    try:
        result = inspect(args)
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as error:
        write_status(Path("work/status.json"), "validation_failed", str(error))
        print(f"IPA validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
