# Agent tracing demo

A small support agent whose model calls show up in the Synthorai console as
traces and sessions, with no tracing SDK and no collector. The whole tracing
layer is [`tracing.py`](./tracing.py): a few ids and one JSON header.

Guides that use this code:

- [Trace an LLM agent](https://synthorai.io/use-cases/agent-tracing/)
- [Why is my LLM agent slow?](https://synthorai.io/use-cases/agent-latency/)
- [Why is my LLM agent expensive?](https://synthorai.io/use-cases/agent-cost/)
- [Debug an LLM agent](https://synthorai.io/use-cases/agent-debugging/)

## Run it

```bash
git clone https://github.com/synthorai-io/use-cases
cd use-cases
cp .env.example .env    # put your API key in it
cd agent-tracing
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py --fail
```

It needs Python 3.10 or later. It runs two conversations of two turns each
(21 model calls, under $0.002 at list prices), prints the session and trace
ids, then prints what each call cost.
Open **Traces** in the [console](https://synthorai.io/console/traces) and the
same ids are listed there.

A call whose connection drops before any response is sent again, up to twice.
The attempt that got no response is printed with status `0` and no cost. When
we saw one, it had not reached the gateway: it was in the output and not in
the console.

## What is in here

| File | What it does |
|---|---|
| [`tracing.py`](./tracing.py) | Builds the headers for one call: `X-Session-Id`, `X-Trace-Id`, `X-Span-Id`, `X-Agent-Id` and `X-Synthorai-Metadata` |
| [`agent.py`](./agent.py) | The agent: a planning call with tools, two local tools, three parallel checks by a sub-agent, and an answer. Every model call goes through `call()` |
| [`run.py`](./run.py) | Runs the conversations and reads each call's billing record from `GET /v1/generation` |
| [`incidents.py`](./incidents.py) | Ten problems an agent has in production, each run twice: broken, then fixed |
| [`policy_manual.md`](./policy_manual.md) | A long, stable system prompt for the caching case |

## What one turn looks like

```
plan            the main agent reads the message and asks for tools
(your app)      the tools run locally: an order lookup and a policy search
sentiment    \
policy-check  }  three checks by the "analyst" sub-agent, in parallel
risk         /
answer          the main agent writes the reply
```

With `--fail`, the `policy-check` call of one turn first names a model that does
not exist, gets a 404, and is retried as `policy-check.retry`, so the trace
shows a failed call next to its retry.

## Incidents

```bash
python incidents.py                  # all ten, about $0.03
python incidents.py cache-miss       # one
```

Every call is labelled with `case` and `variant` (`before` or `after`), so both
runs of a case can be found in the console with `metadata.case = cache-miss`.
Each result is saved to `.data/incidents.json` as soon as its run finishes, and
a case that fails (a timeout, an outage) is reported and skipped, not fatal.

The fixed `cache-miss` run depends on what is already cached. The first time
you run it, the first turn has nothing to read and the session comes out near
73% cached: 0% on the first turn, then 93% to 95%. Run it again within a few
minutes and the first turn hits too, for about 94%.

| Case | The problem | Guide |
|---|---|---|
| `serial-checks` | three independent model calls made one after another | latency |
| `slow-tool` | the wait is in your own code, not in the model | latency |
| `reasoning` | the model thinks before one-sentence answers that do not need it | latency |
| `cache-miss` | a timestamp at the top of the prompt defeats prompt caching | cost |
| `context-growth` | every old lookup result is sent again on every later turn | cost |
| `retry-loop` | a reply check that cannot pass while a tool is down | cost |
| `replay-mismatch` | a user message is saved without a suffix it was sent with, so a long history never hits the cache | cost |
| `wrong-model` | a frontier model does one-sentence classification | cost |
| `cut-off` | an output cap ends every reply before it starts | debugging |
| `bad-history` | the saved conversation drops a tool call, so the second turn is rejected | debugging |

## Settings

| Variable | Default | |
|---|---|---|
| `SYNTHORAI_API_KEY` | none | Read from `.env` here or at the repo root |
| `BASE_URL` | `https://synthorai.io/v1` | The model calls work on any OpenAI-compatible endpoint. The headers and the cost lookup mean something only on the gateway |
| `PLANNER_MODEL` | `gpt-6-luna` | The main agent. It needs function calling, and has to answer after one round of tools: the demo does not loop when a model asks for a second round |
| `ANALYST_MODEL` | `deepseek-v4-flash` | The sub-agent |
| `REQUEST_TIMEOUT` | `300` | Seconds the client waits for one model call. Model calls can take minutes |
