# Synthorai Chatbot Demo

A complete, minimal chatbot you can run locally in two minutes: FastAPI server,
one static page, no build step, no database. Companion code for the guide at
[synthorai.io/use-cases/chatbots](https://synthorai.io/use-cases/chatbots/).

![A search-assisted turn: the question, the expanded activity trail with the
query and its results, and the answer with a code block — session token and
cost stats along the top](docs/screenshot.png)

## What it does

A chatbot looks like one text box, but it is four systems behind it: **which
model answers**, **what context that model sees**, **what survives when the
context runs out**, and **what the model can reach for when it does not know**.
Everything else — streaming, stopping, markdown, conversation lists — is table
stakes on top. This demo implements all four plainly enough to read in an
afternoon, and instruments them so you can watch what each one costs.

### 1. Model selection

- **Switch models mid-conversation.** History is plain OpenAI-format messages,
  so it carries over unchanged; the next turn just goes to a different model.
- **A lineup you control.** Seven models ship in `.env`, grouped by tier in the
  picker. Add any id the gateway serves from Settings → Models; it persists in
  `.data/models.json`.
- **Per-model capability is measured, not assumed.** Models differ in what they
  can actually do — which ones stream their reasoning, which ones survive a
  tool call — and the demo routes around the differences instead of pretending
  they are not there.

### 2. Context management

Everything the model sees on a turn is assembled in one place
([context.py](./context.py)), in a fixed order, most-stable first:

| Layer | Comes from | Editable in |
|---|---|---|
| System prompt (persona) | `presets/*.txt` or free text | Settings → Context |
| Long-term memory | `.data/memory.md` | Settings → Memory |
| Rolling summary | compression, automatic | — |
| Recent turns | the conversation | — |
| Date anchor | the server clock | — |
| Tool instructions | only on tool turns | Settings → Tooling |
| User prompt | the composer | the composer |

- **The order is the point.** Stable things first means the cache breakpoint can
  sit after them and every later turn re-reads that prefix at a fraction of the
  price.
- **Prompt caching.** The stable head is marked with `cache_control`, which is
  the single biggest cost lever here — on a conversation carrying real memory
  and a summary, later turns cost roughly a tenth of the first.
- **A budget, measured rather than estimated.** The context bar is drawn from
  the last response's real `prompt_tokens`, not a local guess, and is segmented
  by what is filling it: system prompt, memory, summary, history.
- **A date anchor.** Every request states today's date, so the model does not
  answer — or search — as if its training cutoff were the present.

### 3. Memory

Two kinds, because they have different lifetimes:

- **Rolling summary (this conversation).** When the next turn would overflow the
  budget, older messages are folded into a summary by a cheap model and the
  recent window survives verbatim. Nothing is silently dropped: a notice appears
  in the chat when it happens.
- **Long-term memory (every conversation).** The same compression call also
  extracts durable facts about the user into `.data/memory.md`, which every
  future conversation loads. One call, two outputs — it is already paying to
  re-read the dropped messages.
- **The compression prompt is a setting.** It decides what survives a
  conversation, so it is editable per conversation in Settings → Memory rather
  than buried in the source.
- **Failure does not cost you the turn.** If the summary call fails, the server
  drops the oldest turns unsummarized and says so.

### 4. Tool calling

- **Thinking.** Models that stream `reasoning_content` show their working out
  live, in the turn, and it is stored with the answer.
- **Web search** (`synthorai:web_search`) — the gateway runs the search
  server-side and folds the results into the prompt; there is no tool round trip
  in this codebase. Citations render under the reply as domain chips.
- **Web fetch** (`synthorai:web_fetch`) — reads a page in full when a snippet
  does not settle the question.
- **On by default, used on judgement.** Both tools are offered every turn and
  the model decides from context whether the question needs them, so arithmetic
  costs nothing extra while "what is the current…" searches.
- **A budget the model is actually told about.** Per-use tools need a cap *and*
  a sentence explaining the cap, or the model plans as if unlimited and runs out
  mid-thought. That sentence lives in Settings → Tooling, because its wording
  moves the bill more than it looks like it should.
- **An activity trail per turn.** What the model thought, searched, and read,
  collapsed into one muted line above the answer (`Thought for 5s · Searched the
  web · Read 2 pages`) and expandable into a timeline: queries, results with
  their domains, pages, reasoning. Live while the turn runs, folded away once it
  lands, and **stored with the answer** — a trail that vanishes on reload cannot
  be used to audit anything, which is most of the point.

### What a chat UI owes you regardless

- **Streaming** — server-sent events, token by token.
- **Stop that reaches the provider.** The Send button becomes Stop while a reply
  streams; no confirmation. It aborts the fetch, the server notices the
  disconnect and closes the upstream call, so generation actually stops instead
  of billing for tokens nobody will read. The partial reply is kept and marked
  *interrupted*.
- **Regenerate** — redo the last reply without retyping.
- **Named recovery paths, not one red line.** Six failure modes each get their
  own: network error and rate limit offer Retry (honouring `retry-after`), a
  refused reply offers *Edit & resend*, a bad key names the env var to fix, a
  broken stream keeps the partial, and a failed compression degrades to a
  warning while the turn continues. The draft returns to the composer whenever
  the turn did not take.
- **Markdown rendering** — headings, lists, quotes, tables, code. Half-open bold
  and unterminated fences stay plain text until they close, so the layout does
  not jump mid-stream. Code blocks get a copy button.
- **Conversation management** — rename, delete, and full-text search across
  titles and bodies.
- **Settings that are per conversation.** Everything except memory and the model
  list is scoped to one conversation, so two can run side by side with different
  prompts, budgets, and tools — which is how you reproduce a claim rather than
  take our word for it.

### What it shows you about cost

Nothing here is a mock: every number comes from the gateway's `usage`.

- **Per turn** — input, output, and cached token counts with the reported cost.
- **Per session** — running totals for input, output, cached tokens (and what
  share of input that is), searches, fetches, cost, and turns. Turns that
  arrived *without* a cost field are counted separately rather than summed as
  zero, because a total that silently omits turns reads as the bill when it is
  only a floor.

## Run it

Requires Python 3.10+.

```bash
cp ../.env.example ../.env    # repo root; put your API key in it
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --reload
```

Open <http://127.0.0.1:8000/>. The key lives in a single `.env` at the repo
root and is shared by every use case; a `.env` in this directory is optional
and overrides it per setting. Keys are issued at
[synthorai.io](https://synthorai.io/); any OpenAI-compatible endpoint works via
`BASE_URL`.

This is a local demo: the server has no authentication, and every request it
serves spends your API key. Run it on localhost; do not expose it to the
public internet as-is.

The default budget is 102,400 tokens, which a normal conversation will not
reach. To watch compression happen, open Settings and drop the context window to
a few thousand for that one conversation, then paste a few long messages: a
notice appears in the chat when old turns are folded away, and the memory box
fills in once facts are extracted.

## Layout

```
server.py    routes; the chat turn pipeline (project → compress → stream → record)
context.py   token budgeting, compression call, memory file, message assembly
storage.py   one JSON file per conversation under .data/ (settings included)
config.py    env-driven settings; model lineup, tiers, and per-model params
tools.py     the /v1/messages transport used by tool-enabled turns
static/      the chat page (vanilla JS, no build)
presets/     system-prompt presets (one .txt each)
```

## Not in the MVP, on purpose

Auth, multi-user, rate limiting, RAG, semantic memory dedup, tool calling,
syntax highlighting inside code blocks. Each is a real feature with real design
questions; the guide's closing section sketches where each would attach.
