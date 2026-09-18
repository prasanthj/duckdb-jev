"""Explicitly opt-in: one HTTP request containing three real Jev questions."""

import json
import os
from pathlib import Path

import duckdb
import pytest
from conftest import EXTENSION


@pytest.mark.skipif(os.environ.get("JEV_RUN_LIVE") != "1", reason="Paid API smoke test is opt-in")
def test_live_three_primitives() -> None:
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    questions = {
        "refund": {"type": "noul", "instructions": "Does the customer explicitly request a refund?"},
        "department": {
            "type": "choice",
            "instructions": "Which team should handle this request?",
            "criteria": {"billing": "Charges, invoices, refunds", "technical": "Software bugs and outages"},
        },
        "sentiment": {
            "type": "score",
            "instructions": "How negative is the customer's tone?",
            "criteria": ["Neutral", "Frustrated", "Extremely angry"],
        },
    }
    try:
        con.execute(f"LOAD '{EXTENSION}'")
        row = con.execute(
            "SELECT jev_eval(?,?::JSON)",
            ["I was charged twice. Please refund the duplicate payment. This is frustrating.", json.dumps(questions)],
        ).fetchone()
        assert row is not None
        result = row[0]
        answers = json.loads(result["answers"])
        assert answers["department"]["choice"] == "billing"
        assert answers["refund"]["noul"] > 0.5
        assert 0 <= answers["sentiment"]["score"] <= 2
        output = Path(__file__).resolve().parents[1] / "benchmarks/results/live-smoke.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"model": result["model"], "answers": answers, "requests": 1}, indent=2) + "\n")
    finally:
        con.close()


@pytest.mark.skipif(os.environ.get("JEV_RUN_LIVE") != "1", reason="Paid API smoke test is opt-in")
def test_live_stream() -> None:
    """One request, two independent rows, then stop."""
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    questions = {"refund": {"type": "noul", "instructions": "Does the text explicitly request a refund?"}}
    try:
        con.execute(f"LOAD '{EXTENSION}'")
        rows = con.execute(
            "SELECT * FROM jev_stream((SELECT id, message, ?::JSON FROM "
            "(VALUES (1,'Please refund the duplicate charge.'), (2,'Thank you, everything works well.')) t(id,message))) "
            "ORDER BY row_id",
            [json.dumps(questions)],
        ).fetchall()
        assert len(rows) == 2
        assert json.loads(rows[0][1])["refund"]["noul"] > 0.5
        assert json.loads(rows[1][1])["refund"]["noul"] < 0.5
        assert all(row[2] and not row[3] for row in rows)
    finally:
        con.close()
