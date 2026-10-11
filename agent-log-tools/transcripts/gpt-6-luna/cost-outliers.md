# cost-outliers

**Task.** Find the sessions in this window whose cost is out of line with the others, and say what in the token counts, the models or the number of calls explains each one.

**Scope.** 2026-10-11T11:24:00Z to 2026-10-11T11:32:00Z, feature = support-agent

**Tools the model called** (1):

1. `list_sessions` {"start": "2026-10-11T11:24:00Z", "end": "2026-10-11T11:32:00Z", "labels": {"feature": "support-agent"}, "limit": 50} → 26 rows

**Answer.**

- `wrong-model-53a0d02ac5cf` cost **$0.0012104**, versus **$0.0003461** for `wrong-model-84fcafb7384e`: both made 4 calls and used similar tokens (217/523 vs. 211/525 input/output), but the pricier session used **gpt-6.1-sol** and gpt-6-luna, rather than deepseek-v4.1-flash and gpt-6-luna.
- `reasoning-e2156007f3bb` cost **$0.0006011** for 5 calls, versus **$0.0001371** for `reasoning-85c472286a5a`, also 5 calls: both had 386 input tokens, but the higher-cost session produced **1,125 output tokens** versus 197.
- `retry-loop-71b013a7d1bb` cost **$0.0004100** across **10 calls** (1,345 input, 551 output), compared with **$0.0000974** for `retry-loop-00d8cba07fcd` across 2 calls (269 input, 141 output).
- `replay-mismatch-a14896679e70` was the standout at **$0.00383069**: 6 gpt-6-luna calls, 24,557 input and 1,523 output tokens, including **24,539 cache-write tokens** and no cached tokens.
- Its counterpart, `replay-mismatch-7c7c695680b5`, cost **$0.00152223** for 6 calls, with 24,702 input and 1,204 output tokens; 18,844 input tokens were cached and 5,840 were cache-write tokens.
- `cache-miss-cd57f1afa7a9` cost **$0.00149658** (4 calls; 5,579 input, 1,599 output), versus **$0.00111276** for `cache-miss-31f364483f98` (4 calls; 5,729 input, 1,750 output): the former had 5,567 cache-write tokens and none cached, while the latter had 4,157 cached and 1,560 cache-write tokens.
- The two six-call `context-growth` sessions were also high-cost: `context-growth-2d11dfe29a08` cost **$0.00174746** (8,053 input, 1,756 output) and `context-growth-c5c393570ca2` **$0.00156015** (24,546 input, 1,018 output); both used gpt-6-luna and had thousands of cache-write tokens.
- Higher-than-small-session costs also track multi-call or mixed-model usage: `support-0eb3a905a189` cost **$0.0008979** for 10 calls (1,799 input, 897 output), while the two-call `slow-tool` sessions cost about **$0.00028–$0.00032** with both deepseek-v4.1-flash and gpt-6-luna and 484–575 output tokens.

_2 model calls, 1 tool calls returning 26 rows (7,654 characters), 6,469 input tokens (1,766 cached), 1,789 output tokens, 13.3 s, $0.0015, on gpt-6-luna._
