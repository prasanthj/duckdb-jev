"""Recordable live Jev throughput demo for the terminal."""

import json
import os
import time
from collections import Counter
from pathlib import Path

import duckdb

from benchmarks.live import QUESTIONS, corpus

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
DIM = "\033[2m"
YELLOW = "\033[1;33m"


def line(label: str, value: str, color: str = "") -> None:
    print(f"  {label:<24} {color}{value}{RESET}", flush=True)


def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise SystemExit("Set TYPESAFE_API_KEY before running this live example.")

    root = Path(__file__).resolve().parents[1]
    rows = 2049
    batch = 205
    concurrency = 10
    fixture = corpus(rows)
    con = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    try:
        con.execute(f"LOAD '{root / 'build/extension/jev/jev.duckdb_extension'}'")
        con.execute(f"SET jev_batch_size={batch}")
        con.execute(f"SET jev_concurrency={concurrency}")
        con.execute("SET jev_timeout_ms=90000")
        con.execute("SET jev_max_request_bytes=1048576")
        con.execute("SET jev_session_cache_bytes=16777216")
        con.execute("SET jev_session_cache_ttl_ms=60000")
        con.execute("CREATE TABLE events(id BIGINT, evidence JSON, expected VARCHAR)")
        con.executemany("INSERT INTO events VALUES (?,?,?)", [(row[0], row[1], row[2]) for row in fixture])
        questions = json.dumps({"route": QUESTIONS["route"]})
        sql = (
            "SELECT row_id, answers, model, cache_hit "
            "FROM jev_stream((SELECT id, evidence, ?::JSON FROM events)) ORDER BY row_id"
        )

        print(f"{CYAN}JEV × DUCKDB  /  LIVE THROUGHPUT{RESET}")
        print(f"{DIM}Vectorized input • batched remote inference • ordered SQL results{RESET}\n")
        print(f"{BOLD}CONFIGURATION{RESET}")
        line("Rows", f"{rows:,} unique nested JSON records")
        line("Primitive", "Jev Choice · 4 routing labels")
        line("Batching", f"{batch} rows/request · {concurrency} concurrent requests")
        line("Execution", "jev_stream over a DuckDB table")
        print(f"\n{DIM}SELECT * FROM jev_stream((SELECT id, evidence, questions FROM events));{RESET}")
        print(f"\n{YELLOW}● COLD RUN{RESET}  Calling the real Jev endpoint ...", flush=True)

        before = con.execute("SELECT requests, questions FROM jev_stats()").fetchone()
        assert before is not None
        started = time.perf_counter()
        cold = con.execute(sql, [questions]).fetchall()
        cold_seconds = time.perf_counter() - started
        after = con.execute("SELECT requests, questions FROM jev_stats()").fetchone()
        assert after is not None
        cold_requests = after[0] - before[0]
        cold_questions = after[1] - before[1]
        actual = [json.loads(row[1])["route"] for row in cold]
        expected = [row[2] for row in fixture]
        if [answer["choice"] for answer in actual] != expected:
            raise AssertionError("Live Choice results did not match the demo fixture")
        labels = Counter(answer["choice"] for answer in actual)
        model = cold[0][2]

        print(f"\n{GREEN}✓ {rows:,} classifications complete{RESET}")
        line("Wall time", f"{cold_seconds:.3f} seconds", GREEN)
        line("Measured throughput", f"{rows / cold_seconds:,.0f} rows/second", GREEN)
        line("HTTP work", f"{cold_requests} batched requests · {cold_questions:,} judgments")
        line("Returned model", str(model))
        line("Validated outputs", f"{len(actual):,}/{rows:,}")
        distribution = "  ".join(f"{name} {labels[name]}" for name in sorted(labels))
        line("Label distribution", distribution)

        print(f"\n{YELLOW}● WARM REPLAY{RESET}  Same SQL, cross-query TTL/LRU cache ...", flush=True)
        before_requests = after[0]
        started = time.perf_counter()
        warm = con.execute(sql, [questions]).fetchall()
        warm_seconds = time.perf_counter() - started
        after_warm = con.execute("SELECT requests FROM jev_stats()").fetchone()
        assert after_warm is not None
        warm_requests = after_warm[0] - before_requests
        cache_hits = sum(bool(row[3]) for row in warm)
        if cache_hits != rows or warm_requests != 0:
            raise AssertionError(f"Expected a fully cached replay; got {cache_hits} hits and {warm_requests} requests")

        print(f"\n{GREEN}✓ Cached replay complete{RESET}")
        line("Wall time", f"{warm_seconds * 1000:,.1f} ms", GREEN)
        line("Cache hits", f"{cache_hits:,}/{rows:,}", GREEN)
        line("API requests", "0", GREEN)
        print(
            f"\n{DIM}Single live run on this machine · synthetic data · throughput varies with network and service latency{RESET}",
            flush=True,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
