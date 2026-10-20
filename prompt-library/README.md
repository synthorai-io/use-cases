# Prompt library demo

Keep the support agent's prompts in the Synthorai prompt library instead of in
the code: call them by id, ship a new version by moving a label, send a share
of calls to a candidate, and test a version before it ships. It is the support
agent of the [agent tracing demo](../agent-tracing/) with its prompts moved
out, and every call still carries the same session and trace headers.

It is the code behind three guides:
[Prompt versioning](https://synthorai.io/use-cases/prompt-versioning/),
[A/B test a prompt change](https://synthorai.io/use-cases/prompt-rollout/) and
[Test a prompt before it ships](https://synthorai.io/use-cases/prompt-testing/).

## Results

From runs on 2026-10-19 and 2026-10-20. Every figure comes from the rows in
`results/`.

**Calling by id and shipping** (`call.py`, `ship.py`, `errors.py`)

- A call by `prompt_id` sent a median of 114.5 prompt tokens over 20 pairs,
  the same as the rendered request sent inline. Median client latency was
  4.1 s by id and 4.6 s inline.
- With a call every 0.5 s, the first call answered by the new version was
  sent a median 8.52 s after a label move from v1 to v3 (8.03 to 9.03 s over 5
  moves), and 8.02 s after a move from v3 back to v1 (8.02 to 9.02 s). After
  15 s without calls it was 0.0 s, in all 10 moves. In none of the 20 moves
  did a call get the old version after one had got the new one.
- The five calls that cannot be served failed with 400 or 404 before any model
  ran, and none of them was billed.

**Rollout** (`rollout.py`, `effort.py`)

- At a 10% share, 16 of 200 sessions (8.0%, 95% interval 5.0 to 12.6%) went
  to v3; at 50%, 111 of 200 (55.5%, 48.6 to 62.2%). All 400 sessions and all
  100 end users named by `_user` kept one version, and none of the 40 calls
  pinned to `prompt_version=1` went to v3.
- After the gateway fix of October 2026, all 50 users named only by the body's
  `user` and all 20 named only by `safety_identifier` kept one version. Before
  it, 27 of 50 body users got both.
- Read back from Logs per version: v1 answered 1,272 calls at $0.000110 each
  and a median 4.9 s, v3 568 calls at $0.000213 and 3.1 s, 1.94 times v1 per
  call. At the 10% share alone it was $0.000212 over 71 calls against
  $0.000111 over 749, 1.92 times. v3 wrote 321 output tokens per call on
  average against 198, of them 256 reasoning tokens against 149.
- In the gateway's peak band (the 140-call check on 2026-10-20), v3 cost
  $0.000394 per call against $0.000107 for v1, 3.7 times, on 66 and 74 calls.
- `reasoning_effort` sent by the caller, 50 calls per cell on the 40-row
  orders messages:

  | Version | `reasoning_effort` | Cost per call | Output tokens, mean | Median latency | Order number | Judged to answer the question |
  |---|---|---|---|---|---|---|
  | v1 | default | $0.000107 | 192 | 4.8 s | 50/50 | 37/50 |
  | v1 | none | $0.000034 | 45 | 1.8 s | 50/50 | 35/50 |
  | v3 | default | $0.000200 | 300 | 3.2 s | 50/50 | 43/50 |
  | v3 | none | $0.000049 | 55 | 1.7 s | 50/50 | 39/50 |

  "none" against the default, Fisher's exact test on the judged check: p = 0.82
  for v1 and 0.44 for v3.

**Testing** (`evals.py`)

- In the test fixed in advance, on 110 rows that no earlier run had used, v2
  stated the order number in 92 of 110 answers and v1 in 110 of 110:
  two-sided Fisher's exact test p = 3.6 × 10⁻⁶. By set, v2 had 33 of 40,
  27 of 35 and 32 of 35.
- On the 40-row orders set, v2 had 35, 36 and 40 of 40 in runs 7 to 9 and 39
  of 40 in run 10, started from the console; v1 had 40 of 40 every time (in
  run 10 by this script's grading of the stored answers). One of those runs
  passed v2 on every row.
- v1 stated the order number in all 275 calls `evals.py` made.
- On the general set, neither version wrote "refund by" or "business days" in
  three runs.

All of it, every run and rerun, cost $0.78.

## Run it

You need Python 3.10 or later and two keys in `.env` at the repo root:

| Key | What it does here | Give it |
|---|---|---|
| `SYNTHORAI_API_KEY` | every model call, by prompt id or not | your usual inference key |
| `SYNTHORAI_PROMPTS_ADMIN_KEY` | saves versions, moves labels, sets a rollout, records eval runs | an admin key with `prompts.write` and `evals.write` |

`rollout.py` and `call.py` also read Logs with `SYNTHORAI_ADMIN_KEY` (scope
`logs.read`) when it is set.

```bash
git clone https://github.com/synthorai-io/use-cases
cd use-cases
cp .env.example .env    # put the keys in it
cd prompt-library
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup.py         # the two prompts and their versions; safe to run again
python call.py          # every way to call a prompt
python ship.py          # move production and time it
python errors.py        # what a call that cannot be served answers
python rollout.py       # 10% and 50% of production to another version
python effort.py        # what a caller-sent reasoning_effort does to each version
python evals.py         # v2 against v1 on the orders and general sets, three runs each
python evals.py --set fresh --runs 1    # the test fixed in advance
python evals.py --read RUN_ID           # read back a run started in the console
python render.py support-answer 3       # what a call by id would send, without calling a model
```

## The prompts

| Prompt | Version | Change |
|---|---|---|
| `support-answer` | v1 | the baseline, on `gpt-6-luna` |
| `support-answer` | v2 | shorter system text that drops "always state the order number" |
| `support-answer` | v3 | v1's text on `deepseek-v4.1-flash` |
| `support-answer` | v4 | v1 plus a `{{tone}}` variable with no default |
| `policy-check` | v1 | the sub-agent's check, as JSON that follows a schema, on `deepseek-v4.1-flash` |

`setup.py` points `production` at v1 and `staging` at v2.

## The scripts

| Script | What it shows |
|---|---|
| `setup.py` | creating prompts and saving versions through `/api/v1/prompts`, idempotently |
| `render.py` | the request a call by id turns into, from `POST /v1/prompts/{id}/render`: no model call, not billed |
| `call.py` | one id called by production, by label and by version; the same call on Chat Completions, Responses and Messages; `render`; a call by id against the same request inline; round 2 of a tool loop |
| `ship.py` | seconds from a label move to the first call the new version answers, 5 moves each way |
| `errors.py` | the status, code and cost of five calls that cannot be served |
| `rollout.py` | how a rollout splits sessions, end users (`_user`, the body's `user`, `safety_identifier`), calls with none of them, and pinned calls, and the per-version figures read back from Logs (`--logs START END` for an earlier run) |
| `effort.py` | v1 and v3 with and without a caller-sent `reasoning_effort: "none"`: cost, tokens, latency and the order-number check |
| `evals.py` | v2 against v1 on test sets: orders (40 rows) and general (20), three runs each, and three fresh sets of 40, 35 and 35 rows for the test fixed in advance; pass counts per check, Fisher's exact test, which rows failed in how many runs, and `--read RUN_ID` for a run started in the console. Every call is labelled `source: eval` |
| `webhook.py` | verifying the signature of a prompt-change webhook |
| `migrate.py` | an OpenAI Responses call with `prompt: {id: "pmpt_..."}` served from the library unchanged |

Every model call is one row in `results/<script>.jsonl`: the version that
answered (from `X-Synthorai-Prompt-Version`), status, error code, tokens,
client latency, request id and the cost from its billing record. Every
customer message carries a short random tag, so no two calls are the same
request. Each script prints what the demo has spent so far.

In `results/rollout.jsonl` the body-user rows from before the gateway fix keep
their own cases: "10%: body user (before fix, one call per user)" and "50%:
body user (before fix, one call per user)" from the main rollout, where each
call had its own user, and "50%: body user (before fix)" for the 50 users × 2
calls on 2026-10-19. "50%: body user" and "50%: safety id" are the check after
the fix, on 2026-10-20.

All DeepSeek calls ran in the gateway's off-peak band (22:00 to 08:00 Beijing
time) except the 66 v3 calls in the post-fix rollout check (2026-10-20 05:24
UTC), which ran in the peak band.

## Settings

| Variable | Default | |
|---|---|---|
| `SYNTHORAI_API_KEY` | none | Model calls |
| `SYNTHORAI_PROMPTS_ADMIN_KEY` | none | Prompt and eval writes |
| `SYNTHORAI_ADMIN_KEY` | none | Optional: reads Logs |
| `BASE_URL` | `https://synthorai.io/v1` | The inference API |
| `API_URL` | `https://synthorai.io/api/v1` | The management API |
| `CONSOLE_URL` | `https://synthorai.io/console` | The console links the scripts print |
