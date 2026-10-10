# cache-miss-before-after

**Question.** For the label case = cache-miss, compare variant = before with variant = after: input tokens, cached tokens and cost of each.

**Tools the agent called** (2):

1. `search_requests` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent", "case": "cache-miss", "variant": "before"}, "limit": 50}
2. `search_requests` {"start": "2026-10-08T05:54:00Z", "end": "2026-10-08T06:00:00Z", "labels": {"feature": "support-agent", "case": "cache-miss", "variant": "after"}, "limit": 50}

**Answer.**

Both variants made four `gpt-6-luna` requests (turns 1–4), and the "after" variant cost about 26% less, mostly because it read from the cache.

| Variant | Session | Input tokens | Cached tokens | Cost |
|---|---|---|---|---|
| before | `cache-miss-bf78f5ff7fc3` | 5,612 | 0 | $0.001447 |
| after | `cache-miss-bda267bad923` | 5,590 | 3,735 | $0.001064 |

- **Before:** no request read from the cache. Each turn wrote its whole prompt to the cache (1,292 to 1,500 tokens per turn).
- **After:** turn 1 wrote 1,292 tokens, as in "before". Turns 2–4 each read 1,245 cached tokens and wrote only 113, 188 and 250 new tokens.
- **Saving:** about $0.000383 across the four turns.

The tools don't report why the caching changed, only the token and cost figures.

_3 turns, 14.5 s, $0.0342 for the agent's own model calls on claude-sonnet-5-5._
