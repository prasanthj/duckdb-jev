"""Deterministic performance contracts, not vendor latency claims."""

import duckdb
import pytest
from conftest import Stub


@pytest.mark.parametrize(("batch", "calls"), [(1, 200), (25, 8), (100, 2), (1000, 1)])
def test_batch_request_reduction(db: duckdb.DuckDBPyConnection, stub: Stub, batch: int, calls: int) -> None:
    db.execute(f"SET jev_batch_size={batch}")
    db.execute("SET jev_max_request_bytes=1048576")
    rows = db.execute("SELECT (jev_noul({'i':i},'p')).noul FROM range(200) t(i)").fetchall()
    assert rows == [((i % 10) / 10,) for i in range(200)]
    assert len(stub.calls) == calls


@pytest.mark.parametrize("concurrency", [1, 4, 10])
def test_concurrency_limit(db: duckdb.DuckDBPyConnection, stub: Stub, concurrency: int) -> None:
    stub.delay = 0.03
    db.execute("SET jev_batch_size=1")
    db.execute(f"SET jev_concurrency={concurrency}")
    db.execute("SELECT jev_noul({'i':i},'p') FROM range(40) t(i)").fetchall()
    assert stub.peak == concurrency


def test_repeated_rows_cache_scope(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    rows = db.execute("SELECT jev_noul('same','p') FROM range(4097)").fetchall()
    assert len(rows) == 4097
    assert len(stub.calls) == 1
    assert sum(row[0]["cache_hit"] for row in rows) == 4096
    db.execute("SELECT jev_noul('same','p')").fetchall()
    assert len(stub.calls) == 2  # No reuse across statements.
