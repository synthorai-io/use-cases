"""Synthorai Chatbot Demo server.

Run:
    uvicorn server:app --reload

Routes:
    GET  /                          the chat page
    GET  /api/config                app name, model lineup, presets, budget
    PUT  /api/models                replace the user-added model list
    GET  /api/memory                read the cross-session memory file
    PUT  /api/memory                overwrite it
    GET  /api/conversations         list conversations (optional ?q= search)
    POST /api/conversations         create one {model?, system_prompt?}
    GET  /api/conversations/{id}    full conversation
    PATCH /api/conversations/{id}   title / model / persona / budget / web search
    DELETE /api/conversations/{id}  remove it
    POST /api/conversations/{id}/chat        {message} -> SSE stream
    POST /api/conversations/{id}/regenerate  redo the last reply -> SSE stream
    POST /api/conversations/{id}/rewind      pull the last user message back

The chat route is the whole product. Per turn it:
    1. projects the next request size from the last measured prompt_tokens;
    2. compresses old turns into the rolling summary if it would overflow,
       extracting durable facts into .data/memory.md as a side effect;
    3. streams the completion as server-sent events;
    4. records measured usage (and gateway-reported cost) on the conversation.

Stopping a turn is not advisory. When the client aborts the fetch (Stop
button, closed tab, dropped connection) the server notices, closes the
upstream stream so the provider stops generating, and persists whatever text
arrived marked `interrupted`. Nobody pays for tokens no one will read.
"""

import contextlib
import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel

import config
import context
import storage
import tools

app = FastAPI(title=config.APP_NAME)

client = AsyncOpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)

PRESETS = config.load_presets()
STATIC = Path(__file__).parent / "static"

FILTER_MARKERS = ("content_filter", "content_policy", "safety", "moderation")


def cached_tokens(usage: dict) -> int:
    """Cached input tokens, whichever field the provider used to report them."""
    details = usage.get("prompt_tokens_details") or {}
    return (usage.get("cache_read_input_tokens")
            or (details.get("cached_tokens") if isinstance(details, dict) else 0)
            or 0)


def total_input(usage: dict) -> int:
    """The whole prompt, cached part included.

    Providers disagree about whether `prompt_tokens` already contains the cached
    tokens: some report the full prompt, others only the uncached delta. Getting
    this wrong is not cosmetic — this number is the budget's ground truth, and
    under-counting it by the size of the cache means compression never fires and
    the window silently overflows. When `prompt_tokens` is smaller than the
    cached count it cannot be the whole prompt, so the parts are summed.
    """
    prompt = usage.get("prompt_tokens", 0) or 0
    cached = cached_tokens(usage)
    written = usage.get("cache_creation_input_tokens", 0) or 0
    if prompt >= cached:
        return prompt + (written if written and prompt < written else 0)
    return prompt + cached + written


def tally(totals: dict, usage: dict) -> dict:
    """Fold one turn's usage into the conversation's running totals.

    The two endpoints and the different vendors do not agree on field names:
    Anthropic reports `cache_read_input_tokens`, DeepSeek and GLM only
    `prompt_tokens_details.cached_tokens`, Gemini neither. Read whichever is
    present so the session line means one thing.
    """
    cached = cached_tokens(usage)
    totals["input_tokens"] += total_input(usage)
    totals["output_tokens"] += usage.get("completion_tokens", 0) or 0
    totals["cached_tokens"] += cached
    totals["cache_write_tokens"] += usage.get("cache_creation_input_tokens", 0) or 0
    totals["web_searches"] += usage.get("web_search_requests", 0) or 0
    totals["web_fetches"] += usage.get("web_fetch_requests", 0) or 0
    # The gateway reports `cost` on some turns and omits it on others —
    # reproducibly, on turns where a tool is declared but not used. Count the
    # gaps instead of folding them into the sum: a total that silently omits
    # turns reads as "this is what you spent" when it is only a floor.
    if "cost" in usage:
        totals["cost_reported"] = True
        totals["cost"] = round(totals["cost"] + (usage.get("cost") or 0), 8)
    else:
        totals["cost_missing_turns"] += 1
    totals["turns"] += 1
    return totals


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def retry_after(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    header = getattr(response, "headers", {}).get("retry-after") if response else None
    try:
        return int(header) if header else None
    except ValueError:
        return None


def citations(delta) -> list[dict]:
    """Pull web-search citations off a streamed delta.

    `annotations` is not part of the OpenAI chunk schema the SDK models, so it
    arrives as plain dicts in the model's extra fields rather than as typed
    objects. Handle both, and ignore anything without a URL.
    """
    out = []
    for note in getattr(delta, "annotations", None) or []:
        if not isinstance(note, dict):
            note = note.model_dump() if hasattr(note, "model_dump") else {}
        cite = note.get("url_citation") or {}
        url = cite.get("url")
        if url:
            out.append({"url": url, "title": cite.get("title") or url})
    return out


def classify(exc: Exception) -> dict:
    """Map an upstream exception onto the failure modes the UI can recover from.

    `recoverable` means "the same request may succeed if repeated" — it drives
    whether the UI offers Retry or asks the user to edit and resend.
    """
    message = str(exc)
    if isinstance(exc, httpx.HTTPStatusError):
        # The web-search transport is raw HTTP, so map status codes the same way
        # the SDK maps its own exception types.
        status = exc.response.status_code
        detail = exc.response.text[:300] or message
        if status == 429:
            return {"kind": "rate_limit", "recoverable": True,
                    "retry_after": None, "message": detail}
        if status in (401, 403):
            return {"kind": "auth", "recoverable": False, "message": detail}
        if status == 400 and any(m in detail.lower() for m in FILTER_MARKERS):
            return {"kind": "content_filter", "recoverable": False,
                    "message": detail}
        return {"kind": "upstream", "recoverable": status >= 500,
                "message": detail}
    if isinstance(exc, httpx.RequestError):
        return {"kind": "network", "recoverable": True, "message": message}
    if isinstance(exc, httpx.HTTPError):
        return {"kind": "upstream", "recoverable": True, "message": message}
    if isinstance(exc, RateLimitError):
        return {"kind": "rate_limit", "recoverable": True,
                "retry_after": retry_after(exc), "message": message}
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return {"kind": "network", "recoverable": True, "message": message}
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return {"kind": "auth", "recoverable": False, "message": message}
    if isinstance(exc, BadRequestError) and any(
        marker in message.lower() for marker in FILTER_MARKERS
    ):
        return {"kind": "content_filter", "recoverable": False, "message": message}
    return {"kind": "upstream", "recoverable": True, "message": message}


@app.get("/")
async def index():
    # Never cache the page. Without this the server sends no cache headers at
    # all, so browsers apply their own heuristic and happily keep serving a
    # stale copy after the file changes — including, once, a half-written one
    # that a plain reload would not shake loose.
    return FileResponse(STATIC / "index.html",
                        headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/config")
async def get_config():
    models = config.all_models()
    return {
        "app_name": config.APP_NAME,
        "models": models,
        "model_groups": config.model_groups(),
        "custom_models": config.custom_models(),
        "default_model": models[0],
        "presets": PRESETS,
        "context_budget_tokens": config.CONTEXT_BUDGET_TOKENS,
        "keep_recent_messages": config.KEEP_RECENT_MESSAGES,
        "cache_enabled": config.CACHE_ENABLED,
        "cache_ttl": config.CACHE_TTL,
        "web_search_tool_name": config.WEB_SEARCH_TOOL,
        "web_fetch_tool_name": config.WEB_FETCH_TOOL,
        "reasoning_models": config.REASONING_MODELS,
        "default_tool_prompt": config.TOOL_PROMPT,
        "default_compress_prompt": config.COMPRESS_PROMPT,
        "summary_model": config.SUMMARY_MODEL,
    }


@app.get("/api/memory")
async def get_memory():
    return {"text": context.load_memory()}


class MemoryIn(BaseModel):
    text: str


@app.put("/api/memory")
async def put_memory(body: MemoryIn):
    """Long-term memory is a text file; the Settings panel edits it directly."""
    context.save_memory(body.text)
    return {"text": context.load_memory()}


class ModelsIn(BaseModel):
    models: list[str]


@app.put("/api/models")
async def put_models(body: ModelsIn):
    """Replace the user-added model list (the .env lineup is always kept)."""
    cleaned, seen = [], set()
    for m in body.models:
        m = m.strip()
        if m and m not in seen:
            seen.add(m)
            cleaned.append(m)
    config.save_custom_models(cleaned)
    return await get_config()


@app.get("/api/conversations")
async def list_conversations(q: str = ""):
    return storage.list_all(q)


class NewConversation(BaseModel):
    model: str | None = None
    system_prompt: str | None = None


@app.post("/api/conversations")
async def create_conversation(body: NewConversation):
    model = body.model or config.all_models()[0]
    if model not in config.all_models():
        return JSONResponse({"error": f"unknown model {model!r}"}, status_code=400)
    prompt = body.system_prompt or next(iter(PRESETS.values()))
    return storage.create(model, prompt)


def breakdown(conv: dict) -> dict:
    """Estimate what the next request's context is actually made of.

    These are estimates, not measurements — only the whole prompt gets a real
    number back from the API, and that arrives after the fact. The point is to
    show the shape: which part is worth trimming when the budget is tight.
    """
    memory = context.load_memory()
    history = sum(context.estimate_tokens(m["content"]) for m in conv["messages"])
    parts = {
        "system_prompt": context.estimate_tokens(conv["system_prompt"]),
        "memory": context.estimate_tokens(memory) if memory else 0,
        "summary": context.estimate_tokens(conv["summary"]) if conv["summary"] else 0,
        "history": history,
    }
    return {
        "parts": parts,
        "estimated_total": sum(parts.values()),
        "measured_total": conv["last_prompt_tokens"],
        "budget": conv.get("context_budget_tokens") or config.CONTEXT_BUDGET_TOKENS,
        "reply_reserve": config.MAX_COMPLETION_TOKENS,
        "message_count": len(conv["messages"]),
        "keep_recent": config.KEEP_RECENT_MESSAGES,
    }


@app.get("/api/conversations/{conv_id}")
async def get_conversation(conv_id: str):
    conv = storage.load(conv_id)
    if conv is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {**conv, "breakdown": breakdown(conv)}


class ConversationPatch(BaseModel):
    title: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    context_budget_tokens: int | None = None
    web_search: bool | None = None
    web_fetch: bool | None = None
    tool_prompt: str | None = None
    compress_prompt: str | None = None


@app.patch("/api/conversations/{conv_id}")
async def patch_conversation(conv_id: str, body: ConversationPatch):
    """Rename, or change any per-conversation setting from the Settings panel."""
    conv = storage.load(conv_id)
    if conv is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if body.title is not None:
        conv["title"] = body.title.strip()[:120] or "Untitled"
    if body.model is not None:
        if body.model not in config.all_models():
            return JSONResponse({"error": f"unknown model {body.model!r}"},
                                status_code=400)
        conv["model"] = body.model
    if body.system_prompt is not None:
        conv["system_prompt"] = body.system_prompt
    if body.context_budget_tokens is not None:
        # Clamped so a typo cannot make every turn compress, or none ever.
        conv["context_budget_tokens"] = max(1000, min(2_000_000,
                                                      body.context_budget_tokens))
    if body.web_search is not None:
        conv["web_search"] = body.web_search
    if body.web_fetch is not None:
        conv["web_fetch"] = body.web_fetch
    if body.tool_prompt is not None:
        # Blank means "use the configured default" rather than "send nothing".
        conv["tool_prompt"] = body.tool_prompt.strip() or None
    if body.compress_prompt is not None:
        text = body.compress_prompt.strip()
        # A prompt missing {transcript} would summarize nothing at all, and the
        # failure would only surface much later, on the first overflow.
        if text and "{transcript}" not in text:
            return JSONResponse(
                {"error": "compress_prompt must contain {transcript}"},
                status_code=400)
        conv["compress_prompt"] = text or None
    storage.save(conv)
    return conv


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: str):
    return {"deleted": storage.delete(conv_id)}


class ChatIn(BaseModel):
    message: str
    model: str | None = None          # switch model mid-conversation
    system_prompt: str | None = None  # edited persona applies from this turn


class RegenerateIn(BaseModel):
    model: str | None = None
    system_prompt: str | None = None


def apply_overrides(conv: dict, model: str | None, system_prompt: str | None):
    """Apply per-turn model / persona overrides. Returns an error dict or None."""
    if model:
        if model not in config.all_models():
            return {"error": f"unknown model {model!r}"}
        conv["model"] = model
    if system_prompt is not None:
        conv["system_prompt"] = system_prompt
    return None


async def completion_events(model: str, messages: list[dict], extra: dict):
    """Stream a normal turn, normalized to the same events tools.py yields.

    Raising before the first yield is how the caller distinguishes a request
    that never started from one that broke mid-reply.
    """
    response = await client.chat.completions.create(
        model=model,
        messages=context.mark_cache(messages),
        max_tokens=config.MAX_COMPLETION_TOKENS,
        stream=True,
        stream_options={"include_usage": True},
        **extra,
    )
    try:
        async for chunk in response:
            if chunk.usage:  # final usage frame (include_usage)
                yield {"kind": "usage", "usage": chunk.usage.model_dump()}
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            for cite in citations(choice.delta):
                yield {"kind": "source", **cite}
            # DeepSeek and GLM stream their working out here. It is the only
            # place any model in this lineup exposes reasoning text, so it is
            # worth surfacing rather than dropping on the floor.
            thought = getattr(choice.delta, "reasoning_content", None)
            if thought:
                yield {"kind": "reasoning", "text": thought}
            if choice.finish_reason:
                yield {"kind": "finish", "reason": choice.finish_reason}
            if choice.delta.content:
                yield {"kind": "delta", "text": choice.delta.content}
    finally:
        with contextlib.suppress(Exception):
            await response.close()


async def run_turn(conv: dict, request: Request, user_message: str | None):
    """Stream one assistant turn as server-sent events.

    `user_message` is None when regenerating: the conversation already ends
    with the user turn to answer.
    """
    # 1+2. Compress before sending if the projection says we'd overflow.
    pending = user_message or ""
    if context.needs_compression(conv["last_prompt_tokens"], pending,
                                 len(conv["messages"]),
                                 conv.get("context_budget_tokens")):
        old = conv["messages"][:-config.KEEP_RECENT_MESSAGES]
        try:
            summary, facts, c_usage = await context.compress(
                client, old, conv["summary"], conv.get("compress_prompt"))
        except OpenAIError as e:
            # The summary call is the one tool this turn depends on. Failing it
            # must not fail the user's message: drop the oldest turns
            # unsummarized and say so, rather than losing the turn.
            conv["messages"] = conv["messages"][-config.KEEP_RECENT_MESSAGES:]
            yield sse({"type": "warning", "kind": "tool_failed",
                       "tool": "compression", "message": str(e),
                       "dropped": len(old)})
        else:
            conv["summary"] = summary
            conv["messages"] = conv["messages"][-config.KEEP_RECENT_MESSAGES:]
            written = context.append_memory(facts)
            yield sse({
                "type": "compression",
                "compressed_messages": len(old),
                "summary_tokens_est": context.estimate_tokens(summary),
                "memory_facts_added": written,
                "compression_usage": c_usage,
            })
        storage.save(conv)

    if user_message is not None:
        conv["messages"].append({"role": "user", "content": user_message})
        if conv["title"] == "New conversation":
            conv["title"] = user_message[:60]
        # Save before the request goes out. If the client vanishes mid-turn the
        # user's own message must still be there when they come back.
        storage.save(conv)

    request_messages = context.build_request_messages(
        conv["system_prompt"], context.load_memory(),
        conv["summary"], conv["messages"],
    )

    # Our own estimate of what we just sent, as a floor under the measured
    # number. Measurement still wins where it is trustworthy; this only stops a
    # provider that reports the prompt net of cache — and does not label the
    # cache read either — from silently shrinking the budget to nothing.
    estimated = sum(
        context.estimate_tokens(m["content"]) for m in request_messages
        if isinstance(m.get("content"), str))

    parts: list[str] = []
    sources: list[dict] = []
    # What the model did to reach this answer, kept alongside it. Without this
    # the trail exists only while the turn streams: reopen the conversation
    # later and the searches, pages, and reasoning are gone, leaving no way to
    # see how a claim was arrived at.
    activity: list[dict] = []
    saved = False

    def persist(interrupted: bool, usage: dict | None = None):
        """Record the assistant turn exactly once, however the stream ended."""
        nonlocal saved
        if saved:
            return
        saved = True
        reply = "".join(parts)
        # Never store a *blank* assistant turn — one that produced no text used
        # to be replayed upstream, where an empty assistant message breaks the
        # user/assistant alternation that tool pairing depends on. A turn that
        # thought or searched but never answered is a different case: it has
        # something worth keeping, and `for_api` drops empty content on the way
        # out, so it can be stored without reaching the model again.
        if reply or activity:
            message = {"role": "assistant", "content": reply}
            if interrupted:
                message["interrupted"] = True
            if activity:
                # Drop the private timing anchor; keep the computed duration.
                message["activity"] = [
                    {k: v for k, v in step.items() if not k.startswith("_")}
                    for step in activity
                ]
            if sources:
                message["sources"] = sources
            conv["messages"].append(message)
        if usage:
            conv["last_prompt_tokens"] = max(total_input(usage), estimated,
                                             0)
            tally(conv["totals"], usage)
        storage.save(conv)

    # 3. Stream the completion, from whichever endpoint this turn needs.
    # Tools run for every model; turns without them stay on the
    # chat-completions path, which is also the only one that streams reasoning
    # text.
    searching = bool(conv.get("web_search") or conv.get("web_fetch"))
    if searching:
        events = tools.stream_events(
            conv["model"], request_messages, config.MAX_COMPLETION_TOKENS,
            use_search=bool(conv.get("web_search")),
            use_fetch=bool(conv.get("web_fetch")),
            tool_prompt=conv.get("tool_prompt"))
    else:
        events = completion_events(
            conv["model"], request_messages,
            dict(config.MODEL_PARAMS.get(conv["model"], {})))

    usage = None
    finish_reason = None
    aborted = False
    started = False
    stream_error: Exception | None = None
    try:
        async for event in events:
            started = True
            # The client hung up (Stop button, closed tab, dead connection).
            # Break out so the generator's own cleanup closes the upstream
            # stream — otherwise the provider keeps generating, and billing.
            if await request.is_disconnected():
                aborted = True
                break
            kind = event["kind"]
            if kind == "usage":
                usage = event["usage"]
            elif kind == "finish":
                finish_reason = event["reason"]
            elif kind == "source":
                sources.append({"url": event["url"], "title": event["title"]})
            elif kind == "search":
                # Shown in the transcript as it happens, so the answer is not
                # the first sign that a tool ran — and kept with the answer, so
                # it is still there next time the conversation is opened.
                step = {"kind": "search", "query": event["query"],
                        "results": event["results"]}
                activity.append(step)
                yield sse({"type": "search", **step})
            elif kind == "reasoning":
                # Deltas: append to one step rather than stacking hundreds, and
                # time the span so a replayed turn can still say how long the
                # model spent on it.
                now = time.monotonic()
                if activity and activity[-1]["kind"] == "reasoning":
                    activity[-1]["text"] += event["text"]
                    activity[-1]["seconds"] = round(now - activity[-1]["_t0"], 1)
                else:
                    activity.append({"kind": "reasoning", "text": event["text"],
                                     "_t0": now, "seconds": 0.0})
                yield sse({"type": "reasoning", "text": event["text"]})
            elif kind == "fetch":
                step = {"kind": "fetch", "url": event["url"],
                        "title": event["title"], "chars": event["chars"]}
                activity.append(step)
                yield sse({"type": "fetch", **step})
            elif kind == "notice":
                yield sse({"type": "warning", "kind": "tool_unavailable",
                           "message": event["message"]})
            elif kind == "delta":
                parts.append(event["text"])
                yield sse({"type": "delta", "text": event["text"]})
        # Record the turn here, not after the try block: on a hard disconnect
        # nothing below the loop gets to run.
        persist(interrupted=aborted or finish_reason == "content_filter",
                usage=usage)
    except (OpenAIError, httpx.HTTPError) as e:
        if not started:
            # Failed before a single event: nothing partial to keep. Roll back
            # the user message so a retry doesn't double it, and let the UI put
            # the draft back in the composer. On a regenerate there is no new
            # user message to roll back — the one being re-answered stays, and
            # saying otherwise made the UI offer a resend of an empty draft.
            if user_message is not None and conv["messages"]:
                conv["messages"].pop()
            storage.save(conv)
            yield sse({"type": "error",
                       "user_message_kept": user_message is None,
                       **classify(e)})
            return
        # The stream broke mid-reply. Keep the partial and mark it interrupted
        # so the transcript shows what actually arrived.
        stream_error = e
        persist(interrupted=True)
    finally:
        # Starlette cancels this generator when the client disconnects, which
        # raises CancelledError at an await rather than running any of the code
        # above. persist() is idempotent, so this is the catch-all save.
        persist(interrupted=True)
        with contextlib.suppress(Exception):
            await events.aclose()

    if stream_error is not None:
        yield sse({"type": "error", "user_message_kept": True,
                   "partial_saved": bool(parts), **classify(stream_error)})
        return

    if aborted:
        return

    if finish_reason == "content_filter":
        yield sse({"type": "error", "kind": "content_filter",
                   "recoverable": False, "user_message_kept": True,
                   "partial_saved": bool(parts),
                   "message": "The provider stopped this reply for content policy "
                              "reasons. Edit the message and send it again."})
        return

    # 4. Report measured usage; it is next turn's budget ground truth.
    yield sse({
        "type": "done",
        "usage": usage,
        "input_tokens_total": total_input(usage) if usage else 0,
        "cost_reported": bool(usage and "cost" in usage),
        "cached_tokens": cached_tokens(usage) if usage else 0,
        "truncated": finish_reason == "length",
        "sources": sources,
        "totals": conv["totals"],
        "budget": {
            "prompt_tokens": conv["last_prompt_tokens"],
            "context_budget_tokens": (conv.get("context_budget_tokens")
                                      or config.CONTEXT_BUDGET_TOKENS),
        },
    })


@app.post("/api/conversations/{conv_id}/chat")
async def chat(conv_id: str, body: ChatIn, request: Request):
    conv = storage.load(conv_id)
    if conv is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # The page trims before sending, but the API is callable directly — and a
    # whitespace turn would be stored, titled "", and then dropped from the
    # wire by for_api, leaving a user message the model never saw.
    if not body.message.strip():
        return JSONResponse({"error": "message is empty"}, status_code=400)
    error = apply_overrides(conv, body.model, body.system_prompt)
    if error:
        return JSONResponse(error, status_code=400)
    return StreamingResponse(run_turn(conv, request, body.message),
                             media_type="text/event-stream")


@app.post("/api/conversations/{conv_id}/rewind")
async def rewind(conv_id: str):
    """Take the last user message back out of the conversation and return it.

    The recovery path for a reply the provider refused: retrying verbatim will
    be refused again, so the UI drops the pair and puts the text back in the
    composer for the user to edit.
    """
    conv = storage.load(conv_id)
    if conv is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    while conv["messages"] and conv["messages"][-1]["role"] == "assistant":
        conv["messages"].pop()
    text = ""
    if conv["messages"] and conv["messages"][-1]["role"] == "user":
        text = conv["messages"].pop()["content"]
    storage.save(conv)
    return {"text": text, "conversation": conv}


@app.post("/api/conversations/{conv_id}/regenerate")
async def regenerate(conv_id: str, body: RegenerateIn, request: Request):
    """Re-answer the last user message, discarding the reply we already have.

    Paired with Stop: interrupt a reply you don't want, then regenerate it
    without retyping. Trailing assistant turns are dropped (including an
    interrupted one) so the conversation ends on the user message again.
    """
    conv = storage.load(conv_id)
    if conv is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    error = apply_overrides(conv, body.model, body.system_prompt)
    if error:
        return JSONResponse(error, status_code=400)
    while conv["messages"] and conv["messages"][-1]["role"] == "assistant":
        conv["messages"].pop()
    if not conv["messages"]:
        return JSONResponse({"error": "nothing to regenerate"}, status_code=400)
    storage.save(conv)
    return StreamingResponse(run_turn(conv, request, None),
                             media_type="text/event-stream")
