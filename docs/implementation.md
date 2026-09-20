# Implementation checkpoints

Implementation is now available; see README.md for the exact implemented surface and limits. This document preserves the staged plan; it is not a completion checklist.

1. Native registration spike: scaffold official C++ template, pin released DuckDB, register a no-network vectorized function returning nested STRUCT/MAP. Compile/load through CLI and Python. Verify constant/dictionary/flat vectors, validity masks, row order, multiple chunks and EXPLAIN no-execution. Add uv-managed Python test dependencies and a reproducible native build script; do not replace the native artifact with a Python UDF.
2. Transport and codec: typed request/response structs, pooled TLS client, bounded executor, injectable local HTTP stub. Validate Choice up to 255 options, Score 2–10 levels, Noul optional criteria, nested instructions and mixed questions. Test unknown model, malformed response, missing answer, NaN/out-of-range distributions, all-null/empty inputs and request splitting. Preserve numerical evidence and duplicate row multiplicity.
3. SQL primitives: typed functions and generic eval, no-network binding validation, snapshot config, credentials and external-access restrictions. Test shared concurrency ceiling across multiple DuckDB workers. Cancellation, statement failures and provider timeouts must not leak workers or keep scheduling requests.
4. Query-local memoization: canonical keys, duplicate coalescing, no caching errors, bounded memory, key/model/policy isolation, request metrics. Validate SELECT+WHERE and repeated expression behavior; require materialization where one evaluation per input is needed.
5. Opt-in real integration: a tiny fixed evidence corpus, one bounded run covering all primitives and structured descriptions. Credentials never in fixtures. Budget enforced and retries off. Compare SDK and native request/response contracts before performance work.
6. Performance and packaging: cold and warm HTTP, explicit no-cache/cache cases, single-row and packed requests, independent concurrency 1/4/10, token usage and wall time. Compare batch-size invariance and neighboring-row contamination. Report p50/p95 per request plus total query duration, not amortized per-row latency. Build macOS arm64 and Linux amd64 release-specific artifacts first; unsigned local loading only for development. Community distribution/signing is a later release step, not automatic publication.

Useful evaluation fixtures: support sentiment and renewal risk; closed-taxonomy intent classification; evidence verification; entity resolution over pre-retrieved KB candidate IDs. Keep these separate from function-correctness tests. A successful SQL function is not evidence that every semantic classification is correct.

## Follow-up: bounded query reuse

Implemented completed-result memoization shared by expressions/workers within a query, with QueryEnd cleanup and a first-use configuration snapshot. Defaults: 8MiB serialized keys/results, 4096 entries; overflow bypasses insertion. Constant evidence is converted once per chunk. Concurrent misses are not coalesced. Streaming cross-chunk pipelining remains a separate operator/table-function design; synchronous scalar callbacks cannot return before filling their output vectors.

## Follow-up: in-flight sharing and streaming

Implemented query-local Flight promises with atomic cache/in-flight lookup, bounded admission, owner-failure propagation and publish-before-wait ordering. Implemented `jev_stream(TABLE)` as a native table-in/out operator with a single input producer, partial packs spanning chunks, concurrent HTTP jobs, ready-prefix output and explicit backpressure. Independent performance review and deterministic lifecycle/memory/batch tests cover this path.
