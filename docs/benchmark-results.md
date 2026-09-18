# Native extension local benchmark

local HTTP stub; not live Jev latency

1000 rows, 3 trials/configuration, 10.0 ms simulated server delay.

| Batch | Concurrency | Requests | Median query seconds | Median rows/s |
|---:|---:|---:|---:|---:|
| 1 | 1 | 1000 | 13.852 | 72 |
| 1 | 4 | 1000 | 3.482 | 287 |
| 1 | 10 | 1000 | 1.398 | 715 |
| 25 | 1 | 40 | 0.596 | 1679 |
| 25 | 4 | 40 | 0.151 | 6642 |
| 25 | 10 | 40 | 0.088 | 11356 |
| 100 | 1 | 10 | 0.143 | 6977 |
| 100 | 4 | 10 | 0.058 | 17275 |
| 100 | 10 | 10 | 0.057 | 17474 |

These measure native packing, HTTP and local scheduling, not semantic accuracy or TypeSafe service latency.
p95 is across whole-query trials; with three trials it is only the maximum observed, not a stable tail estimate.

Independent review: [performance-review.md](performance-review.md). All runs use native code on macOS arm64, DuckDB1.5.5. These trials preceded the final expanded-payload memory guard; the guard does not alter batching decisions for these small fixtures.

Functional batch invariance is tested against a deterministic stub, not established for arbitrary Jev judgments. Native live smoke: one request with Choice, Score and Noul passed; it does not support throughput claims.

## Streaming comparison

Reproduce with `uv run python -m benchmarks.stream`. Local HTTP stub only: 4097 unique rows, batch1000, concurrency10, 20ms simulated service delay, three trials/path, full result validation.

| Path | HTTP requests | Median query seconds |
|---|---:|---:|
| Scalar chunks | 7 | 0.1217 |
| Streaming | 5 | 0.0648 |

The request-count reduction demonstrates cross-chunk packing. These timings do not measure live TypeSafe latency and do not promise production speedups. Raw results: `benchmarks/results/stream-20260918T060615962191Z/results.json` (local generated artifact, ignored by git).
