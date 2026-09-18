# Independent performance review

Reviewed 2026-09-17 by the separate performance reviewer agent. This includes
source review and an independent execution of the deterministic test suite; no paid model
requests were made. Source locations below use function names because the source
was being formatted during review.

## Improvements confirmed on second review

- `Evaluate` now packs pre-serialized question fragments into a growing string.
  The earlier repeated whole-request copying and serialization was quadratic in
  batch size; the revised approach is linear in serialized request bytes.
- `HttpPool` has ten persistent workers with worker-owned CURL handles. Handle
  reset preserves CURL connection caches, allowing reuse across DuckDB chunks.
- Unified-vector validity checks avoid constructing nested `Value` objects just
  to check SQL NULL. Constant instructions and criteria have cached documents.
- `Gate` enforces both a process-wide limit of ten HTTP requests and the configured
  per-client-context limit. DuckDB scan parallelism cannot multiply those limits.
- Batch response IDs, rather than arrival order, determine row/question mapping.
  Validation and answer merging are protected by a mutex. No result race or
  ordinary-path deadlock was identified in this inspection.

## Resolved findings

1. **NULL short-circuit regression from eager constant parsing.** `Evaluate`
   eagerly parses constant criteria before determining whether any row has all
   non-NULL arguments. `jev_choice(NULL::VARCHAR, 'p', 'not-json')` should return
   NULL under the stated contract but can throw. Parse constant documents lazily
   after a valid row is found and add the regression case.
2. **Partial thread-pool construction must unwind safely.** If thread creation
   throws after some workers have started, destroying joinable `std::thread`
   objects terminates the host process. Constructor failure needs a closing
   signal, notifications and joins before rethrowing.
3. **Low-concurrency clients can occupy all worker slots.** Pool workers currently
   dequeue tasks and then wait inside the per-context gate. Ten tasks from a
   concurrency-one context can occupy the pool with one active request and nine
   waiters, delaying another eligible client. This is head-of-line blocking, not
   an observed deadlock. A scheduler that admits eligible contexts before
   allocating workers is the stronger design for mixed concurrent workloads.
4. **Reserve future storage before submitting reference-capturing tasks.** In
   `futures.push_back(Pool().Submit(...))`, submission can succeed before vector
   allocation fails. The exception path then waits only for futures already
   retained, potentially leaving the newly submitted task referring to destroyed
   stack variables. Reserving `packs.size()` entries before the first submission
   removes this allocation-failure lifetime gap.

All four findings above describe the earlier review snapshot and are now resolved:
constant documents are parsed lazily after NULL checks; partial construction
stops and joins started workers; `HttpPool::Runnable` admits only eligible
contexts; and future capacity is reserved before the first submission. These
fixes were independently inspected. The NULL and scheduler cases also have
passing regression tests. Allocation-failure paths were inspected, not fault
injected.

## Packed-payload budget finding resolved

The 32MiB unique-evidence budget does not bound packed request payloads: evidence
is repeated once per question, and every serialized batch remains in memory until
submission. A large evidence row with hundreds of questions can multiply its
size substantially. The implementation now separately accounts for serialized
payload fragments, commas, request prefixes and closing braces, enforcing a
32MiB cumulative packed-request budget before appending. The added
`test_expanded_payload_budget` exercises 500KB evidence repeated across 100
questions and requires failure before any HTTP call. The guard and test were
independently inspected; the implementation owner is running the rebuilt suite.

No blocking findings remain from this review. The limits below still apply.

## Validation assessed

The test suite includes exact request-count reduction for batch sizes 1, 25, 100
and 1000; concurrency levels 1, 4 and 10; a multi-connection global ceiling;
multi-chunk selection/order; HTTP/1.1 connection reuse; nested evidence; mixed
primitives; shuffled response IDs; escaped Unicode byte budgets; and provider
failure handling. These are useful behavioral assertions rather than timing-only
tests.

Independently executed:

```text
uv run pytest -q tests/test_performance.py tests/test_extension.py
32 passed in 21.32s
```

This includes interruption, timeout, and malformed-response cases with queued
work, NULL evidence with invalid constant criteria, and simultaneous
low-concurrency/high-concurrency contexts. Cancellation and timeout checks also
verify successful subsequent queries. Tests use only the local HTTP fixture.

## Limits to communicate

- Deduplication is chunk-local. Repeated evidence across chunk boundaries or
  queries can trigger additional inference; this is not a persistent cache.
- Nested evidence still uses row-oriented conversion and allocation. The native
  function consumes DuckDB chunks and batches HTTP requests, but it should not
  be described as allocation-free vectorized serialization.
- Rows and their questions are collected before final request packing. The
  request-byte limit bounds each HTTP payload, while separate 32MiB guards cap
  unique serialized evidence and cumulative serialized requests per chunk.
  These are not 32MiB process-RSS guarantees: JSON objects, returned answers,
  temporary copies, DuckDB vectors and concurrent chunks use additional memory.
- A serialized byte cap is not an exact token-budget guarantee. Provider context
  rejection must remain an explicit error; never truncate silently.
- Local fixture timing measures the extension and test server, not TypeSafe
  production latency. Report total query wall time and batch/request counts;
  do not call amortized time per row individual-request latency.
- Correct request isolation on the wire is not proof of semantic batch-size
  invariance. A separately budgeted live corpus is needed to compare decisions
  with different neighboring rows and batch sizes.
