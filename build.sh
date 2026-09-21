#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --frozen --python 3.11
if [ ! -d vendor/duckdb ]; then
  git clone --depth 1 --branch v1.5.5 https://github.com/duckdb/duckdb.git vendor/duckdb
fi
[ "$(git -C vendor/duckdb rev-parse HEAD)" = d8cdaa33fda8df955cc76ef58a280f68f4cd43fa ] || { echo 'DuckDB source must match pinned v1.5.5'; exit 1; }

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) platform=linux-amd64; archive_sha=deb47c5300f3c99725e84cdb14d214c3b12bbd748b613b1698b938c894cb68eb ;;
  Linux-aarch64|Linux-arm64) platform=linux-arm64; archive_sha=ea6a34cb49ec2db5ed23d9e8311237c53c32abf9cdbf5dd608c4176c3dd8bfeb ;;
  Darwin-x86_64) platform=osx-amd64; archive_sha=a27d36fa1247a3ffa1692e7aa0bf4ea4d1e0ee51da7c4df7a5db5217357b1b4d ;;
  Darwin-arm64) platform=osx-arm64; archive_sha=d79ec66b8a4054b866faada82e9e31f859a713c555b3f1c4b71c4a43d3273e9c ;;
  *) echo "Unsupported native platform: $(uname -s)-$(uname -m)"; exit 1 ;;
esac

static_dir="vendor/duckdb-static/$platform"
archive="$static_dir.zip"
if [ ! -f "$static_dir/libduckdb_static.a" ]; then
  mkdir -p "$(dirname "$static_dir")"
  curl --fail --location --retry 3 --output "$archive" \
    "https://github.com/duckdb/duckdb/releases/download/v1.5.5/static-libs-$platform.zip"
  ARCHIVE="$archive" EXPECTED_SHA="$archive_sha" OUTPUT="$static_dir" uv run python - <<'PY'
import hashlib
import os
import zipfile
from pathlib import Path

archive = Path(os.environ["ARCHIVE"])
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual != os.environ["EXPECTED_SHA"]:
    raise SystemExit(f"DuckDB static library checksum mismatch: {actual}")
output = Path(os.environ["OUTPUT"])
output.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as package:
    package.extractall(output)
PY
fi

uv run cmake -S vendor/duckdb -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=17 \
  -DBUILD_UNITTESTS=OFF -DBUILD_SHELL=OFF -DDISABLE_UNITY=OFF -DENABLE_JEMALLOC=OFF \
  -DEXTENSION_STATIC_BUILD=OFF -DJEV_PREBUILT_DUCKDB_STATIC_DIR="$PWD/$static_dir" \
  -DDUCKDB_EXTENSION_CONFIGS="$PWD/extension_config.cmake" "$@"
uv run cmake --build build --target jev_loadable_extension --parallel "${JEV_BUILD_JOBS:-4}"
