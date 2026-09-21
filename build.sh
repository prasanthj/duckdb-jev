#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --frozen --python 3.11
duckdb_version="${DUCKDB_VERSION:-1.5.5}"
case "$duckdb_version" in
  1.4.5) source_commit=f31be57c1845a8895169fd58142040be26d433cf ;;
  1.5.5) source_commit=d8cdaa33fda8df955cc76ef58a280f68f4cd43fa ;;
  *) echo "Unsupported DuckDB version: $duckdb_version (supported: 1.4.5, 1.5.5)"; exit 1 ;;
esac

source_dir="vendor/duckdb-$duckdb_version"
if [ ! -d "$source_dir" ]; then
  git clone --depth 1 --branch "v$duckdb_version" https://github.com/duckdb/duckdb.git "$source_dir"
fi
[ "$(git -C "$source_dir" rev-parse HEAD)" = "$source_commit" ] || {
  echo "DuckDB source must match pinned v$duckdb_version"
  exit 1
}

case "$duckdb_version-$(uname -s)-$(uname -m)" in
  1.4.5-Linux-x86_64) platform=linux-amd64; archive_sha=1edaf33c5a74973e191479ba11a71eadc575884fbafbaf7b14c7f9e5989bcee0 ;;
  1.4.5-Linux-aarch64|1.4.5-Linux-arm64) platform=linux-arm64; archive_sha=33f1874c87d31ada3fd57acdd610f6707ad732782d3960a71f395f79c36eb703 ;;
  1.4.5-Darwin-x86_64) platform=osx-amd64; archive_sha=ca7742d71cf586a5ad3165056b6eab95298243ea7367a33d1f335dd0929d25e3 ;;
  1.4.5-Darwin-arm64) platform=osx-arm64; archive_sha=09dff4ba1958f74dbf1688b2ca901a517716a2a329e49759a13e5d6ec14e50fe ;;
  1.5.5-Linux-x86_64) platform=linux-amd64; archive_sha=deb47c5300f3c99725e84cdb14d214c3b12bbd748b613b1698b938c894cb68eb ;;
  1.5.5-Linux-aarch64|1.5.5-Linux-arm64) platform=linux-arm64; archive_sha=ea6a34cb49ec2db5ed23d9e8311237c53c32abf9cdbf5dd608c4176c3dd8bfeb ;;
  1.5.5-Darwin-x86_64) platform=osx-amd64; archive_sha=a27d36fa1247a3ffa1692e7aa0bf4ea4d1e0ee51da7c4df7a5db5217357b1b4d ;;
  1.5.5-Darwin-arm64) platform=osx-arm64; archive_sha=d79ec66b8a4054b866faada82e9e31f859a713c555b3f1c4b71c4a43d3273e9c ;;
  *) echo "Unsupported native platform: $(uname -s)-$(uname -m)"; exit 1 ;;
esac

static_dir="vendor/duckdb-static/$duckdb_version/$platform"
archive="$static_dir.zip"
if [ ! -f "$static_dir/libduckdb_static.a" ]; then
  mkdir -p "$(dirname "$static_dir")"
  curl --fail --location --retry 3 --output "$archive" \
    "https://github.com/duckdb/duckdb/releases/download/v$duckdb_version/static-libs-$platform.zip"
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

build_dir="${JEV_BUILD_DIR:-build}"
version_marker="$build_dir/.jev-duckdb-version"
if [ -d "$build_dir" ] && [ "$(cat "$version_marker" 2>/dev/null || true)" != "$duckdb_version" ]; then
  rm -rf "$build_dir"
fi
legacy_interrupt=OFF
[ "$duckdb_version" = 1.4.5 ] && legacy_interrupt=ON

uv run cmake -S "$source_dir" -B "$build_dir" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=17 \
  -DBUILD_UNITTESTS=OFF -DBUILD_SHELL=OFF -DDISABLE_UNITY=OFF -DENABLE_JEMALLOC=OFF \
  -DEXTENSION_STATIC_BUILD=OFF -DJEV_PREBUILT_DUCKDB_STATIC_DIR="$PWD/$static_dir" \
  -DJEV_DUCKDB_1_4="$legacy_interrupt" \
  -DDUCKDB_EXTENSION_CONFIGS="$PWD/extension_config.cmake" "$@"
printf '%s\n' "$duckdb_version" > "$version_marker"
uv run cmake --build "$build_dir" --target jev_loadable_extension --parallel "${JEV_BUILD_JOBS:-4}"
