"""Query lifecycle and bounded cross-chunk reuse contracts."""

import duckdb
import pytest
from conftest import Stub, connect


@pytest.mark.parametrize("budget", [0, 1, 1024])
def test_budget_bypasses_without_losing_rows(db: duckdb.DuckDBPyConnection, stub: Stub, budget: int) -> None:
    db.execute(f"SET jev_cache_bytes={budget}")
    rows = db.execute("SELECT jev_noul('same','p') FROM range(4097)").fetchall()
    assert len(rows) == 4097
    assert all(row[0]["noul"] == 0.9 for row in rows)
    assert len(stub.calls) == (1 if budget == 1024 else 3)


def test_statement_lifetime_inside_transaction(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("BEGIN")
    for _ in range(2):
        rows = db.execute("SELECT jev_noul('same','p') FROM range(4097)").fetchall()
        assert sum(row[0]["cache_hit"] for row in rows) == 4096
    db.execute("ROLLBACK")
    assert len(stub.calls) == 2


def test_prepared_reexecution_refreshes_options(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("PREPARE p AS SELECT jev_noul('same','p') FROM range(4097)")
    assert not stub.calls
    for model in ("model-one", "model-two"):
        db.execute("SET jev_model=?", [model])
        db.execute("EXECUTE p").fetchall()
    assert [c["body"]["model"] for c in stub.calls] == ["model-one", "model-two"]


def test_thresholds_share_underlying_answer(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    row = db.execute("SELECT jev('same','p',0.8), jev('same','p',0.95), jev_noul('same','p')").fetchone()
    assert row is not None
    assert row[:2] == (True, False)
    assert row[2]["noul"] == 0.9
    assert len(stub.calls) == 1


def test_distinct_questions_do_not_collide(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SELECT jev_noul('same','p'), jev_noul('same','q')").fetchall()
    assert len(stub.calls) == 2
    row = db.execute(
        "SELECT jev_choice({'i':0},'p','{\"a\":\"a\",\"b\":\"b\"}'), "
        "jev_choice({'i':0},'p','{\"c\":\"c\",\"d\":\"d\"}')"
    ).fetchone()
    assert row is not None
    assert row[0]["choice"] == "a" and row[1]["choice"] == "c"
    assert len(stub.calls) == 4


def test_failed_statement_clears_earlier_success(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    with pytest.raises(duckdb.InvalidInputException):
        db.execute("SELECT jev_noul('same','p'), jev_noul('same','q', '{\"invalid\":true}')").fetchall()
    before = len(stub.calls)
    assert before > 0  # The failed query successfully evaluated its first expression.
    db.execute("SELECT jev_noul('same','p')").fetchall()
    assert len(stub.calls) == before + 1


def test_key_is_reloaded_each_statement(
    db: duckdb.DuckDBPyConnection, stub: Stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.execute("SELECT jev_noul('same','p')").fetchall()
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(duckdb.InvalidInputException, match="TYPESAFE_API_KEY"):
        db.execute("SELECT jev_noul('same','p')").fetchall()
    assert len(stub.calls) == 1


def test_connection_isolation(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    other = connect(stub)
    try:
        db.execute("SELECT jev_noul('same','p') FROM range(4097)").fetchall()
        other.execute("SELECT jev_noul('same','p') FROM range(4097)").fetchall()
        assert len(stub.calls) == 2
    finally:
        other.close()


@pytest.mark.parametrize("budget", [-1, 67108865])
def test_invalid_budget(db: duckdb.DuckDBPyConnection, stub: Stub, budget: int) -> None:
    db.execute(f"SET jev_cache_bytes={budget}")
    with pytest.raises(duckdb.InvalidInputException, match="jev_cache_bytes"):
        db.execute("SELECT jev_noul('same','p')").fetchall()
    assert not stub.calls


def test_entry_cap_preserves_mixed_hits_and_misses(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET threads=1")
    rows = db.execute("SELECT jev_noul({'i': i%5000}, 'p') FROM range(10000) t(i)").fetchall()
    assert [row[0]["noul"] for row in rows] == [(i % 10) / 10 for i in range(10000)]
    assert sum(len(call["body"]["questions"]) for call in stub.calls) == 5904
    assert sum(row[0]["cache_hit"] for row in rows) == 4096


def test_partial_byte_budget_preserves_results(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET jev_cache_bytes=512")
    rows = db.execute("SELECT jev_noul({'i': i%10}, 'p') FROM range(4097) t(i)").fetchall()
    assert [row[0]["noul"] for row in rows] == [(i % 10) / 10 for i in range(4097)]
    # Some completed entries fit, but not all ten. Later chunks reuse only those.
    sent = sum(len(call["body"]["questions"]) for call in stub.calls)
    assert 10 < sent < 21


def test_parallel_scan_shared_state(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    db.execute("SET threads=4")
    db.execute("CREATE TABLE parallel_input AS SELECT i, i%10 AS evidence FROM range(500000) t(i)")
    rows = db.execute(
        "SELECT i, (jev_noul({'i':evidence}, 'p')).noul FROM parallel_input ORDER BY i"
    ).fetchall()
    assert len(rows) == 500000
    assert all(value == (i % 10) / 10 for i, value in rows)
    # Simultaneous cold misses share flights, then completed answers are reused.
    assert sum(len(call["body"]["questions"]) for call in stub.calls) == 10
    before = len(stub.calls)
    db.execute("SELECT jev_noul({'i':0},'p')").fetchall()
    assert len(stub.calls) == before + 1
