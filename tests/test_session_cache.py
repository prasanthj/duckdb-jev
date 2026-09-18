"""Opt-in cross-query LRU, expiry, isolation and explicit invalidation."""

import time

import duckdb
import pytest
from conftest import Stub, connect


def enable(db: duckdb.DuckDBPyConnection, budget: int = 1048576, ttl: int = 60000) -> None:
    db.execute(f"SET jev_session_cache_bytes={budget}")
    db.execute(f"SET jev_session_cache_ttl_ms={ttl}")


def query(db: duckdb.DuckDBPyConnection, text: str = "one") -> dict:
    row = db.execute("SELECT jev_noul(?, 'p')", [text]).fetchone()
    assert row is not None
    return row[0]


def test_repeat_and_clear(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    assert not query(db)["cache_hit"]
    assert query(db)["cache_hit"]
    assert len(stub.calls) == 1
    assert db.execute("SELECT jev_cache_clear()").fetchone() == (True,)
    assert not query(db)["cache_hit"]
    assert len(stub.calls) == 2


def test_non_sliding_expiry(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db, ttl=1000)
    query(db)
    time.sleep(0.6)
    assert query(db)["cache_hit"]
    time.sleep(0.6)
    assert not query(db)["cache_hit"]
    assert len(stub.calls) == 2


def test_lru_eviction(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db, budget=500)
    query(db, "one")
    query(db, "two")
    assert query(db, "one")["cache_hit"]
    query(db, "three")
    assert query(db, "one")["cache_hit"]
    assert not query(db, "two")["cache_hit"]
    assert len(stub.calls) == 4


def test_disabled_and_tiny_budget(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    for budget in (0, 1):
        enable(db, budget=budget)
        assert not query(db)["cache_hit"]
        assert not query(db)["cache_hit"]
    assert len(stub.calls) == 4


def test_model_and_credentials_invalidate(
    db: duckdb.DuckDBPyConnection, stub: Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(db)
    query(db)
    db.execute("SET jev_model='different-model'")
    assert not query(db)["cache_hit"]
    assert query(db)["cache_hit"]
    monkeypatch.setenv("TYPESAFE_API_KEY", "different-test-account")
    assert not query(db)["cache_hit"]
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(duckdb.InvalidInputException, match="TYPESAFE_API_KEY"):
        query(db)
    assert len(stub.calls) == 3


def test_connection_isolation(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    query(db)
    other = connect(stub)
    try:
        enable(other)
        assert not query(other)["cache_hit"]
        assert query(db)["cache_hit"]
        assert len(stub.calls) == 2
    finally:
        other.close()


def test_failed_response_not_retained(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    stub.mode = "range"
    with pytest.raises(duckdb.InvalidInputException):
        query(db)
    stub.mode = "normal"
    assert not query(db)["cache_hit"]
    assert query(db)["cache_hit"]
    assert len(stub.calls) == 2


def test_changes_to_evidence_and_prompt_miss(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    query(db)
    assert not query(db, "different")["cache_hit"]
    row = db.execute("SELECT jev_noul('one', 'different instructions')").fetchone()
    assert row is not None and not row[0]["cache_hit"]
    assert len(stub.calls) == 3


def test_repacking_reuses_results(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    db.execute("SET jev_batch_size=1")
    first = db.execute("SELECT jev_noul({'i':i}, 'p') FROM range(10) t(i)").fetchall()
    db.execute("SET jev_batch_size=25")
    second = db.execute("SELECT jev_noul({'i':i}, 'p') FROM range(10) t(i)").fetchall()
    assert [r[0]["noul"] for r in first] == [r[0]["noul"] for r in second]
    assert all(r[0]["cache_hit"] for r in second)
    assert len(stub.calls) == 10


@pytest.mark.parametrize(
    ("setting", "value"), [("bytes", -1), ("bytes", 67108865), ("ttl_ms", 0), ("ttl_ms", 86400001)]
)
def test_invalid_settings(db: duckdb.DuckDBPyConnection, stub: Stub, setting: str, value: int) -> None:
    db.execute(f"SET jev_session_cache_{setting}={value}")
    with pytest.raises(duckdb.InvalidInputException, match="session cache settings"):
        query(db)
    assert not stub.calls


def test_endpoint_invalidation(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    query(db)
    other = Stub()
    try:
        db.execute("SET jev_endpoint=?", [other.endpoint])
        assert not query(db)["cache_hit"]
        assert len(stub.calls) == len(other.calls) == 1
    finally:
        other.close()


def test_stream_repeat(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    sql = """SELECT * FROM jev_stream((SELECT i, {'i':i},
        '{"p":{"type":"noul","instructions":"p"}}'::JSON FROM range(10) t(i)))"""
    first = db.execute(sql).fetchall()
    second = db.execute(sql).fetchall()
    assert [r[:3] for r in first] == [r[:3] for r in second]
    assert all(r[3] for r in second)
    assert len(stub.calls) == 1
