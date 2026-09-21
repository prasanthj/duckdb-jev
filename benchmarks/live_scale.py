"""Bounded live Jev Choice benchmark for a specific row count."""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import time
from datetime import UTC, datetime
from typing import Any

import duckdb

from benchmarks.live import QUESTIONS, ROOT, Relay, corpus, percentile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Required: makes billable API requests")
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--batch-sizes", default="25,100")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--max-requests", type=int, default=200)
    parser.add_argument("--max-questions", type=int, default=8000)
    args = parser.parse_args()
    if not args.live or not os.environ.get("TYPESAFE_API_KEY", "").strip():
        parser.error("--live and TYPESAFE_API_KEY are required")
    batch_sizes = [int(value) for value in args.batch_sizes.split(",")]
    if args.rows < 1 or args.repeats < 1 or args.concurrency < 1 or any(batch < 1 for batch in batch_sizes):
        parser.error("rows, repeats, concurrency and batch sizes must be positive")

    output = ROOT / "benchmarks/results" / datetime.now(UTC).strftime("live-scale-%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    fixture = corpus(args.rows)
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "kind": "REAL Jev Choice scaling benchmark via loopback metrics relay",
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "extension_sha256": hashlib.sha256(
            (ROOT / "build/extension/jev/jev.duckdb_extension").read_bytes()
        ).hexdigest(),
        "duckdb": duckdb.__version__,
        "platform": platform.platform(),
        "settings": vars(args),
        "primitive": "choice",
        "notes": "Synthetic templated corpus for throughput measurement, not a production accuracy estimate.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Results: {output}", flush=True)

    relay = Relay(output, args.max_requests, args.max_questions)
    con = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    results: list[dict[str, Any]] = []
    try:
        con.execute(f"LOAD '{ROOT / 'build/extension/jev/jev.duckdb_extension'}'")
        con.execute("SET jev_endpoint=?", [relay.endpoint])
        con.execute("SET jev_timeout_ms=90000")
        con.execute("SET jev_max_request_bytes=1048576")
        con.execute("CREATE TABLE input_data(id BIGINT, evidence JSON, expected VARCHAR)")
        con.executemany("INSERT INTO input_data VALUES (?,?,?)", [(row[0], row[1], row[2]) for row in fixture])
        questions = json.dumps({"route": QUESTIONS["route"]})
        expected = [row[2] for row in fixture]
        for batch in batch_sizes:
            con.execute(f"SET jev_batch_size={batch}")
            con.execute(f"SET jev_concurrency={args.concurrency}")
            for repeat in range(args.repeats):
                label = f"choice-{args.rows}-b{batch}-c{args.concurrency}-r{repeat}"
                relay.label, relay.peak = label, 0
                before = len(relay.requests)
                started = time.perf_counter()
                returned = con.execute(
                    "SELECT row_id, answers FROM jev_stream((SELECT id,evidence,?::JSON FROM input_data)) ORDER BY row_id",
                    [questions],
                ).fetchall()
                query_seconds = time.perf_counter() - started
                relay.drain()
                request_slice = relay.requests[before:]
                actual = [json.loads(row[1])["route"]["choice"] for row in returned]
                if actual != expected:
                    raise AssertionError(f"Choice outputs did not match fixture in {label}")
                record = {
                    "trial": label,
                    "primitive": "choice",
                    "rows": args.rows,
                    "batch": batch,
                    "concurrency": args.concurrency,
                    "repeat": repeat,
                    "query_seconds": query_seconds,
                    "rows_per_second": args.rows / query_seconds,
                    "requests": len(request_slice),
                    "questions": sum(int(request["questions"]) for request in request_slice),
                    "peak_http": relay.peak,
                    "request_p50": percentile([float(request["seconds"]) for request in request_slice], 0.5),
                    "request_p95": percentile([float(request["seconds"]) for request in request_slice], 0.95),
                    "accuracy": 1.0,
                }
                results.append(record)
                with (output / "trials.jsonl").open("a") as stream:
                    stream.write(json.dumps(record) + "\n")
                print(json.dumps(record), flush=True)
    finally:
        con.close()
        relay.close()

    summaries = []
    for batch in batch_sizes:
        group = [record for record in results if record["batch"] == batch]
        summaries.append(
            {
                "primitive": "choice",
                "rows": args.rows,
                "batch": batch,
                "concurrency": args.concurrency,
                "repeats": len(group),
                "median_query_seconds": statistics.median(record["query_seconds"] for record in group),
                "median_rows_per_second": statistics.median(record["rows_per_second"] for record in group),
                "requests_per_trial": [record["requests"] for record in group],
            }
        )
    summary = {
        "models": sorted({request["model"] for request in relay.requests if isinstance(request.get("model"), str)}),
        "http_statuses": {
            str(status): sum(request.get("status") == status for request in relay.requests)
            for status in {request.get("status") for request in relay.requests}
        },
        "requests": len(relay.requests),
        "questions": relay.questions,
        "results": summaries,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
