#!/usr/bin/env bash
# Build the Lambda zip the Terraform compute resources expect.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
stage="$root/dist/lambda"
rm -rf "$stage"
mkdir -p "$stage"
python3 -m pip install "$root" -t "$stage"
python3 - "$stage" "$root/dist/lambda.zip" <<'PY'
import sys
import zipfile
from pathlib import Path

stage = Path(sys.argv[1])
target = Path(sys.argv[2])
with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in stage.rglob("*"):
        if path.is_file():
            archive.write(path, path.relative_to(stage).as_posix())
PY
echo "wrote $root/dist/lambda.zip"
