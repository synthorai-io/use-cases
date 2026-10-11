"""The tracing layer of the demo: a few ids and one JSON header.

Nothing here talks to a tracing backend. The gateway already logs every call;
these headers only tell it which calls belong together:

  X-Session-Id          one conversation (many runs)
  X-Trace-Id            one run: one user message handled from start to finish
  X-Span-Id             one model call inside that run
  X-Agent-Id            which agent made the call
  X-Parent-Agent-Id     the agent that started it, for sub-agents
  X-Synthorai-Metadata  labels to filter and split cost by (feature, release, _user)

The ids are yours to choose: up to 128 printable ASCII characters each. They
come back on the response and are never sent to the model provider.

The demo sends all of them except X-Parent-Agent-Id. Its one sub-agent is
started by the main agent, so there is no nesting to describe; `headers()`
takes a `parent` for agents that do have it.
"""
import json
import secrets
from dataclasses import dataclass, field


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(6)}"


@dataclass
class Span:
    """What we know about one call from our side of the wire."""
    name: str
    agent: str
    model: str
    status: int
    ms: int
    request_id: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class Run:
    """One agent run, which the gateway shows as one trace."""
    session_id: str
    labels: dict = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: new_id("trace"))
    spans: list = field(default_factory=list)

    def headers(self, span: str, agent: str | None = None, parent: str | None = None) -> dict:
        h = {
            "X-Session-Id": self.session_id,
            "X-Trace-Id": self.trace_id,
            "X-Span-Id": span,
        }
        if agent:
            # Only sub-agents need an id. Calls without one belong to the main agent.
            h["X-Agent-Id"] = agent
        if parent:
            h["X-Parent-Agent-Id"] = parent
        if self.labels:
            # A flat JSON object of strings: at most 10 keys, values up to 128 bytes.
            h["X-Synthorai-Metadata"] = json.dumps(self.labels, separators=(",", ":"))
        return h
