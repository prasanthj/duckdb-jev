Native Jev extension for DuckDB **1.5.5**.

- Semantic predicates, finite-choice classification, rubric scoring, and mixed question evaluation from SQL.
- Bounded batching and concurrent HTTP transport, with streaming relational input.
- Query-local reuse, opt-in connection caching, and Parquet reuse examples.
- macOS and Linux archives for x86-64 and ARM64, with compatibility manifests and SHA-256 checksums.

Each platform runs the native regression suite using local HTTP fixtures before publication. Paid live inference is excluded from release CI. Binaries are unsigned and require an exact DuckDB version/platform match. See the packaged DISTRIBUTION.md for runtime dependencies and loading instructions.
