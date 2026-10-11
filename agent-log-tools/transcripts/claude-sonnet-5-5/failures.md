# failures

**Task.** Which requests failed in this window? Group them by error, and say which sessions and which users they affected.

**Scope.** 2026-10-11T11:24:00Z to 2026-10-11T11:32:00Z, feature = support-agent

**Tools the model called** (1):

1. `search_requests` {"start": "2026-10-11T11:24:00Z", "end": "2026-10-11T11:32:00Z", "labels": {"feature": "support-agent"}, "status": "error", "limit": 50} → 2 rows

**Answer.**

Two support-agent requests failed between 11:24 and 11:32 UTC on 2026-10-11. Each had a different error, so there are two groups. Both cost $0.00 and used 0 tokens.

1. **Invalid request, HTTP 400:** the request at 11:29:58Z (id `<id removed>`) used model `gpt-6-luna`.
   - The error was "No tool call found for function call output with call_id call_Nqc1zUFvwLLuF5oGMEE9WheQ". The conversation history contained a tool result with no matching tool call.
   - It affected session `bad-history-538825d8d546` (trace `trace-b989b7b8612f`, span `plan`) and failed after 524 ms.
   - The request has no user in its metadata, so I can't say which user it affected. Its metadata is case=bad-history and variant=before.

2. **Model not found, HTTP 404:** the request at 11:25:01Z (id `<id removed>`) asked for `model-that-does-not-exist`.
   - The error said the model "does not exist or is not available to this workspace".
   - It affected session `support-d8ac3cdd4411` (trace `trace-6a2bd243ec97`, agent `analyst`, span `policy-check`) and user `u_1001`. It failed after 95 ms, and its metadata shows release v2.

The results had no further pages (`has_more` was false).

_2 model calls, 1 tool calls returning 2 rows (1,619 characters), 7,784 input tokens (0 cached), 702 output tokens, 7.3 s, $0.0226, on claude-sonnet-5-5._
