-- Requires the native Jev extension loaded and account_signals populated.
-- Bound the evidence before any model calls.
CREATE TEMP TABLE renewal_candidates AS
SELECT account_id, struct_pack(
    support_text := support_text,
    active_users_30d := active_users_30d,
    active_users_previous_30d := active_users_previous_30d,
    unresolved_escalations := unresolved_escalations
) AS evidence
FROM account_signals
WHERE renewal_date BETWEEN DATE '2026-10-01' AND DATE '2026-12-31';

-- One multi-question evaluation per row, vectorized across rows internally.
CREATE TEMP TABLE renewal_judgments AS
SELECT account_id, jev_eval(to_json(evidence), '{
  "risk": {
    "type": "choice",
    "instructions": "Classify renewal risk using only the supplied account evidence.",
    "criteria": {
      "red": "Explicit cancellation intent or severe unresolved escalation with declining adoption",
      "yellow": "Concern or declining adoption without clear cancellation intent",
      "green": "Healthy adoption and positive or neutral support evidence",
      "unknown": "Insufficient evidence to judge"
    }
  },
  "sentiment": {
    "type": "score",
    "instructions": "Rate the sentiment expressed in support_text; telemetry alone is not sentiment.",
    "criteria": ["Strongly negative", "Negative", "Neutral", "Positive", "Strongly positive"]
  },
  "explicit_exit": {
    "type": "noul",
    "instructions": "Does support_text explicitly state an intention to cancel or not renew?"
  }
}'::JSON) AS judgment
FROM renewal_candidates;

SELECT account_id,
       judgment.answers->'risk' AS risk_with_confidence,
       judgment.answers->'sentiment' AS sentiment_with_distribution,
       judgment.answers->'explicit_exit' AS exit_probability
FROM renewal_judgments;

-- Separate convenience predicate example; this is an additional evaluation.
SELECT account_id FROM renewal_candidates
WHERE jev(to_json(evidence), 'Does the support text explicitly request urgent escalation?', 0.8);
