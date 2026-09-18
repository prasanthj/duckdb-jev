"""Bounded real Jev evaluation through an instrumented loopback forwarding relay.

No fabricated responses. No retries. Reads TYPESAFE_API_KEY only.
"""

import argparse
import hashlib
import http.client
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS: dict[str, Any] = {
    "route": {
        "type": "choice",
        "instructions": "Choose the team responsible for the customer's explicit request.",
        "criteria": {
            "billing": "Payments, invoices, duplicate charges or refunds",
            "technical": "Software errors, broken features, failed exports or API outages",
            "security": "Suspected unauthorized access, stolen credentials or security incidents",
            "sales": "Pricing quotes, plan upgrades or purchasing more seats",
        },
    },
    "refund": {"type": "noul", "instructions": "Does the customer explicitly request a refund or money back?"},
    "tone": {
        "type": "score",
        "instructions": "Assess negativity of the customer's written tone, not issue severity.",
        "criteria": [
            "Neutral or positive; calm factual request",
            "Frustrated or disappointed",
            "Extremely angry, explicit fury or outrage",
        ],
    },
}
TEMPLATES = [
    ("billing", True, 0, "Please refund the duplicate payment on our invoice. Thank you."),
    ("technical", False, 0, "The CSV export returns error 503. Please investigate the software failure."),
    (
        "security",
        False,
        0,
        "We found an unknown login and suspect our API key was stolen. Please investigate unauthorized access.",
    ),
    ("sales", False, 0, "Please send a pricing quote for purchasing 50 additional seats."),
    ("billing", False, 1, "This is frustrating. Our invoice still has the wrong billing address. Please correct it."),
    (
        "technical",
        False,
        1,
        "We are disappointed that the dashboard export still crashes. Please fix the export feature.",
    ),
    (
        "security",
        False,
        0,
        "We received an unexpected login from an unknown device. Please investigate this security incident.",
    ),
    ("sales", False, 0, "We love the product. Could you quote the annual enterprise plan upgrade?"),
    ("billing", True, 2, "I am furious about being charged twice again. This is outrageous. Give us our money back!"),
    (
        "technical",
        False,
        2,
        "I am furious that the API has been broken all day. This outage is outrageous. Fix the API!",
    ),
    (
        "security",
        False,
        1,
        "We are frustrated that an unauthorized user gained access. Please investigate the compromised credentials.",
    ),
    ("sales", False, 0, "We need to purchase 100 licenses. Please provide a volume pricing quote."),
]


def corpus(count: int) -> list[tuple[int, str, str, bool, int]]:
    rows = []
    for i in range(count):
        route, refund, tone, message = TEMPLATES[i % len(TEMPLATES)]
        evidence = {
            "ticket": {"id": f"T-{i:05}", "message": message},
            "account": {"id": f"A-{i % 17:02}", "plan": "enterprise", "region": "US"},
            "telemetry": {"active_users_7d": 20 + i % 31, "events": ["login", "report_view"], "last_error": None},
            "attachments": [],
        }
        rows.append((i, json.dumps(evidence, separators=(",", ":")), route, refund, tone))
    return rows


class Relay:
    def __init__(self, output: Path, max_requests: int, max_questions: int) -> None:
        self.output = output
        self.lock = threading.Lock()
        self.requests: list[dict[str, Any]] = []
        self.questions = 0
        self.idle = threading.Event()
        self.idle.set()
        self.active = 0
        self.peak = 0
        self.label = "startup"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.upstream = http.client.HTTPSConnection("api.typesafe.ai", timeout=90)

            def finish(self) -> None:
                self.upstream.close()
                super().finish()

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def handle_one_request(self) -> None:
                try:
                    super().handle_one_request()
                except ConnectionResetError:
                    self.close_connection = True

            def do_POST(self) -> None:
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                question_count = len(json.loads(payload)["questions"])
                with owner.lock:
                    allowed = len(owner.requests) < max_requests and owner.questions + question_count <= max_questions
                    if allowed:
                        owner.questions += question_count
                        record: dict[str, Any] = {
                            "trial": owner.label,
                            "index": len(owner.requests),
                            "questions": question_count,
                            "request_bytes": len(payload),
                        }
                        owner.requests.append(record)
                        owner.active += 1
                        owner.idle.clear()
                        owner.peak = max(owner.peak, owner.active)
                if not allowed:
                    self.send_error(503, "Live run budget exhausted")
                    return
                started = time.perf_counter()
                status = 502
                body = b'{"error":"relay transport failure"}'
                try:
                    self.upstream.request(
                        "POST",
                        "/v1/systemone",
                        body=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": self.headers.get("Authorization", ""),
                        },
                    )
                    response = self.upstream.getresponse()
                    status, body = response.status, response.read()
                    decoded = json.loads(body)
                    if not isinstance(decoded, dict):
                        raise TypeError("Expected upstream JSON object")
                    record["usage"] = decoded.get("usage")
                    record["model"] = decoded.get("model")
                except (OSError, http.client.HTTPException, TypeError, ValueError) as exc:
                    record["transport_error"] = type(exc).__name__  # Never log credentials/headers.
                    self.upstream.close()
                finally:
                    record.update(status=status, seconds=time.perf_counter() - started, response_bytes=len(body))
                    with owner.lock:
                        owner.active -= 1
                        if owner.active == 0:
                            owner.idle.set()
                        with (owner.output / "requests.jsonl").open("a") as stream:
                            stream.write(json.dumps(record) + "\n")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    def drain(self) -> None:
        if not self.idle.wait(timeout=95):
            raise TimeoutError("Upstream requests did not finish within relay timeout")

    def close(self) -> None:
        self.drain()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def percentile(values: list[float], p: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)] if values else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Required: makes billable API requests")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-requests", type=int, default=1500)
    parser.add_argument("--max-questions", type=int, default=12000)
    args = parser.parse_args()
    if not args.live or not os.environ.get("TYPESAFE_API_KEY", "").strip():
        parser.error("--live and TYPESAFE_API_KEY are required")
    if args.repeats < 1 or args.max_requests < 1 or args.max_questions < 1:
        parser.error("repeats and budgets must be positive")
    output = ROOT / "benchmarks/results" / datetime.now(UTC).strftime("live-%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    fixture = corpus(2049)
    (output / "corpus.json").write_text(json.dumps(fixture, indent=2) + "\n")
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "kind": "REAL Jev endpoint via loopback metrics relay",
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
        "extension_sha256": hashlib.sha256(
            (ROOT / "build/extension/jev/jev.duckdb_extension").read_bytes()
        ).hexdigest(),
        "duckdb": duckdb.__version__,
        "platform": platform.platform(),
        "settings": vars(args),
        "corpus_sha256": hashlib.sha256(json.dumps(fixture).encode()).hexdigest(),
        "notes": "Synthetic templated corpus, not a production accuracy estimate. No retries. TLS connections reused by relay.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Results: {output}", flush=True)
    relay = Relay(output, args.max_requests, args.max_questions)
    con = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    results: list[dict[str, Any]] = []
    sql = {
        "choice": "SELECT id, jev_choice(evidence, ?, ?::JSON) FROM input_data ORDER BY id",
        "noul": "SELECT id, jev_noul(evidence, ?) FROM input_data ORDER BY id",
        "boolean": "SELECT id, jev(evidence, ?, .5) FROM input_data ORDER BY id",
        "score": "SELECT id, jev_score(evidence, ?, ?::JSON) FROM input_data ORDER BY id",
        "eval": "SELECT id, jev_eval(evidence, ?::JSON) FROM input_data ORDER BY id",
        "stream": "SELECT * FROM jev_stream((SELECT id,evidence,?::JSON FROM input_data)) ORDER BY row_id",
    }

    def run(
        label: str,
        mode: str,
        rows: list[tuple[int, str, str, bool, int]],
        batch: int,
        concurrency: int,
        cache_bytes: int = 8388608,
    ) -> None:
        con.execute("DELETE FROM input_data")
        con.executemany("INSERT INTO input_data VALUES (?,?, ?,?,?)", rows)
        con.execute(f"SET jev_batch_size={batch}")
        con.execute(f"SET jev_concurrency={concurrency}")
        con.execute(f"SET jev_cache_bytes={cache_bytes}")
        if mode == "choice":
            params = [QUESTIONS["route"]["instructions"], json.dumps(QUESTIONS["route"]["criteria"])]
        elif mode in ("noul", "boolean"):
            params = [QUESTIONS["refund"]["instructions"]]
        elif mode == "score":
            params = [QUESTIONS["tone"]["instructions"], json.dumps(QUESTIONS["tone"]["criteria"])]
        else:
            params = [json.dumps(QUESTIONS if mode == "eval" else {"route": QUESTIONS["route"]})]
        relay.label, relay.peak = label, 0
        before = len(relay.requests)
        started = time.perf_counter()
        record: dict[str, Any] = {
            "trial": label,
            "mode": mode,
            "rows": len(rows),
            "batch": batch,
            "concurrency": concurrency,
            "cache_bytes": cache_bytes,
        }
        try:
            returned = con.execute(sql[mode], params).fetchall()
            record["query_seconds"] = time.perf_counter() - started
            if len(returned) != len(rows) or [r[0] for r in returned] != [r[0] for r in rows]:
                raise AssertionError("Output rows/IDs do not match input")
            checks = correct = hits = 0
            with (output / "outputs.jsonl").open("a") as stream:
                for actual, expected in zip(returned, rows, strict=True):
                    rid, _, route, refund, tone = expected
                    stream.write(json.dumps({"trial": label, "row_id": rid, "result": actual[1:]}) + "\n")
                    if mode == "boolean":
                        correct += actual[1] == refund
                        checks += 1
                        continue
                    result = actual[1]
                    if mode == "stream":
                        answers = json.loads(actual[1])
                        hits += actual[3]
                    else:
                        hits += result["cache_hit"]
                        if mode == "eval":
                            answers = json.loads(result["answers"])
                        else:
                            answers = {{"choice": "route", "noul": "refund", "score": "tone"}[mode]: result}
                    for name, answer in answers.items():
                        checks += 1
                        if name == "route":
                            correct += answer["choice"] == route
                        elif name == "refund":
                            correct += (answer["noul"] >= 0.5) == refund
                        else:
                            # Subjective tone uses a tolerance, not an exact index assertion.
                            correct += abs(answer["score"] - tone) <= 0.75
            record.update(
                status="ok",
                correct=correct,
                checks=checks,
                accuracy=correct / checks,
                cache_hits=hits,
                rows_per_second=len(rows) / record["query_seconds"],
            )
        except (duckdb.Error, AssertionError, KeyError, TypeError, ValueError) as exc:
            record.update(status="error", error=str(exc), query_seconds=time.perf_counter() - started)
        relay.drain()
        request_slice = relay.requests[before:]
        record.update(
            requests=len(request_slice),
            questions=sum(r["questions"] for r in request_slice),
            peak_http=relay.peak,
            request_p50=percentile([r["seconds"] for r in request_slice], 0.5),
            request_p95=percentile([r["seconds"] for r in request_slice], 0.95),
        )
        results.append(record)
        with (output / "trials.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        print(json.dumps(record), flush=True)
        if record["status"] == "error":
            # Fail fast on real transport/protocol problems instead of burning repeated calls.
            raise RuntimeError(f"Live case failed: {label}; inspect saved results")

    try:
        con.execute(f"LOAD '{ROOT / 'build/extension/jev/jev.duckdb_extension'}'")
        con.execute("SET jev_endpoint=?", [relay.endpoint])
        con.execute("SET jev_timeout_ms=90000")
        con.execute("SET jev_max_request_bytes=1048576")
        con.execute("CREATE TABLE input_data(id BIGINT, evidence JSON, expected VARCHAR, refund BOOLEAN, tone INTEGER)")
        # All scalar primitives and mixed-question evaluation.
        for mode in ("noul", "boolean", "score", "eval"):
            run(f"primitives-{mode}", mode, fixture[:100], 25, 10)
        for mode in ("choice", "stream"):
            for batch, concurrency in ((1, 1), (1, 10), (25, 1), (25, 10), (100, 10)):
                for repeat in range(args.repeats):
                    run(f"matrix-{mode}-b{batch}-c{concurrency}-r{repeat}", mode, fixture[:100], batch, concurrency)
        for mode in ("choice", "stream"):
            run(f"crosschunk-{mode}", mode, fixture, 100, 10)
            duplicates = [(i, *fixture[i % 10][1:]) for i in range(4097)]
            run(f"duplicates-{mode}", mode, duplicates, 25, 10)
        run("cache-disabled", "choice", [(i, *fixture[i % 10][1:]) for i in range(4097)], 25, 10, 0)
    finally:
        con.close()
        relay.close()
        usage = [r["usage"] for r in relay.requests if isinstance(r.get("usage"), dict)]
        token_totals = {
            key: sum(u.get(key, 0) for u in usage if isinstance(u.get(key, 0), (int, float)))
            for key in {k for u in usage for k, value in u.items() if isinstance(value, (int, float))}
        }
        summary = {
            "queries": len(results),
            "completed": sum(r["status"] == "ok" for r in results),
            "requests": len(relay.requests),
            "questions": relay.questions,
            "http_statuses": {
                str(s): sum(r.get("status") == s for r in relay.requests)
                for s in {r.get("status") for r in relay.requests}
            },
            "models": sorted({r["model"] for r in relay.requests if isinstance(r.get("model"), str)}),
            "usage_totals": token_totals,
            "request_p50": percentile([r["seconds"] for r in relay.requests], 0.5),
            "request_p95": percentile([r["seconds"] for r in relay.requests], 0.95),
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        lines = [
            "# Live Jev native extension results",
            "",
            json.dumps(summary),
            "",
            "Real upstream through loopback relay; synthetic templated data. No retries. Three trials are not enough for stable query p95.",
            "",
            "| Path | Batch | Concurrency | Median query s | Requests/query | Accuracy range |",
            "|---|---:|---:|---:|---|---|",
        ]
        for mode in ("choice", "stream"):
            for batch, concurrency in ((1, 1), (1, 10), (25, 1), (25, 10), (100, 10)):
                group = [
                    r
                    for r in results
                    if r["trial"].startswith("matrix-")
                    and r["mode"] == mode
                    and r["batch"] == batch
                    and r["concurrency"] == concurrency
                    and r["status"] == "ok"
                ]
                if group:
                    lines.append(
                        f"| {mode} | {batch} | {concurrency} | {statistics.median(r['query_seconds'] for r in group):.4f} | "
                        f"{[r['requests'] for r in group]} | {min(r['accuracy'] for r in group):.1%}–{max(r['accuracy'] for r in group):.1%} |"
                    )
        (output / "report.md").write_text("\n".join(lines) + "\n")
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
