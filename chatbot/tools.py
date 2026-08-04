"""The gateway's server-side tools, which live on a different endpoint.

The gateway's search tool is only honoured on `/v1/messages` (the Anthropic
message format). On `/v1/chat/completions` a `tools` entry naming it is accepted
and then silently ignored: HTTP 200, no citations, no per-search charge, and an
answer written from training data. That silent no-op is why this module exists
rather than one extra field on the normal request.

Every model in the lineup can take these tools here, with one caveat measured
rather than assumed: Gemini accepts the declaration and then fails the moment it
actually calls one — `Function call is missing a thought_signature in
functionCall parts`. So a tool-call failure is handled the same way a missing
entitlement is: drop the tools, retry once, say so.

Both tools run server-side. The gateway performs the query or the retrieval and
folds the result into the prompt itself, so there is no tool_result round trip
to run here — only the query, the pages, and the citations to surface.

Search snippets run a few hundred characters each and regularly disagree with
one another, so they answer "what is out there" rather than "what does that page
say". Web fetch answers the second: the gateway retrieves one page and returns
its text. Both are billed per use and both are decided by the model, so a turn
that needs neither costs nothing extra.

Fetch has to be enabled on the key. Without the entitlement the whole request
fails with `web_fetch_not_enabled` — so rather than lose the turn, the tool is
dropped and the request retried once.
"""

import contextlib
import json

import httpx

import config
import context

# A server-side search loop can pause and ask to be resumed. Resuming is just
# re-sending, but it is still a billed request, so it is bounded.
MAX_RESUMES = 3


def declared_tools(use_search: bool, use_fetch: bool) -> list[dict]:
    tools = []
    if use_search:
        tools.append({"type": config.WEB_SEARCH_TOOL,
                      "max_uses": config.WEB_SEARCH_MAX_USES})
    if use_fetch:
        tools.append({"type": config.WEB_FETCH_TOOL,
                      "max_uses": config.WEB_FETCH_MAX_USES})
    return tools


def split_system(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Anthropic keeps the system prompt out of the message list.

    Returned as a block list rather than a string so the cache_control marker
    on the last block survives the conversion.
    """
    system: list[dict] = []
    for m in messages:
        if m["role"] != "system":
            continue
        content = m["content"]
        if isinstance(content, str):
            system.append({"type": "text", "text": content})
        else:
            system.extend(content)
    turns = [{"role": m["role"], "content": m["content"]}
             for m in messages if m["role"] != "system"]
    return system, turns


def _results_brief(block: dict) -> list[dict]:
    """Citations plus a short snippet, for the activity note and source list."""
    out = []
    for item in block.get("content") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        page = (item.get("encrypted_content") or "").strip()
        out.append({"url": url, "title": item.get("title") or url,
                    "snippet": page[:180]})
    return out


async def stream_events(model: str, messages: list[dict], max_tokens: int,
                        use_search: bool = True, use_fetch: bool = True,
                        tool_prompt: str | None = None):
    """Stream one tool-enabled turn, normalized to this app's event shape.

    Yields {"kind": "delta"|"search"|"source"|"usage"|"finish", ...}. Raises
    before the first yield if the request itself fails, which is how the caller
    tells a dead turn apart from one that broke mid-reply.
    """
    system, turns = split_system(context.mark_cache(messages))
    # Tell the model what it can spend. Without this it plans as if the tools
    # were unlimited, burns the last round mid-thought, and the turn ends on
    # "Let me search for..." with no answer — the caps are enforced silently by
    # the gateway, so the budget has to reach the model as text. Static, so it
    # does not disturb the cached prefix.
    budget = []
    if use_search:
        budget.append(f"{config.WEB_SEARCH_MAX_USES} web searches")
    if use_fetch:
        budget.append(f"{config.WEB_FETCH_MAX_USES} page fetches")
    if budget:
        template = (tool_prompt or config.TOOL_PROMPT).strip()
        try:
            note = template.format(tools=" and ".join(budget),
                                   searches=config.WEB_SEARCH_MAX_USES,
                                   fetches=config.WEB_FETCH_MAX_USES)
        except (KeyError, IndexError, ValueError):
            # The template is user-editable, so a stray brace is a typo, not a
            # reason to fail the turn. Send it as written.
            note = template
        if note:
            system = system + [{"type": "text", "text": note}]
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    conversation = list(turns)
    tools = declared_tools(use_search, use_fetch)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(300.0, connect=15.0)
    ) as http:
        for _ in range(MAX_RESUMES + 1):
            body = {"model": model, "max_tokens": max_tokens, "stream": True,
                    "messages": conversation}
            # Omit the key entirely rather than sending an empty array: after
            # dropping the tools on a retry, `"tools": []` is not the same as no
            # tools at all — some backends behind this gateway answer with
            # nothing at all.
            if tools:
                body["tools"] = tools
            if system:
                body["system"] = system

            query = ""
            content: dict[int, dict] = {}
            json_buffers: dict[int, str] = {}
            stop_reason = None

            async with http.stream(
                "POST", config.BASE_URL.rstrip("/") + config.WEB_SEARCH_ENDPOINT,
                headers={"Authorization": f"Bearer {config.API_KEY}",
                         "Content-Type": "application/json"},
                json=body,
            ) as response:
                if response.status_code != 200:
                    await response.aread()
                    if "thought_signature" in response.text:
                        # Gemini takes the declaration and then breaks when it
                        # calls one. Nothing to negotiate: drop both tools and
                        # answer without them rather than lose the turn.
                        use_search = use_fetch = False
                        tools = []
                        yield {"kind": "notice",
                               "message": f"{model} could not complete a tool "
                                          "call, so this turn answered without "
                                          "search or fetch."}
                        continue
                    if (use_fetch and "web_fetch_not_enabled" in response.text):
                        # The key lacks the entitlement. Losing the whole turn
                        # over an optional tool is the wrong trade — drop it and
                        # go again, telling the user why.
                        use_fetch = False
                        tools = declared_tools(use_search, False)
                        yield {"kind": "notice",
                               "message": "Web fetch is not enabled for this API "
                                          "key, so this turn ran without it."}
                        continue
                    response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    kind = event.get("type")
                    index = event.get("index", 0)

                    if kind == "message_start":
                        started = event.get("message", {}).get("usage") or {}
                        # This endpoint reports input_tokens net of cache, while
                        # the chat one reports it inclusive. Normalize to the
                        # inclusive form so both paths mean the same thing.
                        read = started.get("cache_read_input_tokens", 0) or 0
                        written = started.get("cache_creation_input_tokens", 0) or 0
                        usage["prompt_tokens"] += (
                            started.get("input_tokens", 0) + read + written)
                        if read:
                            usage["cache_read_input_tokens"] = (
                                usage.get("cache_read_input_tokens", 0) + read)
                        if written:
                            usage["cache_creation_input_tokens"] = (
                                usage.get("cache_creation_input_tokens", 0) + written)
                        # Cost can ride on either usage frame depending on the
                        # request; take it wherever it shows up.
                        if "cost" in started:
                            usage["cost"] = usage.get("cost", 0) + started["cost"]
                    elif kind == "content_block_start":
                        content[index] = dict(event.get("content_block", {}))
                        json_buffers[index] = ""
                    elif kind == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "input_json_delta":
                            json_buffers[index] = (json_buffers.get(index, "")
                                                   + delta.get("partial_json", ""))
                        elif delta.get("type") == "text_delta" and delta.get("text"):
                            yield {"kind": "delta", "text": delta["text"]}
                    elif kind == "content_block_stop":
                        block = content.get(index)
                        if not block:
                            continue
                        if block.get("type") == "server_tool_use":
                            with contextlib.suppress(json.JSONDecodeError):
                                block["input"] = json.loads(json_buffers.get(index) or "{}")
                            query = (block.get("input") or {}).get("query", "")
                        elif block.get("type") == "web_fetch_tool_result":
                            fetched = block.get("content") or {}
                            page = ((fetched.get("content") or {}).get("text")
                                    or "")
                            url = fetched.get("url") or ""
                            yield {"kind": "fetch", "url": url,
                                   "title": fetched.get("title") or url,
                                   "chars": len(page)}
                            if url:
                                yield {"kind": "source", "url": url,
                                       "title": fetched.get("title") or url}
                        elif block.get("type") == "web_search_tool_result":
                            found = _results_brief(block)
                            yield {"kind": "search", "query": query,
                                   "results": found}
                            for cite in found:
                                yield {"kind": "source", "url": cite["url"],
                                       "title": cite["title"]}
                    elif kind == "message_delta":
                        done = event.get("usage") or {}
                        usage["completion_tokens"] += done.get("output_tokens", 0)
                        server = done.get("server_tool_use") or {}
                        for field in ("web_search_requests", "web_fetch_requests"):
                            count = server.get(field)
                            if count:
                                usage[field] = usage.get(field, 0) + count
                        if "cost" in done:
                            usage["cost"] = usage.get("cost", 0) + done["cost"]
                        stop_reason = (event.get("delta") or {}).get("stop_reason")
                    elif kind == "error":
                        detail = (event.get("error") or {}).get("message",
                                                                "stream error")
                        raise httpx.HTTPError(detail)

            if stop_reason == "pause_turn":
                # The server-side search loop hit its own iteration limit and
                # asked to be continued. Echoing the turn back resumes it; there
                # is nothing for this client to execute.
                conversation.append({
                    "role": "assistant",
                    "content": [content[i] for i in sorted(content)],
                })
                continue

            yield {"kind": "usage", "usage": dict(usage)}
            yield {"kind": "finish",
                   "reason": {"max_tokens": "length",
                              "refusal": "content_filter"}.get(stop_reason,
                                                               stop_reason)}
            return

        yield {"kind": "usage", "usage": dict(usage)}
        yield {"kind": "finish", "reason": "paused"}
