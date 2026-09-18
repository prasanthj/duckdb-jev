"""Compare scalar/chunk and streaming packing using local HTTP only."""

import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks.run import Stub, connect


def main() -> None:
    os.environ["TYPESAFE_API_KEY"] = "local-benchmark-not-a-real-key"
    output = Path(__file__).resolve().parent / "results" / datetime.now(UTC).strftime("stream-%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    trials: list[dict[str, Any]] = []
    questions = json.dumps({"p": {"type": "noul", "instructions": "p"}})
    for mode in ("scalar", "stream"):
        for repeat in range(3):
            stub = Stub()
            stub.delay = 0.02
            con = connect(stub)
            try:
                con.execute("SET jev_batch_size=1000")
                con.execute("SET jev_max_request_bytes=1048576")
                started = time.perf_counter()
                if mode == "scalar":
                    result = con.execute("SELECT (jev_noul({'i':i},'p')).noul FROM range(4097) t(i)").fetchall()
                    assert result == [((i % 10) / 10,) for i in range(4097)]
                else:
                    result = con.execute(
                        "SELECT * FROM jev_stream((SELECT i, {'i':i}, ?::JSON FROM range(4097) t(i)))",
                        [questions],
                    ).fetchall()
                    assert [(r[0], json.loads(r[1])["p"]["noul"]) for r in result] == [
                        (i, (i % 10) / 10) for i in range(4097)
                    ]
                trials.append(
                    {
                        "mode": mode,
                        "repeat": repeat,
                        "seconds": time.perf_counter() - started,
                        "requests": len(stub.calls),
                        "peak_http": stub.peak,
                    }
                )
            finally:
                con.close()
                stub.close()
    report = {
        "kind": "local stub, NOT live Jev latency",
        "rows": 4097,
        "batch_size": 1000,
        "concurrency": 10,
        "delay_ms": 20,
        "trials": trials,
    }
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    for mode in ("scalar", "stream"):
        runs = [r for r in trials if r["mode"] == mode]
        print(
            json.dumps(
                {
                    "mode": mode,
                    "median_seconds": statistics.median(r["seconds"] for r in runs),
                    "requests": [r["requests"] for r in runs],
                }
            )
        )
    print(output)


if __name__ == "__main__":
    main()
