# Jev in DuckDB — proposed design

Status: proposed, for discussion. Researched 2026-09-17. This document distinguishes verified provider capabilities from extension design choices.

## Goal and deployment

Make semantic predicates, classification and scoring available beside ordinary relational operations. SQL first reduces candidate rows with exact filters/joins, then Jev judges the selected evidence. This is remote inference; data is sent to TypeSafe. It is not a local model or an index, and scan cost grows with uncached evidence.

Recommend a native C++ extension targeting released DuckDB v1.5.5 initially. It runs through DuckDB CLI and language clients without a Python interpreter. Use DuckDB's official C++ extension template and libcurl for pooled TLS HTTP. Python via uv is for fixture tooling and integration tests, not production inference. Pin DuckDB and toolchain revisions in the implementation.

A Python Arrow UDF is useful for prototyping request packing, but requires registration in a Python process and is not an installable native extension. Do not present it as the requested deliverable. The official C extension template avoids building DuckDB, but is marked experimental. Current main headers include scalar bind/init APIs marked stable in 1.5.6, beyond the latest released 1.5.5 observed today. Do not depend on unreleased APIs accidentally. C++ offers the needed binder, connection state, configuration and secret integration at the cost of building per supported DuckDB version/platform.

## Verified TypeSafe surface

`POST https://api.typesafe.ai/v1/systemone`, bearer authentication. Request: `state`, `model`, and a map of `questions`. Returned answer IDs match question IDs; IDs themselves are not semantic instructions. State and instructions accept text or JSON objects/arrays.

| Primitive | Criteria | Answer |
|---|---|---|
| Choice | Named option map; up to 255 options; descriptions may be structured per primitive docs | choice, probabilities, confidence |
| Score | Ordered rubric, 2–10 levels | fractional probability-weighted score, legend, probabilities, confidence |
| Noul | Optional yes/no descriptions | probability of yes, `noul`, between 0 and 1 |

Confidence is derived from the probability distribution. It is not necessarily the probability assigned to the selected label. Noul has no separate confidence. Score is not automatically a 0–100 score; its range comes from rubric indices.

Mixed primitives can be sent in one request. Questions are independent and share state. Dependent judgments require a second call. Documentation describes an approximately 32K-token combined context budget; do not assume a hard 100-question API maximum. Our row/question/byte caps are extension controls, not model limits.

Documented request-level model control is `model`. Timeout, retry policy, base URL and headers are client/transport controls. Do not invent temperature, reasoning-effort or token-generation knobs. API reference and richer primitive/SDK pages differ in their descriptions of structured criteria: implement from tested wire fixtures, and probe richer forms before claiming support.

## Proposed SQL contract

All functions accept explicit evidence, never implicitly discover or export a whole database. Canonical input is JSON; offer VARCHAR text and generic STRUCT overloads that preserve nested values through a shared serializer. Reject unsupported serialization such as non-finite numeric evidence with an actionable error. Whole SQL NULL input returns SQL NULL without a network request; nested null fields remain evidence. Define JSON null explicitly as an invalid top-level state rather than silently treating it as an empty object.

Initial signatures (overloads, not arbitrary SQL named arguments):

```
jev_noul(evidence, instructions [, criteria_json])
  -> STRUCT(noul DOUBLE, model VARCHAR, cache_hit BOOLEAN)

jev_choice(evidence, instructions, criteria_json)
  -> STRUCT(choice VARCHAR, confidence DOUBLE,
            probabilities MAP(VARCHAR, DOUBLE), model VARCHAR, cache_hit BOOLEAN)

jev_score(evidence, instructions, levels_json)
  -> STRUCT(score DOUBLE, confidence DOUBLE, probabilities MAP(VARCHAR, DOUBLE),
            legend JSON, model VARCHAR, cache_hit BOOLEAN)

jev_eval(evidence, questions_json)
  -> STRUCT(answers JSON, model VARCHAR, cache_hit BOOLEAN)

jev(evidence, predicate VARCHAR, threshold DOUBLE) -> BOOLEAN
```

`jev` is convenience over Noul, with an explicit caller threshold. Proposed comparison is `noul >= threshold`, threshold in [0,1]. Three arguments avoid burying a policy decision in an implicit 0.5 default. A two-argument overload can be added if desired, with a documented default.

Keep detailed result structs as the primary API so callers can inspect uncertainty. `jev_eval` preserves every named answer and is the preferred path when one row needs several judgments: risk, sentiment and escalation in one call. Separate calls to `jev_choice` and `jev_score` are NOT promised to fuse into one network call. Arrays/maps/objects inside instructions and criteria are available through JSON overloads. Plain VARCHAR instructions stay text; do not heuristically parse JSON-looking prose.

Use SQL to normalize scores, threshold probabilities, map choices to actions, and join selected KB entity IDs back to authoritative attributes. Add explicit `unknown`/`no_match` choices when appropriate. The model does not invent options or retrieve parent-company evidence by itself.

## Vectorized execution and HTTP scheduling

1. Bind validates static schemas, criteria and configuration without network calls. Register remote functions as volatile/side-effecting so constants are not evaluated during binding/planning. Ordinary EXPLAIN must not send requests; EXPLAIN ANALYZE executes work.
2. Receive DuckDB data chunks; respect validity and selection vectors and restore output order. Partition by model/question specification; canonicalize duplicate evidence/spec combinations for local deduplication.
3. Pack multiple independent row questions per HTTP request within configurable caps. Use explicit row evidence in each question's structured instructions and a shared policy in state, following the existing Jev demo pattern. Never depend on IDs to communicate which row to inspect. Also support row-only state with all questions for a row for the single-row/mixed-question path.
4. Grouping must be validated for semantic isolation: other rows are still in the same request. Check batch-size invariance and conflicting-neighbor fixtures. If results depend on neighbors, fall back to one row per call for that workload.
5. Split on serialized size and question count, not merely DuckDB vector size. Start with 25 row-question units and 64KiB request budget as conservative configurable controls, not a proof of token fit. Never truncate evidence silently. Oversized single rows fail with a specific error; explicit row-level error mode may preserve them for review.
6. Reuse HTTP connections. Bound total in-flight calls across DuckDB workers with a shared executor/semaphore scoped to the database/extension instance. Initial proposed concurrency 10; one batch size and one concurrency budget. Do not multiply ten requests by every DuckDB worker.
7. Map each response by ID and validate all types/ranges/options before producing output. Missing/duplicate/unexpected answers are protocol errors. Cancellation stops scheduling new work and signals in-flight curl operations; completed requests may already be billable.

SQL scanning has rows ready to batch, unlike a single interactive intent request. `max_questions_per_request=1` allows no-batch behavior with concurrency 10. Small vectors may not reach the concurrency ceiling; do not buffer an entire table merely to fill it. Cross-chunk asynchronous pipelining is a later optimization if profiling justifies the complexity.

No guarantee that SQL LIMIT means only that many model calls: upstream chunks, filters and optimizer decisions can evaluate more rows. Materialize a deterministic prefiltered candidate table before inference when a strict input budget matters. SQL rollback cannot undo remote API charges. Volatility is necessary but does not promise exactly one remote call across repeated expressions; use a materialized result table plus deduplication.

## Options and state

Proposed per-connection settings: model (`jev-latest` default), timeout_ms (30000), max_concurrency (10), max_questions_per_request (25), max_request_bytes (65536), max_retries (0), cache_mode (`query`), cache_capacity_bytes, cache_ttl_seconds, and query call budget. Snapshot settings at query start. Budget checks count actual attempts, including explicit retries. Reject unknown settings rather than ignoring them.

These names/defaults are extension design, not upstream Jev parameters. Retry off by default bounds spending. Optional bounded retries honor rate-limit backoff and Retry-After; never retry invalid input or authentication errors, and make attempts visible. POST retries after an ambiguous timeout can duplicate charges; do not claim exactly-once inference without provider idempotency support.

Credentials: `TYPESAFE_API_KEY` from the environment only; native DuckDB secret-provider integration is the deployment path. No key literals required in SQL and no secrets in EXPLAIN, errors, cache keys or logs. Retain TLS verification, avoid forwarding auth through cross-host redirects, and respect DuckDB external-access controls. Endpoint overrides are trusted connection configuration, not per-row inputs.

## Errors, metrics and cache

Default: fail the query on provider/auth/validation failure, with sanitized status and request ID. Never turn a timeout into FALSE or 'low confidence', which would hide a missing result in WHERE. An explicit later `jev_try_eval` can return an envelope with `status`, `error_code` and NULL answer; choose one coherent contract instead of adding error variants to every function initially.

Expose a per-connection request log/metrics relation: query/request IDs, row/question counts, requested/returned model, latency, attempts, status, tokens and cache counts. Token usage belongs to requests; do not duplicate it into every row and then sum. Never invent per-row latency by dividing batch duration. Raw evidence/response logging is off by default.

Start with bounded query-local deduplication and in-flight duplicate coalescing. Optional connection-memory LRU with TTL is phase 2; persistent disk cache is opt-in later. Cache identity includes canonical evidence, instructions, criteria, requested model/version, endpoint, a non-secret account namespace and cache schema version. Never reuse across tenants or credentials. Cache successful validated answers only. Unknown/timeout/auth failures are not decisions and are never cached. `jev-latest` may change without a versioned response; alias caches need TTL/invalidation and cannot promise reproducibility. Store returned model metadata and support explicit model pins. Changing a threshold can reuse a cached Noul result because thresholding is local.

## Discussion points

Recommended baseline: native C++, typed result structs, explicit predicate threshold, fail-fast errors, all three primitives plus mixed-question JSON, query-local cache first. SQL names, convenience overloads and whether persistent cache belongs in v1 are open for discussion. These are product design decisions, not required approvals to begin the technical spike.

## Sources

- https://docs.typesafe.ai/primitives
- https://docs.typesafe.ai/primitives/choice
- https://docs.typesafe.ai/primitives/score
- https://docs.typesafe.ai/primitives/noul
- https://docs.typesafe.ai/api.md
- https://docs.typesafe.ai/sdk/python/api/clients/sync/client.md
- https://github.com/duckdb/extension-template (observed main cfaf3e236008e782d27f4341b0ee036002d0a449)
- https://github.com/duckdb/extension-template-c (observed main d20892fa19756ca4839fbb4e7ce62b1aceb39ae5)
- https://github.com/duckdb/duckdb/releases/tag/v1.5.5
- https://github.com/duckdb/duckdb/blob/main/src/include/duckdb.h (main is research only, not our release target)

The PostgreSQL screenshot is inspiration only. Its row-count, latency, cache and price claims have not been independently reproduced.
