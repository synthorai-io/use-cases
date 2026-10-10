# MCP debugging demo

Ask an agent about your request log without opening the console. Claude Code
connects to the Synthorai gateway's MCP server with a read-only key, calls the
log tools, and answers questions such as "which session cost the most, and
why?" or "did any request for this user fail?".

The questions here are about the requests the
[agent tracing demo](../agent-tracing/) made in one six-minute run
(`python run.py --fail`, then
`python incidents.py cache-miss context-growth replay-mismatch`), so every
answer can be checked against the same sessions in the console.

## Run it

You need Python 3.10 or later, the `claude` command (Claude Code 2.1.280 or
later for Claude Sonnet 5.5; we ran 2.1.296), and two keys in `.env` at the
repo root:

| Key | What it does here | Give it |
|---|---|---|
| `SYNTHORAI_ADMIN_KEY` | reads the request log through MCP | an admin key with the `logs.read` scope and nothing else |
| `SYNTHORAI_API_KEY` | pays for the agent's own model calls | your usual inference key |

An admin key cannot call a model, and an inference key cannot read the log, so
neither key can do the other's job.

```bash
git clone https://github.com/synthorai-io/use-cases
cd use-cases
cp .env.example .env    # put both keys in it
cd mcp-debugging
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python ask.py "Which model had the most errors in the last hour?"
```

Set `LOG_WINDOW` to a time range that holds your own requests first, and
change the label in `BRIEF` at the top of [`ask.py`](./ask.py): the default
window points at our run, so with your key it finds nothing. Without a
question, `python ask.py` asks the three below.

Each answer is printed with the tools the agent called and saved to
`.data/transcripts/`. The runs we made are in [`transcripts/`](./transcripts/).
The three questions cost $0.17 in model calls when we ran them on
October 10, 2026, on Claude Sonnet 5.5: $0.09, $0.03 and $0.04.

## How the agent is connected

[`.mcp.json`](./.mcp.json) is the whole connection. The key is read from the
environment, so the file is safe to commit:

```json
{
  "mcpServers": {
    "synthorai": {
      "type": "http",
      "url": "https://synthorai.io/v1/mcp",
      "headers": {
        "Authorization": "Bearer ${SYNTHORAI_ADMIN_KEY}",
        "X-MCP-Readonly": "true",
        "X-MCP-Toolsets": "logs"
      }
    }
  }
}
```

The two `X-MCP` headers narrow one connection further than the key does.
With them, `tools/list` returned seven tools for our key:

| Tool | What it returns |
|---|---|
| `search_requests` | requests in a time range: model, status, tokens, cost, timing, session, trace and labels. Never the prompt or the answer |
| `get_request` | one request by id |
| `list_sessions` | requests grouped by session id, with cost and tokens |
| `list_traces` | requests grouped by trace id |
| `list_models`, `get_model`, `get_pricing` | the model catalogue and prices |

The three log tools take a `labels` filter: up to five label names and values,
matched exactly, such as `{"case": "cache-miss", "variant": "before"}`. With
it, a session or trace is summed over its labelled requests only. Their
descriptions also say how to read the token counts: `input_tokens` is the
whole prompt, and `cached_tokens` and `cache_write_tokens` are parts of it.

`ask.py` then starts Claude Code with only these tools allowed: no shell, no
file access, no web. It runs with an empty `CLAUDE_CONFIG_DIR`, so it does not
load or change your own Claude Code setup, and that directory is deleted when
the question is answered.

## The three questions, and whether the answers were right

Each answer was checked against the session pages in the console.

| Question | The agent's answer | Tool calls | The console |
|---|---|---|---|
| Which `cache-miss`, `context-growth` or `replay-mismatch` session cost the most, and why? | `replay-mismatch-ad1904ac1cd1`, $0.0037 over 6 requests: 0 of its 24,459 input tokens read from cache. Its twin read 18,735 of 24,578 from cache and cost $0.0014 | 4 × `list_sessions` | the same session, $0.0037, 6 calls, input 24.5k, cache hit 0% |
| Compare `cache-miss` before and after | before: 5,612 input tokens, 0 cached, $0.0014. After: 5,590, 3,735 cached, $0.0011 | 2 × `search_requests` | 0 of 5,612 and $0.0014; 3,735 of 5,590 and $0.0011 |
| Did any request for `u_1001` fail? And `u_1002`? | one of 11 requests, 404, `The model "model-that-does-not-exist" does not exist or is not available to this workspace`, retried successfully. None of `u_1002`'s 10 | 2 × `search_requests` | 11 calls and 1 × 404 for `u_1001`; 10 calls and 0 errors for `u_1002` |

Every cost, token count, request count and status matched. `BRIEF` gives the
agent the time range and the label to stay within, and nothing about the
tools: it found the `labels` filter and the meaning of the token fields in the
tool descriptions. One explanation was still wrong.

## What to know before you rely on it

**Check the explanation, not only the numbers.** The first answer says the
session "wrote 24,441 tokens to cache" and that "cache writes cost more than
ordinary input". The numbers are right. The reason is not: the model in these
sessions, `gpt-6-luna`, caches automatically and has no separate price for
writing to the cache. The part of a prompt that is not read from cache is
billed as ordinary input. The agent took the idea from the field name
`cache_write_tokens`, where the log tools report that part. The accurate
reading is plainer: nothing was read from cache, so every token was billed at
the full input price.

**An answer is only as wide as the tools the agent chose.** The same answer
says the tools "give per-session totals only, not per-request token counts".
`list_sessions` does; `search_requests` gives each request. The agent had
called only the first. When an answer says something cannot be known, check
the list of tool calls before you believe it.

**Ask with labels, and the agent stays inside them.** Every call in the three
transcripts carries a `labels` filter, so the agent read the demo's requests
and nothing else from the workspace. A question with no label to filter on
makes it read every request in the time range, and those rows are most of what
an answer costs. `ask.py` still removes anything shaped like a UUID, which
covers request ids and Claude Code session ids, from the saved transcript.
Read a transcript before you share it anyway.

**It cannot see content.** The log tools return what was called and how it
went, never the prompt or the reply. The agent can tell you a request failed
with a 404 and what the gateway's error was. It cannot tell you what the user
asked.

## Settings

| Variable | Default | |
|---|---|---|
| `SYNTHORAI_ADMIN_KEY` | none | Read-only admin key for the MCP server |
| `SYNTHORAI_API_KEY` | none | Pays for the agent's model calls |
| `AGENT_MODEL` | `claude-sonnet-5-5` | The model Claude Code runs on |
| `LOG_WINDOW` | our run on October 8, 2026, 05:54 to 06:00 UTC | The time range the agent is told to look at |
| `RUN_BUDGET_USD` | `0.40` | Passed to `--max-budget-usd` for each question |
| `SPEND_CEILING_USD` | `2.00` | The script will not start a question that could take the total past this |
