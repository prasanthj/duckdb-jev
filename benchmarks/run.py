"""Local transport/packing benchmark; does not contact TypeSafe or consume credits."""

import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

# Reuse the deterministic local server, not a second independently drifting protocol stub.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import Stub, connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--delay-ms", type=float, default=10)
    parser.add_argument("--batch-sizes", default="1,25,100")
    parser.add_argument("--concurrencies", default="1,4,10")
    args = parser.parse_args()
    if args.rows < 1 or args.repeats < 1 or args.delay_ms < 0:
        parser.error("rows/repeats must be positive; delay must be nonnegative")
    os.environ["TYPESAFE_API_KEY"] = "local-benchmark-not-a-real-key"
    output = Path(__file__).resolve().parent / "results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    metadata = {
        "kind": "local HTTP stub; not live Jev latency",
        "duckdb": duckdb.__version__,
        "machine": platform.machine(),
        "platform": platform.platform(),
        "settings": vars(args),
    }
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    summaries: list[dict[str, Any]] = []
    for batch in [int(x) for x in args.batch_sizes.split(",")]:
        for concurrency in [int(x) for x in args.concurrencies.split(",")]:
            trials = []
            for repeat in range(args.repeats):
                stub = Stub()
                stub.delay = args.delay_ms / 1000
                con = connect(stub)
                try:
                    con.execute(f"SET jev_batch_size={batch}")
                    con.execute(f"SET jev_concurrency={concurrency}")
                    started = time.perf_counter()
                    result = con.execute(
                        f"SELECT (jev_noul({{'i':i}},'p')).noul FROM range({args.rows}) t(i)"
                    ).fetchall()
                    elapsed = time.perf_counter() - started
                    assert result == [((i % 10) / 10,) for i in range(args.rows)]
                    record = {
                        "batch": batch,
                        "concurrency": concurrency,
                        "repeat": repeat,
                        "seconds": elapsed,
                        "rows_per_second": args.rows / elapsed,
                        "http_requests": len(stub.calls),
                        "tcp_connections": len(stub.sockets),
                        "peak_active_http": stub.peak,
                        "payload_bytes": sum(c["bytes"] for c in stub.calls),
                    }
                    trials.append(record)
                    with (output / "trials.jsonl").open("a") as stream:
                        stream.write(json.dumps(record) + "\n")
                finally:
                    con.close()
                    stub.close()
            times = sorted(t["seconds"] for t in trials)
            summary = {
                "batch": batch,
                "concurrency": concurrency,
                "median_seconds": statistics.median(times),
                "p95_query_seconds": times[math.ceil(len(times) * 0.95) - 1],
                "median_rows_per_second": statistics.median(t["rows_per_second"] for t in trials),
                "http_requests": trials[0]["http_requests"],
                "max_tcp_connections": max(t["tcp_connections"] for t in trials),
            }
            summaries.append(summary)
            (output / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
            print(json.dumps(summary), flush=True)
    report = [
        "# Native extension local benchmark",
        "",
        metadata["kind"],
        "",
        f"{args.rows} rows, {args.repeats} trials/configuration, {args.delay_ms} ms simulated server delay.",
        "",
        "| Batch | Concurrency | Requests | Median query seconds | Median rows/s |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        report.append(
            f"| {row['batch']} | {row['concurrency']} | {row['http_requests']} | "
            f"{row['median_seconds']:.3f} | {row['median_rows_per_second']:.0f} |"
        )
    report += [
        "",
        "These measure native packing, HTTP and local scheduling, not semantic accuracy or TypeSafe service latency.",
        "p95 is across whole-query trials; with three trials it is only the maximum observed, not a stable tail estimate.",
    ]
    (output / "report.md").write_text("\n".join(report) + "\n")
    print(f"Saved: {output}")


if __name__ == "__main__":
    main()
