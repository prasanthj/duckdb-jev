-- Requires the native extension to be loaded and TYPESAFE_API_KEY in the environment.
-- This executes remote inference for 100 synthetic support messages, without a live feed.
SELECT row_id, answers, model, cache_hit
FROM jev_stream((
  SELECT i AS ticket_id,
    {'account_id': i % 10,
     'message': CASE WHEN i % 2 = 0
       THEN 'The exports failed three times. We need an urgent fix before renewal.'
       ELSE 'Thanks, the export fix worked and our team is happy with the service.' END,
     'telemetry': {'export_failures': CASE WHEN i % 2 = 0 THEN 3 ELSE 0 END}},
    '{"sentiment":{"type":"score","instructions":"Assess customer sentiment from the message.",
       "criteria":["Negative","Neutral","Positive"]},
      "route":{"type":"choice","instructions":"Choose the appropriate next action.",
       "criteria":{"escalate":"Unresolved issue needing urgent attention",
                   "monitor":"Resolved or positive feedback"}}}'::JSON
  FROM range(100) AS input(i)
)) ORDER BY row_id;
