#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
uv sync --frozen
if [ ! -d vendor/duckdb ]; then
  git clone --depth 1 --branch v1.5.5 https://github.com/duckdb/duckdb.git vendor/duckdb
fi
[ "$(git -C vendor/duckdb rev-parse HEAD)" = d8cdaa33fda8df955cc76ef58a280f68f4cd43fa ] || { echo 'DuckDB source must match pinned v1.5.5'; exit 1; }
uv run cmake -S vendor/duckdb -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_UNITTESTS=OFF -DBUILD_SHELL=OFF -DDISABLE_UNITY=ON \
  -DDUCKDB_EXTENSION_CONFIGS="$PWD/extension_config.cmake"
uv run cmake --build build --target jev_loadable_extension --parallel 4
