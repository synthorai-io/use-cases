"""Run two support conversations and print what the gateway will show for them.

    python run.py            two conversations, two turns each
    python run.py --fail     the same, with one call that fails and is retried

Each conversation is one session, each turn one trace. When it finishes, open
Traces in the Synthorai console: the ids printed here are the ones listed there.
"""
import asyncio
import os
import sys

import httpx
from openai import OpenAIError

from agent import BASE_URL, handle
from tracing import Run, new_id

CONVERSATIONS = [
    ("u_1001", ["My order A-48213 arrived but the dock does not charge my laptop. Can I send it back?",
                "OK. How long until I get my money back once you have it?"]),
    ("u_1002", ["Where is order A-50977? It has been a week.",
                "If it does not arrive by Friday, can I cancel and get a refund?"]),
]


def billing_client() -> httpx.AsyncClient:
    """One client for every lookup, so they share a few connections instead of opening one each."""
    return httpx.AsyncClient(timeout=20, headers={"Authorization": f"Bearer {os.environ['SYNTHORAI_API_KEY']}"})


async def cost_of(http: httpx.AsyncClient, request_id: str) -> float | None:
    """The billing record of one request. It is written a moment after the call returns."""
    if not request_id:
        return None                                  # a call that got no response has no record
    for _ in range(6):
        try:
            r = await http.get(f"{BASE_URL}/generation", params={"id": request_id})
            if r.status_code == 200:
                return r.json()["data"]["total_cost"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            pass                                     # a dropped connection or a half-written record: try again
        await asyncio.sleep(2)
    return None


async def main(show_failure: bool):
    runs = []
    for n, (user, turns) in enumerate(CONVERSATIONS):
        session_id, history = new_id("support"), []
        print(f"\nsession {session_id}  (customer {user})")
        for i, text in enumerate(turns):
            run = Run(session_id, labels={"feature": "support-agent", "release": "v2", "_user": user})
            try:
                reply = await handle(run, history, text, show_failure=show_failure and n == 0 and i == 1)
            except OpenAIError as e:
                # A wrong key, a model this key cannot use, a gateway that cannot be reached.
                sys.exit(f"  trace {run.trace_id} stopped: {type(e).__name__}: {str(e)[:300]}")
            runs.append(run)
            print(f"  trace {run.trace_id}")
            print(f"    customer: {text}")
            print(f"    agent:    {reply[:160]}{'...' if len(reply) > 160 else ''}")

    print("\nwhat each call cost\n")
    total, missing = 0.0, 0
    async with billing_client() as http:
        for run in runs:
            costs = await asyncio.gather(*(cost_of(http, s.request_id) for s in run.spans))
            print(f"trace {run.trace_id}  session {run.session_id}")
            for s, c in zip(run.spans, costs):
                total += c or 0
                missing += c is None and bool(s.request_id)   # a call with no response has nothing to bill
                cost = f"${c:.6f}" if c is not None else "n/a"
                print(f"  {s.name:20s} {s.agent:8s} {s.model:26s} {s.status:3d}  {s.ms:5d} ms  "
                      f"{s.prompt_tokens:5d} in {s.completion_tokens:4d} out  {cost}")
    print(f"\n{sum(len(r.spans) for r in runs)} calls in {len(runs)} traces and "
          f"{len(CONVERSATIONS)} sessions, ${total:.5f} in total"
          + (f" ({missing} without a billing record, so the total is a floor)" if missing else ""))


if __name__ == "__main__":
    asyncio.run(main("--fail" in sys.argv))
