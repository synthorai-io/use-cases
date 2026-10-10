# failed-requests-by-user

**Question.** Did any request with the label _user = u_1001 fail? Give the status code, the error and how many times it happened. Then say whether _user = u_1002 had any failures.

**Tools the agent called** (2):

1. `search_requests` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent", "_user": "u_1001"}, "limit": 50}
2. `search_requests` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent", "_user": "u_1002"}, "limit": 50}

**Answer.**

Yes, one `_user = u_1001` request failed. It was a 404 with the error "The model "model-that-does-not-exist" does not exist or is not available to this workspace". It happened once. It was request <id removed>, span `policy-check`, in session `support-db6dde1bf967` and trace `trace-ae86db4e3fc4`, at 05:55:02Z. It cost $0 and used 0 tokens. The same trace then ran a `policy-check.retry` span on `deepseek-v4-flash`, which returned 200 (115 input and 133 output tokens, $0.00005245).

`u_1001` made 11 requests in the window, so 10 succeeded. `u_1002` had no failures: all 10 of its requests, in session `support-28df0287b426`, returned 200 with no errors. The tools returned everything for both users in a single page each, so I found nothing further to report.

_3 turns, 11.1 s, $0.0425 for the agent's own model calls on claude-sonnet-5-5._
