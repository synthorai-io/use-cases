# Synthorai use cases

Runnable companion code for the guides at [synthorai.io/use-cases](https://synthorai.io/use-cases/).
One directory per use case. Every project is small on purpose: a reference you can
read top to bottom, run locally in a couple of minutes, and copy pieces from.

| Directory | What it is | Needs | Guide |
|---|---|---|---|
| [`chatbot/`](#chatbot) | A complete chatbot: model selection, prompt caching, memory, tools | Python, an API key | [Build an LLM chatbot](https://synthorai.io/use-cases/chatbots/) |
| [`agent-tracing/`](#agent-tracing) | A support agent traced with request headers, and ten production problems to find with the traces | Python, an API key | [Trace an LLM agent](https://synthorai.io/use-cases/agent-tracing/) |
| [`coding-agent-spend/`](#coding-agent-spend) | Claude Code run through the gateway, with its cost split by agent | Python, Claude Code, an API key | in progress |
| [`mcp-debugging/`](#mcp-debugging) | An agent that answers questions about your request log over MCP | Python, Claude Code, an API key and a read-only admin key | in progress |
| [`translation-bench/`](#translation-bench) | A translation benchmark with its corpus, runners and raw results | Python, an API key | [Best LLM for Translation](https://synthorai.io/blog/llm-translation-quality/) |

## Setup

Every project talks to the [Synthorai gateway](https://synthorai.io/), and all
but `translation-bench/`, which takes its key from the environment, share one
credentials file:

```bash
git clone https://github.com/synthorai-io/use-cases
cd use-cases
cp .env.example .env    # put your API key in it
```

Each project then has its own `requirements.txt` and its own README with the
exact commands. A project may also keep its own `.env` for settings specific to
it, which take precedence over the shared one. [`.env.example`](./.env.example)
lists every setting, grouped by project.

The model calls cost money. Each README says what a run cost when we ran it,
and the two projects that start Claude Code cap what a run can spend.

## The projects

### chatbot

A chatbot you can run locally in two minutes: a FastAPI server and one static
page, with no build step and no database. It implements the four systems
behind a chat box plainly enough to read in an afternoon, and shows what each
one costs as you use it:

- **Model selection.** Switch models in the middle of a conversation; the
  history carries over unchanged.
- **Context and prompt caching.** The stable part of the prompt is cached, and
  the page shows how much of each turn was read from cache.
- **Memory.** When the conversation outgrows its token budget, older turns are
  compressed into a summary and a list of facts.
- **Tools.** Model reasoning, web search and page fetching, each with a cap on
  how often a turn may use it.

It uses the plain OpenAI-compatible API, so any OpenAI-compatible endpoint
works if you point `BASE_URL` at it.
[Read more](./chatbot/)

### agent-tracing

A small support agent, with a sub-agent, whose model calls show up in the
Synthorai console as traces and sessions. There is no tracing SDK and no
collector: the whole tracing layer is one 65-line file that sets five request
headers.

- `run.py` runs two conversations (21 model calls, under $0.002) and prints
  the session and trace ids, then what each call cost.
- `incidents.py` reproduces ten problems an agent has in production, each one
  broken and then fixed, so the two can be compared in the console: checks run
  one after another, a slow tool, a prompt that defeats caching, a retry loop,
  replies cut off by an output cap, reasoning nobody needed, a growing
  context, the wrong model for the job, a saved history the API rejects, and a
  history that is not replayed as it was sent. All ten cost about $0.03.

It is the code behind four guides: tracing, latency, cost and debugging.
[Read more](./agent-tracing/)

### coding-agent-spend

Runs Claude Code, headless, on a small repository with one failing test, and
sends it through the gateway instead of straight to the provider. The console
then shows what the run cost and which agent spent it: the main conversation,
the built-in Explore sub-agent, and a custom sub-agent that runs the tests.

- Nothing in your own Claude Code setup is touched. The gateway address and
  key exist only in the environment of the process the script starts, and the
  agent works on a temporary copy of the sample repository.
- Labels such as `repo=shipping` and `_user=dev_1` are attached to every call,
  so spend can be split by repository or developer without a key for each.
- One run takes about half a minute and cost $0.17 with a cold cache. The
  script passes Claude Code a budget and keeps its own ceiling across runs.

[Read more](./coding-agent-spend/)

### mcp-debugging

Lets an agent read your request log so you can ask about it in plain words:
which session cost the most and why, how two variants compare, whether any
request for a user failed. Claude Code connects to the gateway's MCP server
with a read-only key and is allowed those tools and nothing else: no shell, no
files, no web.

- The connection is one committed file, `.mcp.json`, that holds no secret.
- Two keys do two jobs. The admin key reads the log and cannot call a model;
  the API key pays for the agent's own calls and cannot read the log.
- `transcripts/` holds three real question-and-answer runs with every tool
  call the agent made. The README checks each answer against the console and
  says where the agent's numbers were right and its explanation was not.
- The three questions cost $0.17 together.

[Read more](./mcp-debugging/)

### translation-bench

A reproducible benchmark of translation quality and cost: 9 models, 9
languages and 52 source segments in 6 domains, with 4,210 generated
translations and 8,455 blind pairwise verdicts from a panel of three judge
models. Everything needed to verify or re-run the study is in the directory:
the corpus and the script that sampled it, the runners, the raw results, and
the code that aggregates them.
[Read more](./translation-bench/)

## License

[MIT](./LICENSE)
