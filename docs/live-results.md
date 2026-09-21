# Real Jev evaluation and cache results

Native DuckDB v1.5.5 on macOS arm64; returned model `jev-1.13.0`. Main benchmark run on 2026-09-21 UTC, with batch-equivalence verification repeated on 2026-09-20. All inference requests used the real TypeSafe endpoint. Test data: nested synthetic support tickets from 12 templates. These are functional/throughput measurements, not production accuracy estimates.

![Live Jev batching performance](images/live-performance.svg)

## Validation

- Full deterministic regression suite: **109 passed, 3 skipped**. Three opt-in live API tests also passed. Fault injection, concurrency accounting and deterministic edge cases use the local stub.
- Main live suite: **39 queries completed**, **1,327 requests**, **7,750 judgments**; 1,325 responses were HTTP 200. Two transient 502 responses during one one-row trial were retried successfully by the extension.
- All main-suite outputs matched the fixture's routing/refund expectations or score tolerance (±0.75 rubric index). Repeated templates limit this result's generality.
- Cache suite: **36 additional requests**, **774 judgments**; validated 129 rows across six first-run/cached-repeat pairs. Every warm query had 129 cache hits and zero requests.
- Independent review: 52 cache/query/stream tests passed; no blocking findings remain.

## Batch-equivalence check

The live equivalence test evaluated 12 nested inputs with Choice, Score and Noul questions at batch sizes 1, 10, 25 and 100. All Choice labels and Noul threshold decisions matched the batch-1 baseline. The largest numeric difference in a confidence, probability or score field was 0.13. A repeated batch-1 control differed by as much as 0.10, confirming that small numeric movement is normal live-model variation rather than evidence of cross-row contamination. The test fails on any Choice or Noul-decision mismatch, a model change, or a numeric delta above 0.15.

## Median wall time for 100 unique rows

Three repetitions per configuration. SQL execution/fetch/order and relay overhead are included; input loading and output-file writing are excluded. Configurations ran in fixed order using pooled connections, so path differences should not be interpreted as isolated causal speedups.

| Path | Batch | Concurrency | HTTP requests | Median query time |
|---|---:|---:|---:|---:|
| Scalar Choice | 1 | 1 | 100 | 15.176s |
| Scalar Choice | 1 | 10 | 100 | 1.635s |
| Scalar Choice | 25 | 1 | 4 | 0.797s |
| Scalar Choice | 25 | 10 | 4 | 0.244s |
| Scalar Choice | 100 | 10 | 1 | 0.329s |
| Stream | 1 | 1 | 100–102 | 16.828s |
| Stream | 1 | 10 | 100 | 1.537s |
| Stream | 25 | 1 | 4 | 0.793s |
| Stream | 25 | 10 | 4 | 0.211s |
| Stream | 100 | 10 | 1 | 0.329s |

Per-HTTP-request p50/p95 over the main run: 147ms / 281ms. These combine different batch sizes and include network transport; not model compute time. Three trials do not establish a robust query p95.

For 2,049 unique rows at batch 100/concurrency 10: scalar 1.328s, stream 0.991s; both made 21 requests in this input-table scan. For 4,097 rows containing 10 unique inputs: one request and 4,087 reused rows on each path. Scalar with query cache disabled made 3 requests (chunk dedup remains enabled).

## 1,000-row Choice scaling run

Three fresh repetitions used `jev_stream` with one finite-label Choice question per unique nested JSON row. All expected labels matched, and all 150 API responses were HTTP 200.

| Batch | Concurrency | Requests/query | Median query time | Median throughput |
|---:|---:|---:|---:|---:|
| 25 | 10 | 40 | 0.964s | 1,037 rows/s |
| 100 | 10 | 10 | 0.515s | 1,943 rows/s |

Batch 100 creates exactly ten requests for 1,000 rows, allowing the configured ten-way HTTP concurrency to be fully occupied without additional waves.

## Repeated-query cache: 129 rows

Batch 25/concurrency 10; three cold/warm pairs per path. Connection cache 8MiB, TTL 60s. Cache explicitly cleared before each cold query.

| Path | Cold median | Warm median | Cold/warm HTTP requests |
|---|---:|---:|---:|
| Scalar | 435.97ms | 7.20ms | 6 /0 |
| Stream | 245.38ms | 6.36ms | 6 /0 |

Offline Parquet reuse: **129/129 rows**, **8.11ms**, zero HTTP requests, new DuckDB connection with no extension loaded. Export includes input/spec fingerprints, model/confidence inside the result, and creation time; reuse applies an age check. Distributed caching remains outside this implementation.

## Usage and artifacts

The fresh main run used **1,327 requests /7,750 judgments**. Provider-reported token totals: `{"input_tokens": 2284256, "output_tokens": 328857}`. Cache results below come from the separate 2026-09-18 run. Dollar cost is not inferred from token totals without verified billing rates.

- Main raw inputs/outputs, request ledger, timings and binary fingerprint: `benchmarks/results/live-20260921T030147262719Z`.
- 1,000-row Choice scaling trials: `benchmarks/results/live-scale-20260921T032315156626Z`.
- Cache trials, request ledger, summary and reusable `enriched.parquet`: `benchmarks/results/live-cache-20260918T062635911271Z`.

Raw generated artifacts are local and git-ignored. This report and runnable harnesses are tracked. All runs terminate; no paid feed or background inference service remains.
