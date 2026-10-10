"""A small support agent, built to produce a trace worth looking at.

One user message becomes one run:

  plan            the planner reads the message and asks for tools
  (your app)      the tools run locally: an order lookup and a policy search
  sentiment  \\
  policy-check  }  three checks by an "analyst" sub-agent, in parallel
  risk       /
  answer          the planner writes the reply

Every model call goes through `call()`, which is the only place that knows
about tracing: it attaches the run's headers and records what came back.

The tools run for one round. A model that answers a tool result by asking for
more tools gets no second round here, so pick a planner that answers after one.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from tracing import Run, Span

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(REPO_ROOT / ".env")

BASE_URL = os.environ.get("BASE_URL", "https://synthorai.io/v1")
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "gpt-6-luna")
ANALYST_MODEL = os.environ.get("ANALYST_MODEL", "deepseek-v4-flash")
# Used once, on purpose, when a run is asked to show a failed call.
BROKEN_MODEL = "model-that-does-not-exist"

# A model call can take minutes, most of all a reasoning model writing a long
# answer, so the timeout is generous. A short one does not make a slow call
# cheaper: the client gives up, and the request it abandoned may keep running.
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "300"))
# How many times a call is sent again when the connection drops before any response.
CONNECT_RETRIES = 2

if not os.environ.get("SYNTHORAI_API_KEY"):
    sys.exit("SYNTHORAI_API_KEY is not set. Copy .env.example to .env at the repo root and put your key in it.")

# The SDK's own retries are off: it would resend a call under the same span without telling
# us, and the spans this demo prints would no longer match the calls the gateway logged.
client = AsyncOpenAI(api_key=os.environ["SYNTHORAI_API_KEY"], base_url=BASE_URL,
                     timeout=REQUEST_TIMEOUT, max_retries=0)

SYSTEM = (
    "You are a support agent for an online electronics shop. Use the tools to look up the order "
    "and the policy before you answer. Be brief and specific, and never promise a refund the "
    "policy does not allow."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "lookup_order", "description": "Status, items and dates of an order.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "search_policy", "description": "The shop's policy on a topic such as returns, refunds or shipping.",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}}},
]

ORDERS = {
    "A-48213": {"status": "delivered", "delivered_on": "2026-09-28", "items": ["USB-C dock"], "total": 89.00},
    "A-50977": {"status": "in transit", "shipped_on": "2026-10-01", "items": ["webcam", "desk lamp"], "total": 73.50},
}
POLICY = {
    "returns": "Items can be returned within 30 days of delivery if unused and in the original box.",
    "refunds": "Refunds go to the original payment method within 5 business days of the return arriving.",
    "shipping": "Standard shipping takes 3 to 7 business days. Late parcels can be traced after day 8.",
}


async def run_tool(name: str, args: dict) -> str:
    """The tools are local and slow on purpose, so the gap shows up in the trace as "your app"."""
    if name == "lookup_order":
        await asyncio.sleep(1.2)
        return json.dumps(ORDERS.get(args.get("order_id", ""), {"error": "order not found"}))
    await asyncio.sleep(0.6)
    topic = args.get("topic", "").lower()
    found = [text for key, text in POLICY.items() if key.rstrip("s") in topic]
    return " ".join(found) or "No policy found for that topic."


async def call(run: Run, span: str, *, model: str, messages: list, agent: str | None = None,
               parent: str | None = None, **kwargs):
    """One model call, labelled as one span of the run."""
    for attempt in range(CONNECT_RETRIES + 1):
        t0 = time.time()
        try:
            raw = await client.chat.completions.with_raw_response.create(
                model=model, messages=messages, extra_headers=run.headers(span, agent, parent), **kwargs)
            break
        except APIStatusError as e:
            run.spans.append(Span(span, agent or "main", model, e.status_code, int((time.time() - t0) * 1000),
                                  e.response.headers.get("x-request-id", "")))
            raise
        except APIConnectionError as e:
            # No response at all: a timeout or a dropped connection. There is no status code
            # and no request id, so the span is recorded with status 0 and what we do know.
            run.spans.append(Span(span, agent or "main", model, 0, int((time.time() - t0) * 1000)))
            # A dropped connection is worth sending again. A timeout is not: the call already
            # had REQUEST_TIMEOUT seconds, and the request it abandoned may still be running.
            if isinstance(e, APITimeoutError) or attempt == CONNECT_RETRIES:
                raise
            await asyncio.sleep(1 + attempt)
    resp = raw.parse()
    usage = resp.usage
    run.spans.append(Span(span, agent or "main", model, 200, int((time.time() - t0) * 1000),
                          raw.headers.get("x-request-id", ""),
                          usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0,
                          (getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0) if usage else 0))
    return resp


async def analyst(run: Run, span: str, question: str, context: str, fail_first: bool = False) -> str:
    """A sub-agent call. With fail_first, the first attempt names a model that does not exist."""
    messages = [{"role": "user", "content": f"{question}\n\n{context}\n\nAnswer in one sentence."}]
    if fail_first:
        try:
            await call(run, span, model=BROKEN_MODEL, messages=messages, agent="analyst")
        except APIStatusError:
            span = f"{span}.retry"      # the retry is its own span, so both show in the waterfall
    resp = await call(run, span, model=ANALYST_MODEL, messages=messages, agent="analyst", max_tokens=400)
    return (resp.choices[0].message.content or "").strip()


async def handle(run: Run, history: list, user_message: str, show_failure: bool = False) -> str:
    """Handle one user message. `history` is the conversation so far and is extended in place."""
    messages = [{"role": "system", "content": SYSTEM}, *history, {"role": "user", "content": user_message}]

    plan = await call(run, "plan", model=PLANNER_MODEL, messages=messages, tools=TOOLS)
    step = plan.choices[0].message
    tool_messages = []
    if step.tool_calls:
        results = await asyncio.gather(*(
            run_tool(tc.function.name, json.loads(tc.function.arguments or "{}")) for tc in step.tool_calls))
        tool_messages = [{"role": "tool", "tool_call_id": tc.id, "content": r}
                         for tc, r in zip(step.tool_calls, results)]
        messages += [step.model_dump(exclude_none=True), *tool_messages]

    facts = "\n".join(m["content"] for m in tool_messages) or "(no tool results)"
    context = f"Customer message: {user_message}\nWhat we looked up:\n{facts}"
    sentiment, policy, risk = await asyncio.gather(
        analyst(run, "sentiment", "How does the customer feel?", context),
        analyst(run, "policy-check", "Does our policy allow what the customer is asking for?", context,
                fail_first=show_failure),
        analyst(run, "risk", "Is there anything in this request a human should review?", context),
    )

    notes = (f"Notes from your analyst (not from the customer). Sentiment: {sentiment} "
             f"Policy: {policy} Risk: {risk} Now write the reply to the customer.")
    answer = await call(run, "answer", model=PLANNER_MODEL, messages=[*messages, {"role": "user", "content": notes}],
                        tools=TOOLS, tool_choice="none")
    reply = (answer.choices[0].message.content or "").strip()
    history += [{"role": "user", "content": user_message}, {"role": "assistant", "content": reply}]
    return reply
