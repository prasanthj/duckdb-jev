import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import duckdb
import pytest
from conftest import Stub, connect


def test_primitives(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    result = db.execute(
        "SELECT jev_noul({'i': 9}, 'urgent?'), jev_choice({'i': 1}, 'route?', "
        '\'{"a":"alpha","b":"beta"}\'::JSON), '
        "jev_score('message', 'sentiment?', '[\"low\",\"high\"]'::JSON)"
    ).fetchone()
    assert result is not None
    assert result[0] == {"noul": 0.9, "model": "jev-stub-pinned", "cache_hit": False}
    assert result[1]["choice"] == "b" and result[1]["probabilities"] == {"a": 0.0, "b": 1.0}
    assert result[2]["score"] == 0.5 and result[2]["confidence"] == 0.5
    assert json.loads(result[2]["legend"]) == {"0": "low", "1": "high"}
    assert len(stub.calls) == 3


def test_null_and_explain_never_call(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("EXPLAIN SELECT jev('hello','urgent?',0.8)").fetchall()
    assert db.execute("SELECT jev(NULL,'urgent?',0.8), jev_noul('x',NULL)").fetchone() == (None, None)
    assert db.execute("SELECT jev_noul(i::varchar,'x') FROM range(0) t(i)").fetchall() == []
    assert not stub.calls


def test_constant_dedup_and_threshold(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    rows = db.execute("SELECT jev_noul('same','urgent?') FROM range(100)").fetchall()
    assert all(r[0]["noul"] == 0.9 for r in rows)
    assert sum(r[0]["cache_hit"] for r in rows) == 99
    assert len(stub.calls) == 1
    assert db.execute("SELECT jev({'i':9},'urgent?',.9),jev({'i':9},'urgent?',.91)").fetchone() == (True, False)


def test_nested_evidence_null_fields(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute(
        "SELECT jev_noul({'i':9,'nested':{'text':'hi','missing':NULL},'items':[1,2,NULL]}, 'question')"
    ).fetchone()
    evidence = stub.calls[0]["body"]["questions"]["q0"]["instructions"]["evidence"]
    assert evidence == {"i": 9, "nested": {"text": "hi", "missing": None}, "items": [1, 2, None]}


def test_selection_and_multichunk_order(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    rows = db.execute(
        "SELECT i, (jev_noul({'i':i},'question')).noul FROM range(6000) t(i) WHERE i%3<>0 ORDER BY i DESC"
    ).fetchall()
    assert len(rows) == 4000
    assert all(value == (i % 10) / 10 for i, value in rows)
    assert all(len(c["body"]["questions"]) <= 25 for c in stub.calls)
    assert len(stub.sockets) <= 10  # Connection pool persists across chunks.


def test_mixed_questions_split_preserve_ids(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_batch_size=1")
    questions = {
        "urgent": {"type": "noul", "instructions": "urgent?"},
        "routing": {
            "type": "choice",
            "instructions": {"task": "route"},
            "criteria": {"yes": {"means": "yes"}, "no": None},
        },
        "sentiment": {"type": "score", "instructions": "mood?", "criteria": ["low", "high"]},
    }
    row = db.execute("SELECT jev_eval({'i':9},?::JSON)", [json.dumps(questions)]).fetchone()
    assert row is not None
    result = json.loads(row[0]["answers"])
    assert set(result) == set(questions)
    assert result["urgent"]["noul"] == 0.9
    assert len(stub.calls) == 3


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT jev('x','p',1.1)",
        "SELECT jev_choice('x','p','{}')",
        "SELECT jev_score('x','p','[\"one\"]')",
        'SELECT jev_eval(\'x\',\'{"a":{"type":"invented","instructions":"x"}}\')',
        "SELECT jev_noul('null'::JSON,'p')",
        "SELECT jev_noul({'x':'NaN'::DOUBLE},'p')",
    ],
)
def test_invalid_input_fails_without_calls(db: duckdb.DuckDBPyConnection, stub: Stub, sql: str) -> None:
    with pytest.raises(duckdb.Error):
        db.execute(sql).fetchall()
    assert not stub.calls


@pytest.mark.parametrize("mode", ["missing", "range", "malformed"])
def test_protocol_error_is_not_false(db: duckdb.DuckDBPyConnection, stub: Stub, mode: str) -> None:
    stub.mode = mode
    with pytest.raises(duckdb.Error):
        db.execute("SELECT jev('x','p',.5)").fetchall()
    assert len(stub.calls) == 1


def test_http_failure_no_retry(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    stub.status = 429
    with pytest.raises(duckdb.Error, match="429"):
        db.execute("SELECT jev_noul('x','p')").fetchall()
    assert len(stub.calls) == 1


def test_external_access_disabled(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET enable_external_access=false")
    with pytest.raises(duckdb.Error, match="external access"):
        db.execute("SELECT jev_noul('x','p')").fetchall()
    assert not stub.calls


def test_exact_byte_budget(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_max_request_bytes=900")
    db.execute("SELECT jev_noul({'i':i,'text':repeat('雪\\\"',30)},'p') FROM range(40) t(i)").fetchall()
    assert all(c["bytes"] <= 900 for c in stub.calls)
    previous = len(stub.calls)
    with pytest.raises(duckdb.Error, match="byte budget"):
        db.execute("SELECT jev_noul(repeat('x',901),'p')").fetchall()
    assert len(stub.calls) == previous


def test_global_ceiling_multiple_connections(stub: Stub) -> None:
    stub.delay = 0.02

    def query(_: int) -> list[Any]:
        con = connect(stub)
        try:
            con.execute("SET jev_batch_size=1")
            return con.execute("SELECT jev_noul({'i':i},'p') FROM range(30) t(i)").fetchall()
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(len(result) == 30 for result in pool.map(query, range(3)))
    assert 2 <= stub.peak <= 10
    assert len(stub.calls) == 90


def test_null_skips_invalid_constant_criteria(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    assert db.execute("SELECT jev_choice(NULL::VARCHAR,'p','not-json')").fetchone() == (None,)
    assert not stub.calls


def test_timeout_stops_queued_work(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_batch_size=1")
    db.execute("SET jev_concurrency=1")
    db.execute("SET jev_timeout_ms=30")
    stub.delay = 0.15
    with pytest.raises(duckdb.Error, match="transport failed"):
        db.execute("SELECT jev_noul({'i':i},'p') FROM range(200) t(i)").fetchall()
    time.sleep(0.25)
    assert len(stub.calls) == 1 and stub.active == 0
    stub.delay = 0
    db.execute("SET jev_timeout_ms=30000")
    assert db.execute("SELECT (jev_noul({'i':9},'p')).noul").fetchone() == (0.9,)


def test_interrupt_stops_queued_work(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_batch_size=1")
    db.execute("SET jev_concurrency=1")
    stub.delay = 0.2
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: db.execute("SELECT jev_noul({'i':i},'p') FROM range(500) t(i)").fetchall())
        deadline = time.monotonic() + 3
        while not stub.calls and time.monotonic() < deadline:
            time.sleep(0.005)
        assert stub.calls
        db.interrupt()
        with pytest.raises(duckdb.Error):
            future.result(timeout=5)
    time.sleep(0.3)
    assert stub.active == 0 and len(stub.calls) <= 1
    stub.delay = 0
    assert db.execute("SELECT (jev_noul({'i':9},'p')).noul").fetchone() == (0.9,)


def test_failure_stops_queued_requests(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_batch_size=1")
    db.execute("SET jev_concurrency=1")
    stub.mode = "missing"
    with pytest.raises(duckdb.Error):
        db.execute("SELECT jev_noul({'i':i},'p') FROM range(500) t(i)").fetchall()
    time.sleep(0.05)
    assert len(stub.calls) == 1 and stub.active == 0


def test_slow_context_does_not_occupy_all_workers(stub: Stub) -> None:
    stub.delay = 0.02

    def query(concurrency: int, count: int) -> None:
        con = connect(stub)
        try:
            con.execute("SET jev_batch_size=1")
            con.execute(f"SET jev_concurrency={concurrency}")
            con.execute(f"SELECT jev_noul({{'i':i,'lane':{concurrency}}},'p') FROM range({count}) t(i)").fetchall()
        finally:
            con.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(query, 1, 100)
        deadline = time.monotonic() + 3
        while not stub.calls and time.monotonic() < deadline:
            time.sleep(0.005)
        fast = pool.submit(query, 4, 12)
        fast.result(timeout=5)
        assert not slow.done()
        slow.result(timeout=10)
    assert 4 <= stub.peak <= 5


def test_expanded_payload_budget(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_max_request_bytes=1048576")
    questions = {f"q{i}": {"type": "noul", "instructions": "p"} for i in range(100)}
    with pytest.raises(duckdb.Error, match="packed requests exceed"):
        db.execute("SELECT jev_eval(repeat('x',500000),?::JSON)", [json.dumps(questions)]).fetchall()
    assert not stub.calls


@pytest.mark.parametrize("key", [None, "", "   ", "invalid\r\nheader"])
def test_invalid_environment_key_fails_before_request(
    db: duckdb.DuckDBPyConnection, stub: Stub, monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    if key is None:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TYPESAFE_API_KEY", key)
    with pytest.raises(duckdb.InvalidInputException, match="TYPESAFE_API_KEY is not configured or invalid"):
        db.execute("SELECT jev_noul('evidence','question')").fetchall()
    assert not stub.calls
