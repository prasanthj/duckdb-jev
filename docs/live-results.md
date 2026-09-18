# Real Jev evaluation and cache results

Native DuckDB v1.5.5 on macOS arm64; returned model `jev-1.13.0`. Run on 2026-09-18 UTC. All inference requests forwarded unchanged over HTTPS to the real TypeSafe endpoint through an instrumented loopback relay. No retries. Test data: nested synthetic support tickets from 12 templates. These are functional/throughput measurements, not production accuracy estimates.

## Validation

- Full regression suite: **91 passed**, including two opt-in live API smoke tests. Fault injection, concurrency accounting and deterministic edge cases use the local stub.
- Main live suite: **39 queries completed**, **1,325 requests**, **7,748 judgments**; every HTTP response 200.
- All main-suite outputs matched the fixture's routing/refund expectations or score tolerance (±0.75 rubric index). Repeated templates limit this result's generality.
- Cache suite: **36 additional requests**, **774 judgments**; validated 129 rows across six first-run/cached-repeat pairs. Every warm query had 129 cache hits and zero requests.
- Independent review: 52 cache/query/stream tests passed; no blocking findings remain.

## Median wall time for 100 unique rows

Three repetitions per configuration. SQL execution/fetch/order and relay overhead are included; input loading and output-file writing are excluded. Configurations ran in fixed order using pooled connections, so path differences should not be interpreted as isolated causal speedups.

| Path | Batch | Concurrency | HTTP requests | Median query time |
|---|---:|---:|---:|---:|
| Scalar Choice | 1 | 1 | 100 | 15.304s |
| Scalar Choice | 1 | 10 | 100 | 1.818s |
| Scalar Choice | 25 | 1 | 4 | 0.713s |
| Scalar Choice | 25 | 10 | 4 | 0.436s |
| Scalar Choice | 100 | 10 | 1 | 0.322s |
| Stream | 1 | 1 | 100 | 14.794s |
| Stream | 1 | 10 | 100 | 2.025s |
| Stream | 25 | 1 | 4 | 0.598s |
| Stream | 25 | 10 | 4 | 0.175s |
| Stream | 100 | 10 | 1 | 0.309s |

Per-HTTP-request p50/p95 over the main run: 137ms / 330ms. These combine different batch sizes and include network transport; not model compute time. Three trials do not establish a robust query p95.

For 2,049 unique rows at batch 100/concurrency 10: scalar 1.827s, stream 0.997s; both made 21 requests in this input-table scan. For 4,097 rows containing 10 unique inputs: one request and 4,087 reused rows on each path. Scalar with query cache disabled made 3 requests (chunk dedup remains enabled).

## Repeated-query cache: 129 rows

Batch 25/concurrency 10; three cold/warm pairs per path. Connection cache 8MiB, TTL 60s. Cache explicitly cleared before each cold query.

| Path | Cold median | Warm median | Cold/warm HTTP requests |
|---|---:|---:|---:|
| Scalar | 435.97ms | 7.20ms | 6 /0 |
| Stream | 245.38ms | 6.36ms | 6 /0 |

Offline Parquet reuse: **129/129 rows**, **8.11ms**, zero HTTP requests, new DuckDB connection with no extension loaded. Export includes input/spec fingerprints, model/confidence inside the result, and creation time; reuse applies an age check. Distributed AIDNN caching remains deferred.

## Usage and artifacts

Instrumented main+cache runs: **1,361 requests /8,522 judgments**. Provider-reported token totals: `{"input_tokens": 2488916, "output_tokens": 362697}`. The two separate smoke requests are excluded from those usage totals. Dollar cost is not inferred from token totals without verified billing rates.

- Main raw inputs/outputs, request ledger, timings and binary fingerprint: `benchmarks/results/live-20260918T061555790832Z`.
- Cache trials, request ledger, summary and reusable `enriched.parquet`: `benchmarks/results/live-cache-20260918T062635911271Z`.

Raw generated artifacts are local and git-ignored. This report and runnable harnesses are tracked. All runs terminate; no paid feed or background inference service remains.
