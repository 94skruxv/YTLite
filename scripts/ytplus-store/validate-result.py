#!/usr/bin/env python3
"""Verify an artifact directory before upload; the Pi repeats these checks."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "result")
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    if result.get("status") != "success":
        if {path.name for path in root.iterdir()} != {"result.json"}:
            raise SystemExit("failed result must contain only result.json")
        return
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest != result:
        raise SystemExit("manifest.json and result.json differ")
    filename = manifest.get("ipa_filename", "")
    if not re.fullmatch(r"YouTubePlus_YTP-[0-9.]+_YT-[0-9.]+\.ipa", filename):
        raise SystemExit("manifest IPA filename is unsafe")
    ipa = root / filename
    if ipa.stat().st_size != manifest.get("size"):
        raise SystemExit("manifest IPA size mismatch")
    if digest(ipa) != manifest.get("sha256"):
        raise SystemExit("manifest IPA SHA-256 mismatch")
    original_filename = manifest.get("original_ipa_filename", "")
    if not re.fullmatch(r"YouTube_YT-[0-9.]+\.ipa", original_filename):
        raise SystemExit("manifest original IPA filename is unsafe")
    original_ipa = root / original_filename
    if original_ipa.stat().st_size != manifest.get("original_size"):
        raise SystemExit("manifest original IPA size mismatch")
    if digest(original_ipa) != manifest.get("original_sha256"):
        raise SystemExit("manifest original IPA SHA-256 mismatch")
    expected_checksums = (
        f"{manifest['sha256']}  {filename}\n"
        f"{manifest['original_sha256']}  {original_filename}\n"
    )
    if (root / "sha256.txt").read_text(encoding="utf-8") != expected_checksums:
        raise SystemExit("sha256.txt does not match manifest")
    expected_files = {"result.json", "manifest.json", "sha256.txt", filename, original_filename}
    if {path.name for path in root.iterdir()} != expected_files:
        raise SystemExit("result contains unexpected files")


if __name__ == "__main__":
    main()
