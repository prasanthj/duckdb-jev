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


def test_default_cache_scope(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    assert db.execute("SELECT current_setting('jev_session_cache_bytes')").fetchone() == (0,)
    rows = db.execute("SELECT jev_noul('one','p') FROM range(4097)").fetchall()
    assert sum(row[0]["cache_hit"] for row in rows) == 4096
    assert len(stub.calls) == 1
    assert not query(db)["cache_hit"]
    assert len(stub.calls) == 2


def test_criteria_change_misses_and_confidence_is_preserved(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    stub.mode = "distinct_confidence"
    sql = "SELECT jev_score('one','p',?::JSON)"
    first = db.execute(sql, ['["negative","positive"]']).fetchone()
    second = db.execute(sql, ['["negative","positive"]']).fetchone()
    assert first is not None and second is not None
    assert first[0]["confidence"] == second[0]["confidence"] == 0.73
    assert max(second[0]["probabilities"].values()) == 0.5
    assert second[0] == {**first[0], "cache_hit": True}
    changed = db.execute(sql, ['["neutral","angry"]']).fetchone()
    assert changed is not None and not changed[0]["cache_hit"]
    assert changed[0]["legend"] != first[0]["legend"]
    assert len(stub.calls) == 2


def test_canonical_keys_preserve_json_types(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    sql = "SELECT jev_noul(?::JSON,'p')"
    original = db.execute(sql, ['{"a":1,"b":null}']).fetchone()
    reordered = db.execute(sql, ['{ "b": null, "a": 1 }']).fetchone()
    assert original is not None and reordered is not None
    assert reordered[0] == {**original[0], "cache_hit": True}
    for evidence in ('{"a":"1","b":null}', '{"a":1,"b":"null"}', '{"a":1}'):
        row = db.execute(sql, [evidence]).fetchone()
        assert row is not None and not row[0]["cache_hit"]
    assert len(stub.calls) == 4


def test_external_access_guard_applies_to_warm_cache(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    query(db)
    assert query(db)["cache_hit"]
    db.execute("SET enable_external_access=false")
    with pytest.raises(duckdb.InvalidInputException, match="external access is disabled"):
        query(db)
    assert len(stub.calls) == 1


@pytest.mark.parametrize(("setting", "value"), [("bytes", 2097152), ("ttl_ms", 120000), ("bytes", 0)])
def test_settings_change_invalidates_existing_entries(
    db: duckdb.DuckDBPyConnection, stub: Stub, setting: str, value: int
) -> None:
    enable(db)
    query(db)
    assert query(db)["cache_hit"]
    db.execute(f"SET jev_session_cache_{setting}={value}")
    assert not query(db)["cache_hit"]
    assert len(stub.calls) == 2
    if value == 0:
        enable(db)
        assert not query(db)["cache_hit"]  # Disable cleared the old cached result.
        assert len(stub.calls) == 3


def test_connection_close_discards_results(stub: Stub) -> None:
    for _ in range(2):
        connection = connect(stub)
        try:
            enable(connection)
            assert not query(connection)["cache_hit"]
            assert query(connection)["cache_hit"]
        finally:
            connection.close()
    assert len(stub.calls) == 2


def test_provider_failure_never_becomes_a_cached_decision(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    stub.status = 429
    with pytest.raises(duckdb.InvalidInputException):
        query(db)
    assert len(stub.calls) == 1  # No hidden retry.
    stub.status = 200
    assert not query(db)["cache_hit"]
    assert query(db)["cache_hit"]
    assert len(stub.calls) == 2


def test_choice_descriptions_are_part_of_cache_key(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    sql = "SELECT jev_choice('one','p',?::JSON)"
    for criteria in ('{"a":"billing","b":"technical"}', '{"a":"security","b":"sales"}'):
        first = db.execute(sql, [criteria]).fetchone()
        second = db.execute(sql, [criteria]).fetchone()
        assert first is not None and not first[0]["cache_hit"]
        assert second is not None and second[0]["cache_hit"]
    assert len(stub.calls) == 2


def test_score_level_order_is_part_of_cache_key(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    sql = "SELECT jev_score('one','p',?::JSON)"
    first = db.execute(sql, ['["negative","positive"]']).fetchone()
    second = db.execute(sql, ['["positive","negative"]']).fetchone()
    assert first is not None and second is not None
    assert not second[0]["cache_hit"]
    assert first[0]["legend"] != second[0]["legend"]
    assert len(stub.calls) == 2


def test_threshold_changes_reuse_probability(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    assert query(db)["noul"] == 0.9
    assert db.execute("SELECT jev('one','p',.8)").fetchone() == (True,)
    assert db.execute("SELECT jev('one','p',.95)").fetchone() == (False,)
    assert len(stub.calls) == 1


def test_session_cache_independent_of_query_cache_budget(db: duckdb.DuckDBPyConnection, stub: Stub) -> None:
    enable(db)
    db.execute("SET jev_cache_bytes=0")
    assert not query(db)["cache_hit"]
    assert query(db)["cache_hit"]
    assert len(stub.calls) == 1
