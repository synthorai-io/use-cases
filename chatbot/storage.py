"""Conversation persistence: one JSON file per conversation under .data/.

A conversation is a plain dict:
    {
      "id": "8f2c...",           # hex id, also the filename
      "title": "first user message, truncated",
      "model": "gemini-3.6-flash",
      "system_prompt": "...",
      "summary": "",             # rolling summary produced by compression
      "messages": [...],         # verbatim turns still in context
      "last_prompt_tokens": 0,   # measured size of the last request sent
      "created_at": "...", "updated_at": "..."
    }

Files instead of a database on purpose: inspectable with cat, diffable,
trivially portable. Swapping in SQLite is an exercise for a real deployment.
"""

import json
import secrets
from datetime import datetime, timezone

import config


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(conv_id: str):
    # ids are server-generated hex; validate anyway so a hand-crafted id can
    # never escape the data directory. Returns None rather than raising: to
    # every caller a malformed id and an unknown id mean the same thing, and
    # letting it raise turned "GET /conversations/...." into a 500.
    if not conv_id.isalnum():
        return None
    return config.CONVERSATIONS_DIR / f"{conv_id}.json"


def _defaults(conv: dict) -> dict:
    """Fill in per-conversation settings added after a file was written.

    Conversations are plain JSON on disk, so an older file is simply missing
    the newer keys rather than being invalid. Normalize on read.
    """
    conv.setdefault("context_budget_tokens", None)  # None = use the env default
    # On by default: the tool is offered every turn and the model decides from
    # context whether the question actually needs it. Turning it off is a cost
    # decision, not the starting point.
    conv.setdefault("web_search", True)
    conv.setdefault("web_fetch", True)
    conv.setdefault("tool_prompt", None)      # None = the configured default
    conv.setdefault("compress_prompt", None)  # None = the configured default
    conv.setdefault("totals", {})
    for field in ("input_tokens", "output_tokens", "cached_tokens",
                  "cache_write_tokens", "web_searches", "web_fetches", "turns"):
        conv["totals"].setdefault(field, 0)
    conv["totals"].setdefault("cost", 0.0)
    conv["totals"].setdefault("cost_reported", False)
    conv["totals"].setdefault("cost_missing_turns", 0)
    return conv


def create(model: str, system_prompt: str) -> dict:
    conv = _defaults({
        "id": secrets.token_hex(8),
        "title": "New conversation",
        "model": model,
        "system_prompt": system_prompt,
        "summary": "",
        "messages": [],
        "last_prompt_tokens": 0,
        "created_at": _now(),
        "updated_at": _now(),
    })
    save(conv)
    return conv


def save(conv: dict) -> None:
    config.CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    conv["updated_at"] = _now()
    _path(conv["id"]).write_text(
        json.dumps(conv, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load(conv_id: str) -> dict | None:
    p = _path(conv_id)
    if p is None or not p.exists():
        return None
    return _defaults(json.loads(p.read_text(encoding="utf-8")))


def list_all(query: str = "") -> list[dict]:
    """List conversation summaries, newest first.

    A non-empty `query` filters on title and message text — a plain substring
    scan over the JSON files. That is fine for a few hundred conversations on
    one machine; a real deployment indexes instead.
    """
    if not config.CONVERSATIONS_DIR.is_dir():
        return []
    q = query.strip().lower()
    out = []
    for p in config.CONVERSATIONS_DIR.glob("*.json"):
        c = json.loads(p.read_text(encoding="utf-8"))
        if q:
            haystack = "\n".join(
                [c["title"], c.get("summary", "")]
                + [m["content"] for m in c["messages"]]
            ).lower()
            if q not in haystack:
                continue
        out.append({k: c[k] for k in ("id", "title", "model", "updated_at")})
    return sorted(out, key=lambda c: c["updated_at"], reverse=True)


def rename(conv_id: str, title: str) -> dict | None:
    conv = load(conv_id)
    if conv is None:
        return None
    conv["title"] = title.strip()[:120] or "Untitled"
    save(conv)
    return conv


def delete(conv_id: str) -> bool:
    p = _path(conv_id)
    if p is not None and p.exists():
        p.unlink()
        return True
    return False
