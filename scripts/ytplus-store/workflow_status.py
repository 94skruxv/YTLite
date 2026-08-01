#!/usr/bin/env python3
"""Maintain the small non-secret status file shared by workflow steps."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

ALLOWED = {
    "success",
    "waiting_for_ipa",
    "armconverter_auth_failed",
    "upstream_asset_missing",
    "download_failed",
    "build_failed",
    "validation_failed",
}


def write_status(path: Path, status: str, message: str) -> None:
    if status not in ALLOWED:
        raise ValueError(f"unsupported workflow status: {status}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "message": message[:500]}
    descriptor, temporary = tempfile.mkstemp(prefix=".status-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_status(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    status = payload.get("status")
    if status not in ALLOWED:
        raise ValueError(f"invalid workflow status in {path}: {status!r}")
    return {"status": status, "message": str(payload.get("message", ""))[:500]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("status", choices=sorted(ALLOWED))
    parser.add_argument("message")
    parser.add_argument("--path", type=Path, default=Path("work/status.json"))
    args = parser.parse_args()
    write_status(args.path, args.status, args.message)


if __name__ == "__main__":
    main()
