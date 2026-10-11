# Agent log tools demo

Give your own agent the Synthorai gateway's request log as a set of tools, so
it can look into cost, caching and failures by itself: an on-call bot, a daily
cost check, a step inside a larger agent. One Python file connects to the
gateway's MCP server with a read-only key and runs a short tool loop. There is
no agent framework and no MCP library in it.

Guide: [Give your agent log tools](https://synthorai.io/use-cases/agent-log-tools/).

It also shows when not to do this. A model that reads your log costs tokens,
and a query you already know how to write needs none.

| Who is asking | Use | Model tokens |
|---|---|---|
| A person, ad hoc | the chat window of claude.ai, with the gateway added as a connector | the chat's own |
| A program with a fixed query | the REST API: `python ask.py --rest` | none |
| Your own agent, which has to decide what to look at | MCP in the program: `python ask.py` | yours to pay |

**Set two things before you run it.** Every path looks at one time range and
one label, and the defaults point at a run we made:

| Variable | Default | Set it to |
|---|---|---|
| `LOG_WINDOW` | `2026-10-11T11:24:00Z to 2026-10-11T11:32:00Z` | a time range that holds your own requests |
| `LOG_LABEL` | `feature=support-agent` | a label your requests carry in `X-Synthorai-Metadata` |

With your key and the defaults, every path finds nothing.

## Run it

You need Python 3.10 or later and two keys in `.env` at the repo root:

| Key | What it does here | Give it |
|---|---|---|
| `SYNTHORAI_ADMIN_KEY` | reads the request log | an admin key with the `logs.read` scope and nothing else |
| `SYNTHORAI_API_KEY` | pays for the model's own calls | your usual inference key |

An admin key cannot call a model, and an inference key cannot read the log, so
neither key can do the other's job.

```bash
git clone https://github.com/synthorai-io/use-cases
cd use-cases
cp .env.example .env    # put both keys in it
cd agent-log-tools
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export LOG_WINDOW="2026-10-11T11:24:00Z to 2026-10-11T11:32:00Z"   # yours
export LOG_LABEL="feature=support-agent"                           # yours
python ask.py --rest      # the costliest sessions, by REST
python ask.py --tools     # the same, through one MCP tool
python ask.py             # a model looks for sessions whose cost is out of line
```

The examples below are from the requests of the
[agent tracing demo](../agent-tracing/): a run of `run.py --fail` and of
`incidents.py`, which leaves 26 sessions, several of them broken on purpose.

## One lookup, three ways

### REST, no model

"Which sessions cost the most" is a fixed query. It is one GET and a sort:

```text
GET https://synthorai.io/api/v1/sessions {"start": "2026-10-11T11:24:00Z", "end": "2026-10-11T11:32:00Z", "limit": 50, "metadata.feature": "support-agent"}

26 sessions, the three that cost the most:

  $0.003831   6 requests   24557 in (     0 cached)  replay-mismatch-a14896679e70
  $0.001747   6 requests    8053 in (  1189 cached)  context-growth-2d11dfe29a08
  $0.001560   6 requests   24546 in ( 17536 cached)  context-growth-c5c393570ca2

0 model calls, 0 tokens, $0
```

If the question never changes, stop here.

### MCP, no model

`python ask.py --tools` asks the MCP server what it offers and calls one tool
by hand. The answer is the same three sessions.

```text
7 tools with this key and these headers

  list_models      capability, category, input_modality, limit, search
  get_model        model
  get_pricing      category, limit, model, search
  search_requests  api_key_id, cursor, end, labels, limit, model, prompt_id, session_id, start, status, trace_id
  get_request      id
  list_traces      api_key_id, cursor, end, id, labels, limit, start
  list_sessions    api_key_id, cursor, end, id, labels, limit, start

tools/call list_sessions {"start": "2026-10-11T11:24:00Z", "end": "2026-10-11T11:32:00Z", "labels": {"feature": "support-agent"}, "limit": 50}
```

Both steps are one function, `mcp()`: a JSON-RPC body (`tools/list`, or
`tools/call` with a name and arguments) posted to `https://synthorai.io/v1/mcp`
with three headers.

| Header | What it does |
|---|---|
| `Authorization: Bearer <admin key>` | which workspace, and what the key may read |
| `X-MCP-Readonly: true` | tools that change anything are not listed at all |
| `X-MCP-Toolsets: logs` | of the read-only tools, only the ones about the request log |

The two `X-MCP` headers narrow one connection further than the key does, so an
agent that only needs to read logs cannot be talked into anything else.

Called by hand, MCP gives you nothing REST did not. What it adds is the list:
each tool comes with a description and a JSON schema, which is exactly what a
model needs in order to call it.

### MCP with a model

`python ask.py` hands that list to a model and gives it a task with no fixed
query behind it:

> Find the sessions in this window whose cost is out of line with the others,
> and say what in the token counts, the models or the number of calls explains
> each one.

The loop is the usual one. The tools go to the model as function tools, each
tool call it makes becomes a `tools/call`, the result goes back as text, and
this repeats until the model answers or `MAX_ROUNDS` is reached. The model is
told the time range and the label, and nothing about the tools: it finds the
`labels` filter and the meaning of the token fields in their descriptions. It
is also told that what the tools return is data to report on, not instructions
to follow, because a log row can hold text somebody else chose.

On `gpt-6-luna` it listed the sessions once and reported, among others:

- `replay-mismatch-a14896679e70`, $0.0038: 24,557 input tokens and none read
  from cache, against 18,844 cached in its twin at $0.0015;
- `wrong-model-53a0d02ac5cf`, $0.0012 for 217 input tokens, where its
  counterpart cost $0.0003: one of its models is `gpt-6.1-sol`;
- `retry-loop-71b013a7d1bb`, 10 calls where its counterpart made 2.

Those are three of the problems the agent tracing demo plants. The full
answers and every tool call are in [`transcripts/`](./transcripts/), one
folder for each model.

## What it costs

Every run prints what it used, and the cost is read from the billing record
of each of the model's calls.

| Run | Model calls | Tool calls | Rows read | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|---|
| `--rest` | 0 | 0 | 26 | 0 | 0 | $0 |
| `--tools` | 0 | 1 | 26 | 0 | 0 | $0 |
| `gpt-6-luna`, cost outliers | 2 | 1 | 26 | 6,469 | 1,789 | $0.0015 |
| `gpt-6-luna`, failures | 2 | 1 | 2 | 4,123 | 424 | $0.0003 |
| `claude-sonnet-5-5`, cost outliers | 3 | 3 | 28 | 19,916 | 2,325 | $0.0631 |
| `claude-sonnet-5-5`, failures | 2 | 1 | 2 | 7,784 | 702 | $0.0226 |

Both models named the uncached conversation, the session on the expensive
model and the retry loop. `gpt-6-luna` did it from one call to `list_sessions`.
Claude Sonnet 5.5 also called `get_pricing` twice, worked out from the totals
a price the list does not show, and cost about forty times as much. The
default is `gpt-6-luna`; set `AGENT_MODEL` to change it.

What a run costs is mostly what the model decides to read, because every tool
result stays in the prompt of the calls after it:

- **The same task does not take the same path twice.** Run with `--no-label`,
  `gpt-6-luna` looked up three models as well and cost $0.0020, and Claude
  Sonnet 5.5 called `get_pricing` once and cost $0.0552. A model that opens
  sessions request by request reads more rows and costs more.
- **A wider time range did not cost more here.** Over the sixteen hours that
  end where our window ends, `gpt-6-luna` read 41 rows and cost $0.0018. One
  call to `list_sessions` returns at most 50 rows, so the range starts to
  matter when the model pages through it or opens what it finds.
- **A label keeps the answer on the subject.** With `--no-label` the model is
  told the time range only. In our window every request carries the label, so
  it listed the same sessions. Over the sixteen hours it read 48 rows instead
  of 41, and it was answering about everything in the workspace.
- **`MAX_ROUNDS` is the hard stop.** A run cannot make more model calls than
  that, whatever the model would like to look at next.

## What to know before you rely on it

**Check the numbers you will act on, and explanations separately.** The costs,
token counts and ids in these answers match the console. A sentence that says
why is the model's reading of the numbers, and it can be wrong while they are
right. Read it as a hypothesis.

**An answer is only as wide as the tools the model chose.** The Claude Sonnet
5.5 run called `list_sessions` and `get_pricing`, and its answer ends: "I only
had session-level totals. The tools can't give a per-call cost split, so I
can't say exactly how much of the wrong-model cost came from the sol calls."
The first sentence is true of the calls it made. The second is not true of
the tools: `search_requests` returns each request with its model and cost.
When an answer says something cannot be known, read the list of tool calls
before you believe it.

**It cannot see content.** The log tools return model, status, tokens, cost,
timing, session, trace and labels. They never return a prompt or a reply. The
model can tell you a request failed with a 404 and what the error was. It
cannot tell you what the user asked.

**Transcripts.** Runs are saved to `.data/transcripts/`, which git ignores.
Anything shaped like a UUID is replaced before saving, which covers request
ids. Read a transcript before you share it anyway.

## Settings

| Variable | Default | |
|---|---|---|
| `SYNTHORAI_ADMIN_KEY` | none | Read-only admin key for the REST API and the MCP server |
| `SYNTHORAI_API_KEY` | none | Pays for the model's calls. Not needed for `--rest` or `--tools` |
| `LOG_WINDOW` | our run on October 11, 2026 | The time range to look at |
| `LOG_LABEL` | `feature=support-agent` | The label to stay within |
| `AGENT_MODEL` | `gpt-6-luna` | The model that calls the tools. It needs function calling |
| `MAX_ROUNDS` | `8` | The most model calls one run may make |
| `MAX_OUTPUT_TOKENS` | `4000` | The output cap on each model call |
| `BASE_URL` | `https://synthorai.io/v1` | Where the model is called |
| `MCP_URL` | `https://synthorai.io/v1/mcp` | The MCP server |
| `API_URL` | `https://synthorai.io/api/v1` | The REST API |
