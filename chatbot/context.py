"""Context budget, compression, and cross-session memory.

The three ideas this module implements:

1. Budget by measurement, not by guess. Token counts differ per model
   (the same text can tokenize 40% apart across vendors), so the only
   number we trust is `usage.prompt_tokens` reported by the previous
   response. A chars/4 estimate covers only the delta the API has not
   seen yet.

2. Compress, don't truncate. When the next request would exceed the
   budget, everything except the most recent messages is folded into a
   rolling summary by a cheap model. The bot keeps long-range context
   at a fraction of the tokens.

3. Split memory from summary. The summary is conversation-shaped and
   dies with the conversation. Durable facts about the user ("prefers
   Python", "timezone is UTC+8") are appended to a memory file that every
   future conversation loads. Compression is the natural moment to
   extract them, because that is when the model re-reads old turns anyway.
"""

import json
import re
from datetime import datetime

from openai import AsyncOpenAI

import config


def estimate_tokens(text: str) -> int:
    """Rough token estimate for text the API has not measured yet.

    Char-based (len/4 for Latin-dominant text, closer to len/2 for CJK), and
    intentionally pessimistic: overestimating triggers compression one turn
    early, which is cheap; underestimating overflows the window, which is not.
    """
    cjk = len(re.findall(r"[\u3000-\u9fff\uac00-\ud7af]", text))
    other = len(text) - cjk
    return cjk // 2 + other // 4 + 8  # +8 for message framing overhead


def needs_compression(last_prompt_tokens: int, pending_text: str,
                      message_count: int, budget: int | None = None) -> bool:
    """True when the next request is projected to exceed the budget.

    last_prompt_tokens is the measured size of the previous request; the next
    one adds the previous reply, the new user message, and the completion cap.
    `budget` overrides the configured default, so a conversation can carry its
    own window size set from the Settings panel.
    """
    if message_count <= config.KEEP_RECENT_MESSAGES:
        return False  # nothing old enough to fold away
    projected = (
        last_prompt_tokens
        + estimate_tokens(pending_text)
        + config.MAX_COMPLETION_TOKENS
    )
    return projected > (budget or config.CONTEXT_BUDGET_TOKENS)


async def compress(client: AsyncOpenAI, messages: list[dict],
                   prior_summary: str,
                   prompt: str | None = None) -> tuple[str, list[str], dict]:
    """Fold `messages` (+ the prior summary) into a new summary and facts.

    Returns (summary, facts, usage_dict). On a malformed model reply, falls
    back to treating the whole reply as the summary rather than failing the
    user's turn: a sloppy summary beats a dead chatbot.
    """
    transcript = "\n".join(
        f"[{m['role']}] {m['content']}" for m in messages
    )
    prior = (
        f" (an earlier summary already exists and is included first:"
        f"\n{prior_summary})" if prior_summary else ""
    )
    resp = await client.chat.completions.create(
        model=config.SUMMARY_MODEL,
        messages=[{
            "role": "user",
            "content": (prompt or config.COMPRESS_PROMPT).format(
                prior=prior, transcript=transcript),
        }],
        max_tokens=config.COMPRESS_MAX_TOKENS,
    )
    raw = (resp.choices[0].message.content or "").strip()
    usage = resp.usage.model_dump() if resp.usage else {}
    # Lenient parse: some models wrap JSON in fences despite instructions.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(cleaned)
        summary = str(data.get("summary", "")).strip() or raw
        facts = [str(f).strip() for f in data.get("facts", []) if str(f).strip()]
    except (json.JSONDecodeError, AttributeError):
        summary, facts = raw, []
    return summary, facts, usage


def save_memory(text: str) -> None:
    """Overwrite the memory file — the Settings panel edits it as plain text."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    body = text.strip()
    config.MEMORY_FILE.write_text(body + "\n" if body else "", encoding="utf-8")


def load_memory() -> str:
    if config.MEMORY_FILE.exists():
        return config.MEMORY_FILE.read_text(encoding="utf-8").strip()
    return ""


def append_memory(facts: list[str]) -> int:
    """Append new facts to the memory file, skipping exact duplicates.

    Returns how many were actually written. Dedup is deliberately dumb
    (exact line match); semantic dedup is an extension, not MVP.
    """
    if not facts:
        return 0
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = set(load_memory().splitlines())
    fresh = [f"- {f}" for f in facts if f"- {f}" not in existing]
    if fresh:
        with config.MEMORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(fresh) + "\n")
    return len(fresh)


def for_api(messages: list[dict]) -> list[dict]:
    """Strip local bookkeeping keys before sending messages upstream.

    Stored messages may carry an `interrupted` flag (set when a reply was cut
    short). That is ours, not the API's, and strict providers reject unknown
    keys — so the wire format is always exactly role + content.
    """
    # Empty turns are dropped, not sent: a blank assistant message is not just
    # useless context, it breaks alternation upstream and makes later tool
    # rounds fail with errors that name the wrong message.
    return [{"role": m["role"], "content": m["content"]} for m in messages
            if (m.get("content") or "").strip()]


def today() -> str:
    """Local date, day granularity.

    Deliberately not a timestamp: this string lands in the prompt prefix, so
    anything finer than a day would invalidate the provider's cache on every
    single request.
    """
    return datetime.now().astimezone().strftime("%Y-%m-%d (%A)")


def mark_cache(messages: list[dict]) -> list[dict]:
    """Put the cache breakpoint at the end of the system section.

    Measured on this gateway: `cache_control` is honoured on a system block and
    silently ignored anywhere else. Marking the newest user message — the usual
    multi-turn pattern, which would let the whole history be cached — reads back
    zero cached tokens at full price. So the cacheable prefix here is persona,
    memory, and summary; conversation history is not cacheable.

    That is exactly why those three are ordered most-stable-first: the mark goes
    after the last of them, and everything before it is what gets reused.

    Caching only engages once that prefix clears the model's minimum (~1024
    tokens). A bare persona is a few hundred, so a fresh conversation caches
    nothing and one with accumulated memory and a summary caches everything.
    """
    if not config.CACHE_ENABLED:
        return messages
    marked = list(messages)
    last_system = max(
        (i for i, m in enumerate(marked) if m["role"] == "system"), default=-1)
    if last_system < 0:
        return marked
    block = dict(marked[last_system])
    content = block.get("content")
    if isinstance(content, str) and content:
        block["content"] = [{
            "type": "text",
            "text": content,
            "cache_control": {"type": "ephemeral", "ttl": config.CACHE_TTL},
        }]
        marked[last_system] = block
    return marked


def build_request_messages(system_prompt: str, memory: str, summary: str,
                           recent: list[dict]) -> list[dict]:
    """Assemble the message list, most-stable content first.

    Ordering is deliberate for prompt caching: providers cache a shared
    prefix, and the discount survives only while the prefix is byte-stable.
    The persona never changes; memory changes rarely; the summary changes
    only on compression. Each update invalidates the cache from that point
    on, so the churn belongs at the end, right before the messages.
    """
    msgs = [{"role": "system", "content": system_prompt}]
    if memory:
        msgs.append({
            "role": "system",
            "content": "Long-term memory about this user from earlier "
                       "conversations:\n" + memory,
        })
    if summary:
        msgs.append({
            "role": "system",
            "content": "Summary of the earlier part of this conversation "
                       "(older messages were compressed away):\n" + summary,
        })
    # Last of the system blocks, and not by accident. A model with no date
    # anchor assumes its training cutoff is "now": it writes search queries
    # with a stale year in them and reads a release table as if the newest row
    # were current. This block rolls over daily, so it sits after the ones that
    # don't — persona, memory, and summary keep their cache across midnight.
    msgs.append({
        "role": "system",
        "content": f"Today's date is {today()}. Treat this as authoritative, "
                   "including when deciding what to search for and when judging "
                   "whether a source is current — your training data is older "
                   "than this. Do not put a year in a search query unless the "
                   "user asked about that year.",
    })
    return msgs + for_api(recent)
