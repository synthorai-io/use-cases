# failures

**Task.** Which requests failed in this window? Group them by error, and say which sessions and which users they affected.

**Scope.** 2026-10-11T11:24:00Z to 2026-10-11T11:32:00Z, feature = support-agent

**Tools the model called** (1):

1. `search_requests` {"start": "2026-10-11T11:24:00Z", "end": "2026-10-11T11:32:00Z", "labels": {"feature": "support-agent"}, "status": "error", "limit": 50} → 2 rows

**Answer.**

Two requests failed:

- **Invalid request — no tool-call output found** (`call_id: call_Nqc1zUFvwLLuF5oGMEE9WheQ`): request `<id removed>`, session `bad-history-538825d8d546`. The request metadata does not identify a user. Cost: **$0**; tokens: **0**.
- **Model unavailable** (`model-that-does-not-exist`): request `<id removed>`, session `support-d8ac3cdd4411`, user `u_1001`. Cost: **$0**; tokens: **0**.

_2 model calls, 1 tool calls returning 2 rows (1,619 characters), 4,123 input tokens (3,480 cached), 424 output tokens, 9.4 s, $0.0003, on gpt-6-luna._
