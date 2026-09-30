"""Native OpenRouter backend checks with a local HTTP fixture, no paid calls."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import duckdb
import pytest
from conftest import EXTENSION


class OpenRouterStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.mode = "normal"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                size = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(size))
                owner.calls.append({"body": body, "bytes": size,
                                    "authorization": self.headers.get("Authorization")})
                questions = json.loads(body["messages"][1]["content"])["questions"]
                answers = {}
                for key, question in questions.items():
                    kind = question["type"]
                    if kind == "noul":
                        answers[key] = {"noul": 0.75}
                    elif kind == "choice":
                        labels = list(question["criteria"])
                        values = [1.0] if len(labels) == 1 else [0.8] + [0.2 / (len(labels) - 1)] * (len(labels) - 1)
                        if owner.mode == "bad_distribution" and len(labels) > 1:
                            values = [0.1] * len(labels)
                        answers[key] = {"probabilities": dict(zip(labels, values, strict=True))}
                    else:
                        labels = [str(i) for i in range(len(question["criteria"]))]
                        values = [0.7, 0.3] if len(labels) == 2 else [0.7, 0.2, 0.1]
                        answers[key] = {"probabilities": dict(zip(labels, values, strict=True))}
                if owner.mode == "missing_id":
                    answers.clear()
                elif owner.mode == "wrong_id":
                    answers = {"unexpected": next(iter(answers.values()))}
                elif owner.mode in {"negative_probability", "nonnumeric_probability", "incomplete_distribution"}:
                    distribution = next(iter(answers.values()))["probabilities"]
                    first = next(iter(distribution))
                    if owner.mode == "negative_probability":
                        distribution[first] = -0.1
                    elif owner.mode == "nonnumeric_probability":
                        distribution[first] = "high"
                    else:
                        distribution.pop(first)
                result = {"model": body["model"], "choices": [{
                    "finish_reason": "length" if owner.mode == "length" else "stop",
                    "message": {"content": json.dumps(answers)}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 5}}
                encoded = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/chat/completions"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[OpenRouterStub]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-test-key")
    instance = OpenRouterStub()
    yield instance
    instance.close()


@pytest.fixture
def db(server: OpenRouterStub) -> Iterator[duckdb.DuckDBPyConnection]:
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    con.execute(f"LOAD '{EXTENSION}'")
    con.execute("SET jev_backend='openrouter'")
    con.execute("SET jev_openrouter_endpoint=?", [server.endpoint])
    yield con
    con.close()


def test_native_choice_noul_score_and_batch(db: duckdb.DuckDBPyConnection, server: OpenRouterStub) -> None:
    questions = {"yes": {"type": "noul", "instructions": "refund?"},
                 "route": {"type": "choice", "instructions": "which team?",
                           "criteria": {"billing": "refunds", "technical": "bugs"}},
                 "tone": {"type": "score", "instructions": "tone?",
                          "criteria": ["negative", "neutral", "positive"]}}
    row = db.execute("SELECT jev_eval('refund please', ?::JSON)", [json.dumps(questions)]).fetchone()
    assert row is not None
    result = row[0]
    answers = json.loads(result["answers"])
    assert result["model"] == "openrouter/openai/gpt-4o-mini"
    assert answers["yes"]["noul"] == 0.75
    assert answers["route"]["choice"] == "billing"
    assert answers["route"]["confidence"] == 0.8
    assert answers["tone"]["score"] == pytest.approx(0.4)
    assert len(server.calls) == 1
    call = server.calls[0]
    assert call["authorization"] == "Bearer openrouter-test-key"
    assert call["body"]["provider"]["require_parameters"] is True
    assert call["body"]["response_format"]["type"] == "json_schema"
    assert len(call["body"]["response_format"]["json_schema"]["schema"]["properties"]) == 3


def test_stream_uses_native_backend(db: duckdb.DuckDBPyConnection, server: OpenRouterStub) -> None:
    rows = db.execute("""SELECT * FROM jev_stream((SELECT i, {'i': i},
      '{"p":{"type":"noul","instructions":"is this positive?"}}'::JSON
      FROM range(3) t(i))) ORDER BY row_id""").fetchall()
    assert len(rows) == 3
    assert all(json.loads(row[1])["p"]["noul"] == 0.75 for row in rows)
    assert len(server.calls) == 1


def test_missing_openrouter_key_does_not_use_typesafe_key(
    db: duckdb.DuckDBPyConnection, server: OpenRouterStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("TYPESAFE_API_KEY", "wrong-provider-key")
    with pytest.raises(duckdb.Error, match="OPENROUTER_API_KEY"):
        db.execute("SELECT jev_noul('hello', 'p')").fetchall()
    assert not server.calls


def test_backend_secret_selects_openrouter_and_its_credential(
    db: duckdb.DuckDBPyConnection, server: OpenRouterStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    db.execute("SET jev_backend='typesafe'")
    db.execute("CREATE SECRET (TYPE jev, BACKEND 'openrouter', API_KEY 'secret-openrouter-key', "
               "MODEL 'openai/gpt-4o-mini', ENDPOINT ?)", [server.endpoint])
    result = db.execute("SELECT jev_noul('hello', 'p')").fetchone()
    assert result is not None and result[0]["noul"] == 0.75
    assert server.calls[0]["authorization"] == "Bearer secret-openrouter-key"
    secret = db.execute("SELECT secret_string FROM duckdb_secrets() WHERE type='jev'").fetchone()
    assert secret is not None and "secret-openrouter-key" not in secret[0]


def test_legacy_typesafe_secret_is_not_sent_to_openrouter(
    db: duckdb.DuckDBPyConnection, server: OpenRouterStub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    db.execute("CREATE SECRET (TYPE jev, API_KEY 'typesafe-only-secret')")
    with pytest.raises(duckdb.Error, match="OPENROUTER_API_KEY"):
        db.execute("SELECT jev_noul('hello', 'p')").fetchall()
    assert not server.calls


def test_incomplete_completion_counts_billable_tokens(
    db: duckdb.DuckDBPyConnection, server: OpenRouterStub
) -> None:
    server.mode = "length"
    before = db.execute("SELECT input_tokens, output_tokens FROM jev_stats()").fetchone()
    with pytest.raises(duckdb.Error, match="incomplete structured answer"):
        db.execute("SELECT jev_noul('hello', 'p')").fetchall()
    after = db.execute("SELECT input_tokens, output_tokens FROM jev_stats()").fetchone()
    assert before is not None and after is not None
    assert (after[0] - before[0], after[1] - before[1]) == (12, 5)
    assert len(server.calls) == 1


@pytest.mark.parametrize("mode", ["bad_distribution", "missing_id", "wrong_id",
                                  "negative_probability", "nonnumeric_probability",
                                  "incomplete_distribution"])
def test_invalid_output_is_terminal(db: duckdb.DuckDBPyConnection, server: OpenRouterStub, mode: str) -> None:
    server.mode = mode
    with pytest.raises(duckdb.Error):
        db.execute("SELECT jev_choice('hello', 'p', '{\"a\":null,\"b\":null}'::JSON)").fetchall()
    assert len(server.calls) == 1


def test_final_openrouter_body_respects_byte_cap(db: duckdb.DuckDBPyConnection, server: OpenRouterStub) -> None:
    db.execute("SET jev_max_request_bytes=700")
    with pytest.raises(duckdb.Error, match="single question exceeds request byte budget"):
        db.execute("SELECT jev_noul('hello', 'p')").fetchall()
    assert not server.calls


@pytest.mark.parametrize("stream", [False, True])
def test_final_wire_size_splits_packs(db: duckdb.DuckDBPyConnection, server: OpenRouterStub, stream: bool) -> None:
    db.execute("SET jev_batch_size=100")
    db.execute("SET jev_max_request_bytes=1400")
    if stream:
        db.execute("""SELECT * FROM jev_stream((SELECT i, {'i': i},
          '{"p":{"type":"noul","instructions":"question"}}'::JSON
          FROM range(10) t(i)))""").fetchall()
    else:
        db.execute("SELECT jev_noul({'i':i}, 'question') FROM range(10) t(i)").fetchall()
    assert len(server.calls) > 1
    assert all(call["bytes"] <= 1400 for call in server.calls)


def test_endpoint_rejects_userinfo_loopback_bypass(db: duckdb.DuckDBPyConnection, server: OpenRouterStub) -> None:
    db.execute("SET jev_openrouter_endpoint='http://127.0.0.1:secret@attacker.example/path'")
    with pytest.raises(duckdb.Error, match="endpoint requires HTTPS"):
        db.execute("SELECT jev_noul('hello', 'p')").fetchall()
    assert not server.calls
