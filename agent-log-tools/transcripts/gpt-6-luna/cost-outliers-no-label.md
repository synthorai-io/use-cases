# cost-outliers

**Task.** Find the sessions in this window whose cost is out of line with the others, and say what in the token counts, the models or the number of calls explains each one.

**Scope.** 2026-10-11T11:24:00Z to 2026-10-11T11:32:00Z, no label

**Tools the model called** (4):

1. `list_sessions` {"start": "2026-10-11T11:24:00Z", "end": "2026-10-11T11:32:00Z", "limit": 50} → 26 rows
2. `get_model` {"model": "gpt-6-luna"} → 1 rows
3. `get_model` {"model": "deepseek-v4.1-flash"} → 1 rows
4. `get_model` {"model": "gpt-6.1-sol"} → 1 rows

**Answer.**

Most sessions cost under **$0.0004**; the clearest high-cost outliers are the GPT-6 Luna sessions with large token volumes and the GPT-6.1 Sol session, which used a much pricier model.  
- `replay-mismatch-a14896679e70` cost **$0.00383069** for 6 calls (24,557 input, 1,523 output); nearly all its input tokens (**24,539**) were cache writes. Its counterpart, `replay-mismatch-7c7c695680b5`, cost **$0.00152223** for 6 calls with similar total input (24,702) but **18,844 cached** and only **5,840 cache-write** tokens, plus 1,204 output. Luna cache reads are cheaper than cache writes, explaining much of the cost gap.
- `cache-miss-cd57f1afa7a9` cost **$0.00149658** for 4 calls (5,579 input, 1,599 output; 5,567 cache-write tokens), versus **$0.00111276** for `cache-miss-31f364483f98` (4 calls, 5,729 input, 1,750 output; 4,157 cached and 1,560 cache-write tokens). The first session’s input was almost entirely cache writes rather than cached reads.
- Both `context-growth` sessions were costly: **$0.00156015** for `context-growth-c5c393570ca2` (6 calls, 24,546 input, 1,018 output) and **$0.00174746** for `context-growth-2d11dfe29a08` (6 calls, 8,053 input, 1,756 output). Their large input/cache volumes and, for the second, higher output volume explain the costs.
- `wrong-model-53a0d02ac5cf` cost **$0.0012104** for 4 calls despite only 217 input and 523 output tokens: it used **GPT-6.1 Sol**, alongside Luna, while the similarly sized `wrong-model-84fcafb7384e` cost **$0.0003461** using Luna and DeepSeek. Sol’s listed input/output rates are much higher than Luna’s.
- `reasoning-e2156007f3bb` cost **$0.0006011** versus **$0.0001371** for `reasoning-85c472286a5a`; both used Luna, had 5 calls and 386 input tokens, but the former produced **1,125** output tokens versus **197**.
- `retry-loop-71b013a7d1bb` cost **$0.00041** for **10 calls** (1,345 input, 551 output), compared with **$0.0000974** for `retry-loop-00d8cba07fcd` (2 calls, 269 input, 141 output); its extra calls and tokens account for the increase. Session totals are available, but the tools do not provide a per-call cost breakdown.

_3 model calls, 4 tool calls returning 29 rows (9,728 characters), 11,839 input tokens (6,407 cached), 2,560 output tokens, 21.4 s, $0.0020, on gpt-6-luna._
