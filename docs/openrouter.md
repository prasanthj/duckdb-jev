# OpenRouter backend

The native extension can send Choice, Noul, Score, `jev_eval`, and `jev_stream` questions directly to OpenRouter. It uses OpenRouter's structured-output chat completions, then validates the answer shape before returning SQL values. No local bridge or Python process is required in the query path.

## Configure

Build the extension from this branch until a release containing the OpenRouter backend is published. The native binary must match the exact DuckDB version and platform. Load it in a trusted DuckDB process with unsigned extensions enabled. Provide the OpenRouter key at runtime:

```sh
export OPENROUTER_API_KEY='...'
```

```sql
SET jev_backend = 'openrouter';
SET jev_openrouter_model = 'openai/gpt-4o-mini';
SET jev_batch_size = 25;
SET jev_max_questions_per_query = 1000;
SET jev_max_requests_per_query = 100;

SELECT jev_choice(
  {'message': 'The app crashes at login', 'account': 'enterprise'},
  'Route this ticket',
  '{"billing":"Charges and refunds","technical":"Unresolved bugs and outages"}'::JSON
);
```

A scoped DuckDB secret can supply the backend, key, model, and endpoint instead:

```sql
CREATE SECRET (TYPE jev, BACKEND 'openrouter', API_KEY '...',
               MODEL 'openai/gpt-4o-mini');
```

The secret's backend selects OpenRouter and its key takes precedence over `OPENROUTER_API_KEY`. A legacy Jev secret without `BACKEND` remains TypeSafe-only. `TYPESAFE_API_KEY` is never used for OpenRouter. The default endpoint is `https://openrouter.ai/api/v1/chat/completions`; `jev_openrouter_endpoint` or a matching secret's `ENDPOINT` can override it. HTTP is accepted only for an exact loopback host, for local tests. Redirects are disabled.

Run the bounded live example with `OPENROUTER_API_KEY` set:

```sh
uv run python examples/openrouter_demo.py
```

The example uses three synthetic tickets, nine questions, a one-request-per-query budget, no retries, and a connection cache. It makes at most one billable request on its first run. After a release includes this backend, `JEV_EXTENSION_PATH` can point to a matching downloaded binary.

## Operational contract

- `jev_openrouter_model` must name an OpenRouter model that supports strict JSON-schema structured outputs. OpenRouter provider routing is constrained with `require_parameters=true`; unsupported models or schemas fail the query.
- OpenRouter's model-supplied probabilities are **uncalibrated estimates**, not Jev probabilities. For Choice, the extension chooses the largest probability and reports that maximum as `confidence`. For Score, it reports the probability-weighted level index and the largest probability as `confidence`. Evaluate thresholds on your own labeled data before using `jev(...)` for decisions. The response `model` is prefixed with `openrouter/` to identify its origin.
- Existing cancellation, retries for transport/429/5xx, batching, query budgets, deduplication, cache, and `jev_stats()` apply. The cache is isolated by backend, endpoint, model, and key. OpenRouter prompt/completion tokens feed the existing input/output token counters, including completed HTTP responses rejected for malformed or truncated answers. Rollback and cancellation cannot undo accepted API charges.
- OpenRouter batches are limited to 100 questions. `jev_max_request_bytes` caps the **final serialized OpenRouter HTTP body** (up to 1 MiB). Scalar and streaming packers split batches to fit. A single question that cannot fit fails before network dispatch. The provider response body retains the extension's 8 MiB cap.
- Evidence, instructions, and criteria are sent to OpenRouter and may be routed to a model provider under your OpenRouter settings. Keep the key in an environment variable or DuckDB secret; query results and cache keys do not contain the key.
