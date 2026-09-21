"""Explicitly opt-in: one HTTP request containing three real Jev questions."""

import json
import os
import time
from pathlib import Path
from typing import Any

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


@pytest.mark.skipif(os.environ.get("JEV_RUN_LIVE") != "1", reason="Paid API equivalence test is opt-in")
def test_live_cross_row_batch_equivalence() -> None:
    """Compare independent requests with cross-row batches for every primitive."""
    con = duckdb.connect(config={"allow_unsigned_extensions": True})
    evidence = [
        {"id": 1, "message": "Please refund the duplicate invoice charge today.", "errors_7d": 0},
        {"id": 2, "message": "Everything is working well. Thank you for the help.", "errors_7d": 0},
        {"id": 3, "message": "Production is down and users cannot log in.", "errors_7d": 37},
        {"id": 4, "message": "Could you explain how to add another workspace?", "errors_7d": 0},
        {"id": 5, "message": "Cancel our renewal unless the security issue is fixed.", "errors_7d": 8},
        {"id": 6, "message": "We want to expand to 500 seats next quarter.", "errors_7d": 1},
        {"id": 7, "message": "The report is occasionally slow, but we have a workaround.", "errors_7d": 3},
        {"id": 8, "message": "Our audit starts tomorrow and SSO configuration is blocked.", "errors_7d": 12},
        {"id": 9, "message": "The new dashboard is excellent and adoption is growing.", "errors_7d": 0},
        {"id": 10, "message": "Invoice address is wrong; please correct it before month end.", "errors_7d": 0},
        {"id": 11, "message": "A minor tooltip typo can wait for the next release.", "errors_7d": 0},
        {"id": 12, "message": "Sensitive customer records may be exposed. Escalate immediately.", "errors_7d": 19},
    ]
    questions = {
        "route": {
            "type": "choice",
            "instructions": "Which team should handle `message`?",
            "criteria": {
                "billing": "Invoices, charges, refunds, or payment details.",
                "technical": "Product failures, configuration, or outages.",
                "sales": "Expansion, purchasing, or additional seats.",
                "success": "Adoption, guidance, feedback, or renewal relationship.",
                "security": "Security, privacy, compliance, or exposed data.",
            },
        },
        "severity": {
            "type": "score",
            "instructions": "Assess operational severity using `message` and `errors_7d`.",
            "criteria": ["Informational", "Minor", "Material", "Critical"],
        },
        "urgent": {
            "type": "noul",
            "instructions": "Does `message` require action within 24 hours?",
        },
    }

    def run(batch: int) -> tuple[list[dict[str, Any]], float, list[str]]:
        con.execute(f"SET jev_batch_size={batch}")
        started = time.monotonic()
        rows = con.execute(
            "WITH inputs AS (SELECT value->>'id' AS id, value::VARCHAR AS evidence FROM json_each(?::JSON)), "
            "judged AS MATERIALIZED (SELECT id, jev_eval(evidence::JSON, ?::JSON) AS result FROM inputs) "
            "SELECT id, result.answers, result.model FROM judged ORDER BY id::INTEGER",
            [json.dumps(evidence), json.dumps(questions)],
        ).fetchall()
        return [json.loads(row[1]) for row in rows], time.monotonic() - started, sorted({str(row[2]) for row in rows})

    try:
        con.execute(f"LOAD '{EXTENSION}'")
        con.execute("SET jev_concurrency=10")
        con.execute("SET jev_max_request_bytes=1048576")
        baseline, baseline_seconds, baseline_models = run(1)
        report: dict[str, Any] = {
            "rows": len(evidence),
            "baseline_seconds": baseline_seconds,
            "models": baseline_models,
            "batches": {},
        }
        repeated_baseline, repeated_seconds, repeated_models = run(1)
        candidates: list[tuple[str, list[dict[str, Any]], float, list[str]]] = [
            ("baseline_repeat", repeated_baseline, repeated_seconds, repeated_models)
        ]
        candidates.extend((str(batch), *run(batch)) for batch in (10, 25, 100))
        for label, candidate, seconds, models in candidates:
            max_delta = 0.0
            field_deltas: dict[str, float] = {}
            choice_mismatches = 0
            decision_mismatches = 0
            for expected, actual in zip(baseline, candidate, strict=True):
                choice_mismatches += actual["route"]["choice"] != expected["route"]["choice"]
                decision_mismatches += (actual["urgent"]["noul"] >= 0.5) != (expected["urgent"]["noul"] >= 0.5)
                for question, fields in {
                    "route": ("confidence",),
                    "severity": ("score", "confidence"),
                    "urgent": ("noul",),
                }.items():
                    for field in fields:
                        delta = abs(actual[question][field] - expected[question][field])
                        max_delta = max(max_delta, delta)
                        name = f"{question}.{field}"
                        field_deltas[name] = max(field_deltas.get(name, 0.0), delta)
                for question in ("route", "severity"):
                    assert actual[question]["probabilities"].keys() == expected[question]["probabilities"].keys()
                    for key in actual[question]["probabilities"]:
                        delta = abs(
                            actual[question]["probabilities"][key] - expected[question]["probabilities"][key]
                        )
                        max_delta = max(max_delta, delta)
                        name = f"{question}.probabilities.{key}"
                        field_deltas[name] = max(field_deltas.get(name, 0.0), delta)
            report["batches"][label] = {
                "seconds": seconds,
                "models": models,
                "choice_mismatches": choice_mismatches,
                "decision_mismatches": decision_mismatches,
                "max_numeric_delta": max_delta,
                "field_max_deltas": field_deltas,
            }
        output = Path(__file__).resolve().parents[1] / "benchmarks/results/live-equivalence.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        assert all(result["choice_mismatches"] == 0 for result in report["batches"].values())
        assert all(result["decision_mismatches"] == 0 for result in report["batches"].values())
        assert all(result["max_numeric_delta"] <= 0.15 for result in report["batches"].values())
    finally:
        con.close()
