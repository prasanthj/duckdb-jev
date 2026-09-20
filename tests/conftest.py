"""Deterministic HTTP/1.1 service; never invokes paid inference."""

import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import duckdb
import pytest

EXTENSION = Path(__file__).resolve().parents[1] / "build/extension/jev/jev.duckdb_extension"


class TestHTTPServer(ThreadingHTTPServer):
    # Python 3.11 defaults to five pending connections, below our ten workers.
    # A larger backlog prevents test-server overload during concurrent connects.
    request_queue_size = 128


class Stub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        self.sockets: set[tuple[str, int]] = set()
        self.active = 0
        self.peak = 0
        self.delay = 0.0
        self.status = 200
        self.mode = "normal"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            def handle_one_request(self) -> None:
                try:
                    super().handle_one_request()
                except ConnectionResetError:
                    self.close_connection = True  # Expected after native cancellation.

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                data = json.loads(body)
                with owner.lock:
                    owner.calls.append({"body": data, "bytes": len(body)})
                    owner.sockets.add(self.client_address)
                    owner.active += 1
                    owner.peak = max(owner.peak, owner.active)
                try:
                    time.sleep(owner.delay)
                    answers = {}
                    for key, question in reversed(list(data["questions"].items())):
                        evidence = question["instructions"]["evidence"]
                        number = evidence.get("i", 9) if isinstance(evidence, dict) else 9
                        kind = question["type"]
                        if kind == "noul":
                            answer = {"type": kind, "noul": (int(number) % 10) / 10}
                        elif kind == "choice":
                            options = list(question["criteria"])
                            chosen = options[int(number) % len(options)]
                            answer = {
                                "type": kind,
                                "choice": chosen,
                                "confidence": 1.0,
                                "probabilities": {x: float(x == chosen) for x in options},
                            }
                        else:
                            criteria = question["criteria"]
                            answer = {
                                "type": kind,
                                "score": 0.5,
                                "confidence": 0.5,
                                "legend": {str(i): item for i, item in enumerate(criteria)},
                                "probabilities": {str(i): 0.5 if i < 2 else 0.0 for i in range(len(criteria))},
                            }
                        if owner.mode == "distinct_confidence" and kind != "noul":
                            answer["confidence"] = 0.73
                        if owner.mode == "large_answers":
                            answer["extra"] = "x" * (1024 * 1024)
                        answers[key] = answer
                    if owner.mode == "missing":
                        answers.pop(next(iter(answers)))
                    if owner.mode == "range":
                        next(iter(answers.values()))["noul"] = 1.5
                    result = {
                        "model": "x" * 1025 if owner.mode == "large_model" else "jev-stub-pinned",
                        "answers": answers,
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    }
                    response = json.dumps(result).encode() if owner.mode != "malformed" else b"not-json"
                    self.send_response(owner.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with owner.lock:
                        owner.active -= 1

        self.server = TestHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stub]:
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key-never-real")
    instance = Stub()
    yield instance
    instance.close()


def connect(stub: Stub) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"allow_unsigned_extensions": True, "threads": 4})
    con.execute(f"LOAD '{EXTENSION}'")
    con.execute("SET jev_endpoint = ?", [stub.endpoint])
    return con


@pytest.fixture
def db(stub: Stub) -> Iterator[duckdb.DuckDBPyConnection]:
    if not EXTENSION.is_file():
        pytest.fail("Build native extension first: ./build.sh")
    con = connect(stub)
    yield con
    con.close()
