"""Call the library prompts every way the gateway takes them, and compare a call by id with the same call inline.

    python call.py              everything below
    python call.py --pairs 20   how many by-id / inline pairs to time (default 20)
    python call.py --only-check just the policy-check call
    python call.py --no-check   everything except the policy-check call

1. The same call three ways: production (no version), the staging label, version 1 pinned.
2. The same call on /v1/chat/completions, /v1/responses and /v1/messages, through the OpenAI and
   Anthropic SDKs with the prompt fields in extra_body.
3. POST /v1/prompts/support-answer/render: the request a call would send, without calling a model.
   It runs first, and Logs is then searched for a record of it (with the logs key, if set).
4. By id against inline: each pair renders the prompt, sends the rendered request inline, and
   sends the same values by id, in alternating order, with one salt per pair.
5. Round 2 of a tool loop by id, and the JSON answer of policy-check.
"""
import argparse
import json
import os
import time

import httpx

from anthropic import Anthropic
from openai import OpenAI

from common import (ANSWER, API_URL, BASE_URL, CHECK, Run, call, inference, median, new_id, prompt_body, salt, save,
                    settle)
from prompts import MESSAGES

SCRIPT = "call"


def variables(message: str, status: str, tag: str) -> dict:
    return {"product": "home electronics", "customer_message": message + tag, "order_status": status}


def served(raw) -> str:
    return raw.headers.get("x-synthorai-prompt-version", "?")


def three_ways(http, rows):
    print("1. One prompt id, three ways to choose the version\n")
    message, status = MESSAGES[0]
    run = Run(new_id("library-call"), labels={"feature": "prompt-library", "case": "three-ways"})
    for case, extra in (("production", {}), ("label staging", {"label": "staging"}), ("version 1", {"version": 1})):
        row = call(http, SCRIPT, f"three-ways: {case}", prompt_body(ANSWER, variables(message, status, salt()), **extra),
                   run=run, span=case.replace(" ", "-"))
        rows.append(row)
        print(f"  {case:14s} -> served version {row['served_version']}  ({row['status']}, {row['model']})")


def three_endpoints(rows):
    print("\n2. The same prompt on three endpoints, through the SDKs\n")
    key = os.environ["SYNTHORAI_API_KEY"]
    message, status = MESSAGES[1]
    run = Run(new_id("library-call"), labels={"feature": "prompt-library", "case": "three-endpoints"})
    openai = OpenAI(base_url=BASE_URL, api_key=key, max_retries=0)
    anthropic = Anthropic(base_url=BASE_URL.removesuffix("/v1"), api_key=key, max_retries=0)
    prompt = {"prompt_id": ANSWER, "prompt_variables": variables(message, status, salt())}

    calls = {
        "/v1/chat/completions": lambda h: openai.chat.completions.with_raw_response.create(
            model="any", messages=[{"role": "user", "content": ""}], extra_body=prompt, extra_headers=h),
        "/v1/responses": lambda h: openai.responses.with_raw_response.create(
            model="any", input="", extra_body=prompt, extra_headers=h),
        "/v1/messages": lambda h: anthropic.messages.with_raw_response.create(
            model="any", max_tokens=1500, messages=[{"role": "user", "content": ""}], extra_body=prompt,
            extra_headers=h),
    }
    for endpoint, send in calls.items():
        t0 = time.perf_counter()
        raw = send(run.headers(endpoint.rsplit("/", 1)[1]))
        latency = round((time.perf_counter() - t0) * 1000)
        reply = raw.parse()
        usage = reply.usage
        text = (reply.choices[0].message.content if endpoint.endswith("completions")
                else reply.output_text if endpoint.endswith("responses")
                else "".join(b.text for b in reply.content if b.type == "text"))
        rows.append({
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "script": SCRIPT,
            "case": f"three-endpoints: {endpoint}", "endpoint": endpoint, "prompt_id": ANSWER,
            "requested": "production", "served_version": int(served(raw)) if served(raw) != "?" else None,
            "model": reply.model, "status": raw.http_response.status_code, "error_code": None,
            "prompt_tokens": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None),
            "cached_tokens": 0, "latency_ms": latency, "request_id": raw.headers.get("x-request-id", ""),
            "session_id": run.session_id, "trace_id": run.trace_id, "cost_usd": None, "answer": text})
        print(f"  {endpoint:22s} -> served version {served(raw)}  ({reply.model}): {' '.join(text.split())[:90]}")


def render(http, message: str, status: str, tag: str, headers: dict | None = None) -> dict:
    """Render counts toward the key's rate limit, so a 429 waits as long as the answer asks.

    A render writes nothing, so a dropped connection is simply tried again."""
    for attempt in range(5):
        try:
            r = http.post(f"/prompts/{ANSWER}/render", headers=headers or {},
                          json={"format": "chat", "prompt_variables": variables(message, status, tag)})
        except httpx.TransportError:
            if attempt == 4:
                raise
            time.sleep(2)
            continue
        if r.status_code != 429:
            break
        time.sleep(float(r.headers.get("retry-after", "5")))
    r.raise_for_status()
    return r.json()


def show_render(http):
    """Render once under a session id of its own, then look in Logs for that session.

    A render is not a model call, so it should leave no record. Searching by its own session id
    keeps other calls to the same prompt out of the count."""
    print("\n3. What the gateway sends, without calling a model: POST /v1/prompts/support-answer/render\n")
    message, status = MESSAGES[0]
    session = new_id("render-check")
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 1))
    rendered = render(http, message, status, "", headers={"X-Session-Id": session})
    print("  " + json.dumps(rendered, indent=2).replace("\n", "\n  "))
    key = os.environ.get("SYNTHORAI_ADMIN_KEY") or os.environ.get("SYNTHORAI_PROMPTS_ADMIN_KEY")
    if not key:
        return
    time.sleep(15)                                   # give Logs time to show a record if there were one
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    r = httpx.get(f"{API_URL}/requests", params={"start": start, "end": end, "session_id": session},
                  headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code == 200:
        print(f"  requests in Logs for session {session} (the render's): {len(r.json()['data'])}")


def by_id_against_inline(http, rows, pairs: int):
    print(f"\n4. By id against inline, {pairs} pairs\n")
    run = Run(new_id("library-call"), labels={"feature": "prompt-library", "case": "by-id-vs-inline"})
    for n in range(pairs):
        message, status = MESSAGES[n % len(MESSAGES)]
        tag = salt()
        inline = render(http, message, status, tag)["request"]
        inline = {**inline, "max_tokens": int(inline.get("max_tokens", 1500))}
        by_id = prompt_body(ANSWER, variables(message, status, tag))
        order = [("inline", inline), ("by id", by_id)] if n % 2 == 0 else [("by id", by_id), ("inline", inline)]
        for case, body in order:
            rows.append(call(http, SCRIPT, f"by-id-vs-inline: {case}", body, run=run, span=f"pair-{n + 1}-{case.replace(' ', '-')}",
                             requested=case))
    for case in ("inline", "by id"):
        mine = [r for r in rows if r["case"] == f"by-id-vs-inline: {case}" and r["status"] == 200]
        print(f"  {case:7s} {len(mine):2d} calls  median prompt tokens {median([r['prompt_tokens'] for r in mine])}"
              f"  median client latency {median([r['latency_ms'] for r in mine])} ms")


TOOLS = [{"type": "function", "function": {
    "name": "lookup_order", "description": "Status, items and dates of an order.",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}}]


def tool_loop(http, rows, check: bool = True):
    print("\n5. Round 2 of a tool loop, by id\n")
    run = Run(new_id("library-call"), labels={"feature": "prompt-library", "case": "tool-loop"})
    tag = salt()
    vars_ = variables("Where is order A-50977? It has been a week.", "unknown: look it up", tag)
    first = call(http, SCRIPT, "tool-loop: round 1",
                 prompt_body(ANSWER, vars_, tools=TOOLS, tool_choice="required",
                             messages=[{"role": "user", "content": ""}]), run=run, span="round-1")
    rows.append(first)
    tool_call = (first.get("tool_calls") or [None])[0]
    print(f"  round 1 asked for: {tool_call['function']['name'] if tool_call else 'no tool'}"
          f"({tool_call['function']['arguments'] if tool_call else ''})")
    if not tool_call:
        return
    message = {"role": "assistant", "content": first["answer"] or None, "tool_calls": first["tool_calls"]}
    conversation = [{"role": "user", "content": ""}, message,
                    {"role": "tool", "tool_call_id": tool_call["id"],
                     "content": '{"status": "in transit", "shipped_on": "2026-10-01", "carrier": "DHL"}'}]
    second = call(http, SCRIPT, "tool-loop: round 2", prompt_body(ANSWER, vars_, tools=TOOLS, messages=conversation),
                  run=run, span="round-2")
    rows.append(second)
    print(f"  round 2 answered from the tool result (version {second['served_version']}): "
          f"{' '.join(second['answer'].split())[:140]}")

    if check:
        policy_check(http, rows, run)


def policy_check(http, rows, run=None):
    run = run or Run(new_id("library-call"), labels={"feature": "prompt-library", "case": "policy-check"})
    print("\n   policy-check returns JSON that follows its schema:")
    # policy-check's version stores no reasoning_effort, so the caller sends it: a short check needs none.
    check = call(http, SCRIPT, "policy-check", prompt_body(CHECK, {"customer_message": MESSAGES[2][0] + salt()},
                                                         reasoning_effort="none"), run=run, span="policy-check")
    rows.append(check)
    print(f"   {check['answer']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", type=int, default=20)
    ap.add_argument("--only-check", action="store_true")
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    rows = []
    try:
        run_all(args, rows)
    finally:
        # Whatever ran is kept, even when a later step fails.
        save(SCRIPT, settle(rows))


def run_all(args, rows):
    with inference() as http:
        if args.only_check:
            policy_check(http, rows)
        else:
            show_render(http)
            three_ways(http, rows)
            three_endpoints(rows)
            by_id_against_inline(http, rows, args.pairs)
            tool_loop(http, rows, check=not args.no_check)


if __name__ == "__main__":
    main()
