#!/usr/bin/env bash
set -Eeuo pipefail
set +x

exec python3 "$(dirname "$0")/download-armconverter.py"
