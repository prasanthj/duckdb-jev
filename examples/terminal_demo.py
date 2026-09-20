"""Small live terminal walkthrough. Requires TYPESAFE_API_KEY; at most four unique judgments."""

import os
import time
from pathlib import Path

import duckdb


def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise SystemExit("Set TYPESAFE_API_KEY before running this live example.")
    root = Path(__file__).resolve().parents[1]
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    print("\033[1;36mJEV / DUCKDB\033[0m  Semantic SQL, batched inference, cached reuse\n", flush=True)
    print("D LOAD 'build/extension/jev/jev.duckdb_extension';")
    con.execute(f"LOAD '{root / 'build/extension/jev/jev.duckdb_extension'}'")
    con.execute("SET jev_session_cache_bytes=8388608")
    con.execute("SET jev_batch_size=25")
    print("D SET jev_session_cache_bytes = 8388608;  -- enable cross-query reuse\n")
    con.execute("""CREATE TABLE accounts AS SELECT * FROM (VALUES
      ('Maple Labs', 92, 0.2, 'Great rollout. We plan to expand next quarter.'),
      ('Harbor Cloud', 24, 12.0, 'Still blocked. We will cancel unless this is fixed.'),
      ('Northstar', 58, 2.5, 'Usage is slipping; please help us improve adoption.'),
      ('Cedar Works', 88, 0.1, 'Support was excellent. Renewal has been approved.')
    ) t(account, active_pct, error_pct, support_message)""")
    print("D SELECT * FROM accounts;  -- synthetic account evidence")
    con.sql("SELECT * FROM accounts").show(max_width=125)
    sql = """SELECT account, jev_choice(
  {'telemetry': {'active_pct': active_pct, 'error_pct': error_pct},
   'support': support_message},
  'Classify renewal risk from product telemetry and customer feedback.',
  '{"green":"Healthy adoption and positive renewal signals",
    "yellow":"Adoption concerns; recovery or engagement needed",
    "red":"Severe blockers or explicit cancellation risk"}'::JSON
) AS risk FROM accounts"""
    print("\033[36mD " + sql.replace("\n", "\n  ") + ";\033[0m", flush=True)
    start = time.perf_counter()
    con.execute("CREATE TEMP TABLE cold AS " + sql)
    cold_ms = (time.perf_counter() - start) * 1000
    print("\nD SELECT account, risk.choice, round(risk.confidence, 3) AS confidence FROM cold;")
    con.sql("SELECT account, risk.choice AS renewal_risk, round(risk.confidence, 3) AS confidence FROM cold").show()
    start = time.perf_counter()
    warm = con.execute(sql).fetchall()
    warm_ms = (time.perf_counter() - start) * 1000
    hits = sum(bool(row[1]["cache_hit"]) for row in warm)
    assert [(r[0], r[1]["choice"]) for r in warm] == con.execute("SELECT account, risk.choice FROM cold").fetchall()
    print(f"\033[1;32mCold query: {cold_ms:,.1f} ms  |  Repeat query: {warm_ms:,.1f} ms  |  Cache hits: {hits}/{len(warm)}\033[0m")
    print("Real Jev endpoint • synthetic data • measured on this machine • no inference on cache hits", flush=True)
    con.close()


if __name__ == "__main__":
    main()
