"""Bounded first/repeat query and Parquet-reuse measurements against real Jev."""

import argparse
import hashlib
import json
import os
import statistics
import time
from datetime import UTC, datetime
from typing import Any

import duckdb

from benchmarks.live import QUESTIONS, ROOT, Relay, corpus


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live or not os.environ.get("TYPESAFE_API_KEY", "").strip():
        parser.error("--live and TYPESAFE_API_KEY are required")
    output = ROOT / "benchmarks/results" / datetime.now(UTC).strftime("live-cache-%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    relay = Relay(output, max_requests=40, max_questions=800)
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    rows = corpus(129)
    records: list[dict[str, Any]] = []
    print(f"Results: {output}", flush=True)
    try:
        con.execute(f"LOAD '{ROOT / 'build/extension/jev/jev.duckdb_extension'}'")
        con.execute("SET jev_endpoint=?", [relay.endpoint])
        con.execute("SET jev_session_cache_bytes=8388608")
        con.execute("SET jev_session_cache_ttl_ms=60000")
        con.execute("SET jev_batch_size=25")
        con.execute("SET jev_concurrency=10")
        con.execute("CREATE TABLE input_data(id BIGINT, evidence JSON)")
        con.executemany("INSERT INTO input_data VALUES (?,?)", [(r[0], r[1]) for r in rows])
        last_result: list[Any] = []
        for mode in ("scalar", "stream"):
            params = [QUESTIONS["route"]["instructions"], json.dumps(QUESTIONS["route"]["criteria"])]
            sql = "SELECT id, jev_choice(evidence, ?, ?::JSON) FROM input_data ORDER BY id"
            if mode == "stream":
                params = [json.dumps({"route": QUESTIONS["route"]})]
                sql = "SELECT * FROM jev_stream((SELECT id,evidence,?::JSON FROM input_data)) ORDER BY row_id"
            for repeat in range(3):
                con.execute("SELECT jev_cache_clear()")
                for phase in ("cold", "warm"):
                    label = f"{mode}-{repeat}-{phase}"
                    relay.label = label
                    before = len(relay.requests)
                    started = time.perf_counter()
                    returned = con.execute(sql, params).fetchall()
                    elapsed = time.perf_counter() - started
                    relay.drain()
                    assert len(returned) == 129
                    if mode == "scalar":
                        assert all(r[1]["choice"] == expected[2] for r, expected in zip(returned, rows, strict=True))
                        hits = sum(r[1]["cache_hit"] for r in returned)
                        last_result = returned
                    else:
                        assert all(
                            json.loads(r[1])["route"]["choice"] == expected[2]
                            for r, expected in zip(returned, rows, strict=True)
                        )
                        hits = sum(r[3] for r in returned)
                    requests = len(relay.requests) - before
                    if phase == "warm":
                        assert requests == 0 and hits == 129
                    record = {
                        "mode": mode,
                        "repeat": repeat,
                        "phase": phase,
                        "seconds": elapsed,
                        "requests": requests,
                        "cache_hits": hits,
                        "rows": 129,
                    }
                    records.append(record)
                    print(json.dumps(record), flush=True)
        # Materialize validated results with exact input/spec fingerprints. Reuse survives closing DuckDB.
        spec = hashlib.sha256(
            json.dumps(
                {"question": QUESTIONS["route"], "model": last_result[0][1]["model"], "provider": "typesafe"},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        con.execute(
            "CREATE TABLE saved_results(input_hash VARCHAR, spec_hash VARCHAR, result JSON, created_at TIMESTAMP)"
        )
        con.executemany(
            "INSERT INTO saved_results VALUES (?,?,?,current_timestamp)",
            [
                (hashlib.sha256(source[1].encode()).hexdigest(), spec, json.dumps(result[1]))
                for source, result in zip(rows, last_result, strict=True)
            ],
        )
        artifact = output / "enriched.parquet"
        con.execute("COPY saved_results TO ? (FORMAT PARQUET)", [str(artifact)])
        con.close()
        con = duckdb.connect()  # No extension loaded: entirely offline join against persisted results.
        con.execute("CREATE TABLE input_data(id BIGINT, evidence JSON)")
        con.executemany("INSERT INTO input_data VALUES (?,?)", [(r[0], r[1]) for r in rows])
        before = len(relay.requests)
        started = time.perf_counter()
        reused = con.execute(
            "SELECT i.id,p.result FROM input_data i JOIN read_parquet(?) p "
            "ON p.input_hash=sha256(i.evidence::VARCHAR) AND p.spec_hash=? "
            "WHERE p.created_at > current_timestamp - INTERVAL '1 minute' ORDER BY i.id",
            [str(artifact), spec],
        ).fetchall()
        elapsed = time.perf_counter() - started
        assert len(reused) == 129 and len(relay.requests) == before
        assert all(json.loads(r[1])["choice"] == expected[2] for r, expected in zip(reused, rows, strict=True))
        records.append(
            {
                "mode": "parquet",
                "phase": "new_connection_offline",
                "seconds": elapsed,
                "requests": 0,
                "rows": len(reused),
            }
        )
    finally:
        con.close()
        relay.close()
        (output / "trials.json").write_text(json.dumps(records, indent=2) + "\n")
        summary: dict[str, Any] = {
            "rows": 129,
            "requests": len(relay.requests),
            "questions": relay.questions,
            "extension_sha256": hashlib.sha256(
                (ROOT / "build/extension/jev/jev.duckdb_extension").read_bytes()
            ).hexdigest(),
        }
        for mode in ("scalar", "stream"):
            for phase in ("cold", "warm"):
                times = [r["seconds"] for r in records if r["mode"] == mode and r["phase"] == phase]
                if times:
                    summary[f"{mode}_{phase}_median_ms"] = statistics.median(times) * 1000
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
