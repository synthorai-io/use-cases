# cost-outliers

**Task.** Find the sessions in this window whose cost is out of line with the others, and say what in the token counts, the models or the number of calls explains each one.

**Scope.** 2026-10-11T11:24:00Z to 2026-10-11T11:32:00Z, no label

**Tools the model called** (2):

1. `list_sessions` {"start": "2026-10-11T11:24:00Z", "end": "2026-10-11T11:32:00Z", "limit": 50} → 26 rows
2. `get_pricing` {"search": "gpt-6"} → 1 rows

**Answer.**

Eight sessions stand out. Most sessions cost about $0.00003–$0.0004, so I treated anything above roughly $0.0005 as out of line.

- **replay-mismatch-a14896679e70 ($0.00383, the highest):** It made 6 calls on gpt-6-luna with 24,557 input tokens. 24,539 of those were cache writes and none were cache reads, so the large prompt was re-written to the cache instead of reused. Output was 1,523 tokens.
- **replay-mismatch-7c7c695680b5 ($0.00152):** It has the same shape, with 6 calls and 24,702 input tokens. This time 18,844 were cache reads and 5,840 were cache writes, so the cache partly worked, and the session is still expensive because the prompt is so large.
- **context-growth-2d11dfe29a08 ($0.00175) and context-growth-c5c393570ca2 ($0.00156):** Both made 6 calls on gpt-6-luna. The c5c3 session had 24,546 input tokens, mostly cache reads (17,536). The 2d11 session had only 8,053 input tokens but 1,756 output tokens, and only 1,189 of its input tokens were cache reads. The cost comes from a long prompt that grows with each call, plus heavy output.
- **cache-miss-cd57f1afa7a9 ($0.00150) and cache-miss-31f364483f98 ($0.00111):** Each made 4 calls with about 5.6–5.7k input tokens and about 1.6–1.75k output tokens. cd57 wrote 5,567 tokens to the cache and read none. 31f3 read 4,157 and wrote 1,560. The cost is high for so few calls because of the cache writes and the output.
- **wrong-model-53a0d02ac5cf ($0.00121):** It made only 4 calls with 217 input tokens and 523 output tokens. It is the only session that used gpt-6.1-sol, priced at $2/$10 per million tokens against $0.10/$0.50 for gpt-6-luna. Its sibling wrong-model-84fcafb7384e used deepseek-v4.1-flash with gpt-6-luna and cost $0.00035 for about the same tokens, so the model choice is the cause.
- **reasoning-e2156007f3bb ($0.00060):** It has the same 386 input tokens and 5 calls as reasoning-85c472286a5a ($0.000137). Its output was 1,125 tokens against 197, so the extra cost is output volume.
- **retry-loop-71b013a7d1bb ($0.00041):** It made 10 calls for 1,345 input tokens, against 2 calls for its sibling retry-loop-00d8cba07fcd ($0.0000974). The cost comes from the call count.
- **Pricing gap:** The price list has no cache-write rate, so I can't confirm how much of each cost the cache writes account for.

_3 model calls, 2 tool calls returning 27 rows (8,943 characters), 19,504 input tokens (0 cached), 1,617 output tokens, 15.0 s, $0.0552, on claude-sonnet-5-5._
