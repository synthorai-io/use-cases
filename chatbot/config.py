"""Configuration for Synthorai Chatbot Demo.

Everything is an environment variable with a sensible default, so `uvicorn
server:app` after setting SYNTHORAI_API_KEY is a working deployment. See
.env.example for the full list.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# The API key is shared by every use case in this repo, so it lives in a single
# .env at the repo root. A .env next to this file is optional and wins where the
# two overlap (dotenv keeps the first value it sees for a given name), which is
# how you give one use case its own model lineup or budget without forking the
# shared key.
REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(REPO_ROOT / ".env")

APP_NAME = os.environ.get("APP_NAME", "Synthorai Chatbot Demo")

API_KEY = os.environ.get("SYNTHORAI_API_KEY", "")
BASE_URL = os.environ.get("BASE_URL", "https://synthorai.io/v1")

# Built-in web search, billed per search by the gateway. It is only honoured on
# /v1/messages — naming the tool on /v1/chat/completions returns 200 and then
# silently does not search. See tools.py for why that shapes the code.
WEB_SEARCH_ENDPOINT = os.environ.get("WEB_SEARCH_ENDPOINT", "/messages")
WEB_SEARCH_TOOL = os.environ.get("WEB_SEARCH_TOOL", "synthorai:web_search")
# Uncapped, one question can cost real money: a single turn here ran three
# searches and two fetches before a token was billed. The model decides how
# many rounds it wants, so the cap is the only brake.
WEB_SEARCH_MAX_USES = int(os.environ.get("WEB_SEARCH_MAX_USES", "3"))

# Page fetching, also gateway-side and also billed per use. Needs to be enabled
# on the key: without the entitlement the request fails with
# `web_fetch_not_enabled` rather than degrading, so tools.py drops the tool and
# retries once instead of losing the turn.
WEB_FETCH_TOOL = os.environ.get("WEB_FETCH_TOOL", "synthorai:web_fetch")
# The gateway defaults this to 3 and caps it at 10. Each use is billed, so the
# demo asks for fewer.
WEB_FETCH_MAX_USES = int(os.environ.get("WEB_FETCH_MAX_USES", "2"))

# What the model is told about its tool budget. Editable per conversation in
# Settings, because the wording measurably changes both cost and answer
# quality: with no note at all a question spent the whole tool budget and
# finished mid-sentence; "spend deliberately and stop early" cost less but
# gave up and told the user to go read the page; the wording below skipped
# search entirely, fetched the two authoritative pages, and cost the least of
# the three. Exact figures vary per model — change the wording and watch the
# per-turn cost line.
#
# Placeholders: {tools} lists what is available this turn ("3 web searches and 2
# page fetches"), {searches} and {fetches} are the raw caps.
TOOL_PROMPT = os.environ.get("TOOL_PROMPT", (
    "You may use up to {tools} on this turn. That is a ceiling, not a target — "
    "use what the question needs. Answer it yourself from what you read: do not "
    "tell the user to go look at a page you were able to fetch. If the budget "
    "runs out before you are certain, give your best answer and say which part "
    "is unconfirmed."
))

# Models that stream their working out as `reasoning_content` on the
# chat-completions path. Measured, not assumed: the Claude models return no
# thinking blocks through this gateway even with the thinking parameter set.
REASONING_MODELS = [
    m.strip() for m in os.environ.get(
        "REASONING_MODELS", "deepseek-v4-flash,glm-5.2").split(",") if m.strip()
]

# Models offered in the UI picker. Comma-separated so a .env can swap the
# lineup without touching code. The first entry is the default for new
# conversations. Models added from the Settings panel are stored alongside the
# conversations and appended to this list.
BUILTIN_MODELS = [
    m.strip()
    for m in os.environ.get(
        "MODELS",
        "gemini-3.6-flash,"
        "deepseek-v4-flash,"
        "claude-haiku-4-5,"
        "glm-5.2,"
        "claude-sonnet-5,"
        "claude-opus-5,"
        "claude-fable-5",
    ).split(",")
    if m.strip()
]

# Picker grouping, purely cosmetic: the UI renders one <optgroup> per tier so a
# longer lineup stays readable. Models missing from here fall into "other".
MODEL_TIERS: dict[str, str] = {
    "gemini-3.6-flash": "fast & cheap",
    "deepseek-v4-flash": "fast & cheap",
    "claude-haiku-4-5": "fast & cheap",
    "glm-5.2": "fast & cheap",
    "claude-sonnet-5": "balanced",
    "claude-opus-5": "frontier",
    "claude-fable-5": "frontier",
}
TIER_ORDER = ["fast & cheap", "balanced", "frontier", "added by you"]


def custom_models() -> list[str]:
    """Model ids added from the Settings panel, kept next to the .data files."""
    if MODELS_FILE.exists():
        try:
            return [str(m) for m in json.loads(MODELS_FILE.read_text("utf-8"))]
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def save_custom_models(models: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_FILE.write_text(json.dumps(models, indent=2), encoding="utf-8")


def all_models() -> list[str]:
    seen, out = set(), []
    for m in BUILTIN_MODELS + custom_models():
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def model_groups() -> list[dict]:
    """Group the lineup by tier, preserving TIER_ORDER, for the UI picker."""
    added = set(custom_models()) - set(BUILTIN_MODELS)
    buckets: dict[str, list[str]] = {}
    for m in all_models():
        tier = "added by you" if m in added else MODEL_TIERS.get(m, "added by you")
        buckets.setdefault(tier, []).append(m)
    return [
        {"label": tier, "models": buckets[tier]}
        for tier in TIER_ORDER
        if tier in buckets
    ]


# Extra request params per model. Chat turns are single-step work, so models
# with a reasoning dial get it pinned to the floor: measured on Gemini 3.6
# Flash, `minimal` cut per-call cost by an order of magnitude with no visible
# quality change on chat-shaped tasks (synthorai.io/blog/gemini-3-6-flash-cost/).
# Models not listed here get no extra params.
MODEL_PARAMS: dict[str, dict] = {
    "gemini-3.6-flash": {"reasoning_effort": "minimal"},
}

# Prompt caching, the single biggest cost lever in this demo. Measured on
# this gateway: a cached read costs a fraction of an uncached one (the exact
# ratio differs per model). Gemini accepts the marker and ignores it. The
# write carries a small premium over a normal read, so the break-even is the
# second request.
CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "1").strip().lower() not in (
    "0", "false", "no")
# "5m" is the default; "1h" survives longer gaps but doubles the write premium.
CACHE_TTL = os.environ.get("CACHE_TTL", "5m")

# Context budget in tokens. When the next request would exceed it, the server
# compresses old turns into a summary before sending. This is a working budget,
# not a model limit — every model in the default lineup has a far larger window,
# and the number to set is the point where a turn costs more than it is worth.
# To watch compression happen, drop it to a few thousand in Settings: it is a
# per-conversation value, so one conversation can demo it without changing the
# rest.
CONTEXT_BUDGET_TOKENS = int(os.environ.get("CONTEXT_BUDGET_TOKENS", "102400"))

# How many recent messages survive compression verbatim. Older ones are
# folded into the rolling summary.
KEEP_RECENT_MESSAGES = int(os.environ.get("KEEP_RECENT_MESSAGES", "8"))

# Model used for the compression call itself. Summarization is single-step
# and quality-tolerant, so default to the cheapest chat model in the lineup.
SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "deepseek-v4-flash")

# Output cap for that call: the summary plus the extracted facts. 600 fits the
# "under 200 words" the default prompt asks for with room for the JSON around
# it; raise it if you rewrite COMPRESS_PROMPT to ask for something longer,
# since a summary truncated mid-sentence is what the next turn inherits.
COMPRESS_MAX_TOKENS = int(os.environ.get("COMPRESS_MAX_TOKENS", "600"))

# What the summary model is asked to do when the window overflows. One call
# produces both halves of memory: `summary` is conversation-scoped and dies
# with it, `facts` are cross-conversation and land in memory.md. They are
# merged because compression already pays to re-read the dropped messages;
# extracting durable facts from them costs nothing extra.
#
# Placeholders: {prior} expands to the existing summary when there is one,
# {transcript} to the messages about to be dropped. Editable per conversation
# from Settings -> Memory, because the wording here decides what survives.
COMPRESS_PROMPT = os.environ.get("COMPRESS_PROMPT", (
    "You maintain context for an ongoing chat. Below are the older messages "
    "that are about to be dropped from the model's context window{prior}.\n"
    "\n"
    "Reply with JSON only, no code fences, in this exact shape:\n"
    '{{"summary": "...", "facts": ["...", "..."]}}\n'
    "\n"
    '- "summary": one dense paragraph (under 200 words) of what happened in '
    "these messages: topics, decisions, open questions, and anything the "
    "assistant promised to do. Written so the conversation can continue as if "
    "nothing was dropped.\n"
    '- "facts": zero or more durable facts about the user worth remembering '
    "across conversations (preferences, constraints, ongoing projects). Only "
    "include things that will still be true next week. No conversation "
    "plot.\n"
    "\n"
    "Messages:\n"
    "{transcript}\n"
))

# Cap on completion length for ordinary chat turns, so one runaway reply
# cannot eat the whole context budget.
MAX_COMPLETION_TOKENS = int(os.environ.get("MAX_COMPLETION_TOKENS", "1024"))

# Where conversations, the memory file, and user-added models live.
DATA_DIR = Path(os.environ.get("DATA_DIR", ".data"))
CONVERSATIONS_DIR = DATA_DIR / "conversations"
MEMORY_FILE = DATA_DIR / "memory.md"
MODELS_FILE = DATA_DIR / "models.json"

# System-prompt presets offered in the UI. Files in presets/ named
# <label>.txt; the first (alphabetically) is the default for new conversations.
PRESETS_DIR = Path(__file__).parent / "presets"


def load_presets() -> dict[str, str]:
    presets = {}
    if PRESETS_DIR.is_dir():
        for f in sorted(PRESETS_DIR.glob("*.txt")):
            presets[f.stem] = f.read_text(encoding="utf-8").strip()
    if not presets:
        presets["assistant"] = "You are a concise, helpful assistant."
    return presets
