"""Bounded SQL demo through the native OpenRouter backend."""

import json
import os
import time
from pathlib import Path

import duckdb


def main() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit("Set OPENROUTER_API_KEY before running this live demo")
    extension = Path(os.environ.get("JEV_EXTENSION_PATH", "build/extension/jev/jev.duckdb_extension")).resolve()
    if not extension.is_file():
        raise SystemExit(f"Build the extension first or set JEV_EXTENSION_PATH: {extension}")
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    con.execute(f"LOAD '{extension}'")
    con.execute("SET jev_backend = 'openrouter'")
    con.execute("SET jev_openrouter_model = 'openai/gpt-4o-mini'")
    con.execute("SET jev_batch_size = 25")
    con.execute("SET jev_concurrency = 2")
    con.execute("SET jev_max_questions_per_query = 9")
    con.execute("SET jev_max_requests_per_query = 1")
    con.execute("SET jev_max_retries = 0")
    con.execute("SET jev_session_cache_bytes = 8388608")
    con.execute("SET jev_session_cache_ttl_ms = 60000")
    con.execute("""CREATE TEMP TABLE tickets AS SELECT * FROM (VALUES
      (1, 'I was charged twice. Please refund the duplicate payment.'),
      (2, 'The app crashes at login. We need an engineer to investigate.'),
      (3, 'Thanks for the fix! Everything is working well now.')
    ) AS t(id, message)""")

    questions = {
        "team": {
            "type": "choice",
            "instructions": "Route this support message to the best team.",
            "criteria": {
                "billing": "Charges, invoices, and refunds",
                "technical": "Unresolved product bugs and outages needing investigation",
                "general": "Resolved issues, thank-you messages, feedback, or other requests",
            },
        },
        "refund": {
            "type": "noul",
            "instructions": "Does the customer explicitly request a refund?",
        },
        "tone": {
            "type": "score",
            "instructions": "Rate the tone of this customer message.",
            "criteria": [
                "Negative: reports a problem or expresses frustration",
                "Neutral: factual with no clear positive or negative tone",
                "Positive: expresses thanks or satisfaction",
            ],
        },
    }
    sql = "SELECT id, jev_eval(message, ?::JSON) AS judgment FROM tickets ORDER BY id"
    args = [json.dumps(questions)]
    for label in ("First run", "Cached repeat"):
        started = time.perf_counter()
        rows = con.execute(sql, args).fetchall()
        elapsed_ms = (time.perf_counter() - started) * 1000
        print(f"{label}: {elapsed_ms:.1f} ms")
        for id_, judgment in rows:
            answers = json.loads(judgment["answers"])
            print(
                f"  ticket {id_}: team={answers['team']['choice']}, "
                f"refund={answers['refund']['noul']:.3f}, "
                f"tone={answers['tone']['score']:.3f}, "
                f"cache_hit={judgment['cache_hit']}"
            )
    stats_cursor = con.execute("SELECT * FROM jev_stats()")
    description = stats_cursor.description
    assert description is not None
    names = [column[0] for column in description]
    stats = stats_cursor.fetchone()
    assert stats is not None
    print("Stats:", dict(zip(names, stats, strict=True)))
    con.close()


if __name__ == "__main__":
    main()
