# most-expensive-session

**Question.** Which session with the label case = cache-miss, case = context-growth or case = replay-mismatch cost the most, and what in its token counts explains the cost?

**Tools the agent called** (4):

1. `list_sessions` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent"}, "limit": 50}
2. `list_sessions` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent", "case": "replay-mismatch"}}
3. `list_sessions` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent", "case": "context-growth"}}
4. `list_sessions` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent", "case": "cache-miss"}}

**Answer.**

The most expensive session was `replay-mismatch-ad1904ac1cd1`, at $0.00366794 over 6 requests on gpt-6-luna. That is about twice the next one, `context-growth-8103d931edd4` at $0.00180801.

- **Cache reuse:** The session read 0 of its 24,459 input tokens from cache. It wrote 24,441 tokens to cache, which is almost the whole prompt, and cache writes cost more than ordinary input. Every request therefore paid full price for the prompt.
- **Comparison:** `replay-mismatch-acdbca8572b0` had nearly the same input (24,578 tokens) but read 18,735 of them from cache and wrote only 5,825. It cost $0.00136126. Roughly 24k of input is the same in both sessions, so the cache-read difference is what explains the gap.
- **Output:** Output tokens were 1,222, which is not unusual next to the other sessions' 888 to 1,692. They don't explain the cost.
- **Limits of the tools:** They give per-session totals only, not per-request token counts or per-token prices. So I can't say exactly why the cache missed (for example, a changed prompt prefix), and I can't split the cost into its cache-write, input and output parts.

_5 turns, 17.2 s, $0.0943 for the agent's own model calls on claude-sonnet-5-5._
