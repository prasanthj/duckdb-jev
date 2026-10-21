## Unreleased

- Add DuckDB 2.0.0 builds, SQL regression tests, and native release targets alongside 1.4.5 and 1.5.6.
- Adapt to DuckDB 2.0's identifier and function APIs and checksum-pinned tar.gz library archives.

## v0.2.2

Native Jev extension v0.2.2 for DuckDB **1.4.5** and **1.5.6**.

- Semantic predicates, finite-choice classification, rubric scoring, and mixed question evaluation from SQL.
- Bounded batching and concurrent HTTP transport, with streaming relational input.
- Query-local reuse, opt-in connection caching, and Parquet reuse examples.
- DuckDB secret-backed credentials with an environment fallback.
- Bounded retries, per-query request/question budgets, and process-level usage counters.
- macOS and Linux archives for x86-64 and ARM64, with compatibility manifests, SPDX SBOMs, SHA-256 checksums, and GitHub provenance attestations.

Each platform runs the native regression suite using local HTTP fixtures before publication. Paid live inference is excluded from release CI. Binaries are unsigned and require an exact DuckDB version/platform match. See the packaged DISTRIBUTION.md for runtime dependencies and loading instructions.
