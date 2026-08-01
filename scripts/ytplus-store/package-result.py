#!/usr/bin/env python3
"""Create the short-lived artifact consumed and revalidated by the Pi."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from workflow_status import read_status, write_status


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def package() -> None:
    result_dir = Path("result")
    if result_dir.exists():
        shutil.rmtree(result_dir)
    result_dir.mkdir()
    status = read_status(Path("work/status.json"))
    inputs = load_json(Path("work/inputs.json")) if Path("work/inputs.json").exists() else {}
    built_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if status["status"] != "success":
        payload = {
            "status": status["status"],
            "message": status["message"],
            "build_id": inputs.get("build_id", "unknown"),
            "built_at": built_at,
        }
        (result_dir / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    upstream = load_json(Path("work/upstream.json"))
    inspection = load_json(Path("work/final-inspection.json"))
    source = Path("work/YouTubePlus.ipa")
    original_source = Path("work/youtube-original.ipa")
    if not source.is_file():
        raise ValueError("successful workflow has no final IPA")
    if not original_source.is_file():
        raise ValueError("successful workflow has no original IPA")
    filename = f"YouTubePlus_{inputs['build_id']}.ipa"
    destination = result_dir / filename
    shutil.copyfile(source, destination)
    digest = sha256(destination)
    original_filename = f"YouTube_YT-{inputs['youtube_version']}.ipa"
    original_destination = result_dir / original_filename
    shutil.copyfile(original_source, original_destination)
    original_digest = sha256(original_destination)
    payload = {
        "status": "success",
        "build_id": inputs["build_id"],
        "upstream_repository": upstream["upstream_repository"],
        "upstream_tag": upstream["upstream_tag"],
        "upstream_release_id": upstream["upstream_release_id"],
        "ytplus_version": inputs["ytplus_version"],
        "youtube_version": inputs["youtube_version"],
        "store_version": inputs["store_version"],
        "bundle_version": inspection["bundle_version"],
        "bundle_identifier": inspection["bundle_identifier"],
        "display_name": inspection["display_name"],
        "ipa_filename": filename,
        "sha256": digest,
        "size": destination.stat().st_size,
        "original_ipa_filename": original_filename,
        "original_sha256": original_digest,
        "original_size": original_destination.stat().st_size,
        "built_at": built_at,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    (result_dir / "manifest.json").write_text(encoded, encoding="utf-8")
    (result_dir / "result.json").write_text(encoded, encoding="utf-8")
    (result_dir / "sha256.txt").write_text(
        f"{digest}  {filename}\n{original_digest}  {original_filename}\n",
        encoding="utf-8",
    )


def main() -> None:
    try:
        package()
    except Exception as error:
        write_status(Path("work/status.json"), "validation_failed", str(error))
        result_dir = Path("result")
        if result_dir.exists():
            shutil.rmtree(result_dir)
        result_dir.mkdir()
        payload = {"status": "validation_failed", "message": str(error)[:500]}
        (result_dir / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"result packaging failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
