"""Streaming input, cross-chunk packing, and bounded pipeline lifecycle."""

import json
import time
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest
from conftest import Stub

QUESTIONS = json.dumps({"p": {"type": "noul", "instructions": "p"}})


def stream_sql(count: int, evidence: str = "{'i':i}") -> str:
    return (
        f"SELECT * FROM jev_stream((SELECT i, {evidence}, '{QUESTIONS}'::JSON "
        f"FROM range({count}) t(i)))"
    )


@pytest.mark.parametrize("count", [0, 1, 2047, 2048, 2049, 4097, 10001])
def test_cross_chunk_packing(db: duckdb.DuckDBPyConnection, stub: Stub, count: int) -> None:
    db.execute("SET jev_batch_size=1000")
    db.execute("SET jev_max_request_bytes=1048576")
    rows = db.execute(stream_sql(count)).fetchall()
    assert len(rows) == count
    assert [(row[0], json.loads(row[1])["p"]["noul"]) for row in rows] == [
        (i, (i % 10) / 10) for i in range(count)
    ]
    assert sum(len(c["body"]["questions"]) for c in stub.calls) == count
    if count <= 8192:
        assert len(stub.calls) == (count + 999) // 1000
    assert all(c["bytes"] <= 1048576 for c in stub.calls)


def test_empty_explain_and_null_never_call(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("EXPLAIN " + stream_sql(10)).fetchall()
    rows = db.execute(
        "SELECT * FROM jev_stream((SELECT NULL::INTEGER id, NULL::VARCHAR evidence, 'invalid' q FROM range(9000)))"
    ).fetchall()
    assert rows == [(None, None, None, None)] * 9000
    assert not stub.calls


def test_duplicates_coalesce_before_first_response(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    stub.delay = 0.05
    rows = db.execute(stream_sql(10000, "{'i':i%7}")).fetchall()
    assert all(json.loads(row[1])["p"]["noul"] == (row[0] % 7) / 10 for row in rows)
    assert sum(len(c["body"]["questions"]) for c in stub.calls) == 7
    assert sum(row[3] for row in rows) == 9993


def test_mult_question_rows_span_packs(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_batch_size=1")
    qs = {"a": {"type": "noul", "instructions": "a"}, "b": {"type": "noul", "instructions": "b"}}
    rows = db.execute(
        "SELECT * FROM jev_stream((SELECT i, {'i':i}, ?::JSON FROM range(5) t(i)))", [json.dumps(qs)]
    ).fetchall()
    assert len(rows) == 5
    assert all(json.loads(r[1])["a"]["noul"] == json.loads(r[1])["b"]["noul"] == r[0] / 10 for r in rows)
    assert len(stub.calls) == 10


@pytest.mark.parametrize("concurrency", [1, 4])
def test_pipeline_request_overlap(db: duckdb.DuckDBPyConnection, stub: Stub, concurrency: int) -> None:
    stub.delay = 0.03
    db.execute("SET jev_batch_size=1000")
    db.execute("SET jev_max_request_bytes=1048576")
    db.execute(f"SET jev_concurrency={concurrency}")
    assert len(db.execute(stream_sql(9000)).fetchall()) == 9000
    if concurrency == 1:
        assert stub.peak == 1
    else:
        # Thread scheduling need not saturate every worker, but requests must
        # overlap and may never exceed the configured concurrency bound.
        assert 2 <= stub.peak <= concurrency


def test_limit_stops_and_next_query_works(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    stub.delay = 0.01
    db.execute("SET jev_batch_size=1000")
    db.execute("SET jev_max_request_bytes=1048576")
    rows = db.execute(stream_sql(100000) + " LIMIT 1").fetchall()
    assert len(rows) == 1
    sent = sum(len(c["body"]["questions"]) for c in stub.calls)
    # DuckDB 1.4 can request one additional 2,048-row input vector before the
    # downstream LIMIT cancellation reaches this in/out operator. Work must
    # still stay within five vectors instead of consuming the 100,000-row input.
    assert sent <= 10240
    before = len(stub.calls)
    assert len(db.execute(stream_sql(1)).fetchall()) == 1
    assert len(stub.calls) == before + 1


@pytest.mark.parametrize("mode", ["missing", "malformed", "range"])
def test_failure_cleanup(db: duckdb.DuckDBPyConnection, stub: Stub, mode: str) -> None:
    stub.mode = mode
    with pytest.raises(duckdb.InvalidInputException):
        db.execute(stream_sql(10000)).fetchall()
    stub.mode = "normal"
    assert len(db.execute(stream_sql(2)).fetchall()) == 2


def test_interrupt_cleanup(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    stub.delay = 0.5
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(lambda: db.execute(stream_sql(100000)).fetchall())
        deadline = time.monotonic() + 10
        while not stub.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert stub.calls
        db.interrupt()
        with pytest.raises(duckdb.Error):
            task.result(timeout=10)
    stub.delay = 0
    assert len(db.execute(stream_sql(2)).fetchall()) == 2


def test_buffered_answer_budget(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    stub.mode = "large_answers"
    db.execute("SET jev_batch_size=1")
    with pytest.raises(duckdb.InvalidInputException, match="stream buffered answers exceed"):
        db.execute(stream_sql(50)).fetchall()
    stub.mode = "normal"
    assert len(db.execute(stream_sql(1)).fetchall()) == 1


def test_byte_splits_preserve_rows(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_max_request_bytes=600")
    rows = db.execute(stream_sql(200)).fetchall()
    assert len(rows) == 200
    assert all(c["bytes"] <= 600 for c in stub.calls)
    assert sum(len(c["body"]["questions"]) for c in stub.calls) == 200


def test_invalid_table_shape(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    with pytest.raises(duckdb.BinderException, match="columns"):
        db.execute("SELECT * FROM jev_stream((SELECT 1))").fetchall()
    assert not stub.calls


@pytest.mark.parametrize("sql", ["SELECT jev_noul('x','p')", stream_sql(1)])
def test_provider_model_bound(db: duckdb.DuckDBPyConnection, stub: Stub, sql: str) -> None:
    stub.mode = "large_model"
    with pytest.raises(duckdb.InvalidInputException, match="invalid provider response"):
        db.execute(sql).fetchall()
