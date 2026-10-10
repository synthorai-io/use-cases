"""Ten problems an agent has in production, each run twice: broken, then fixed.

    python incidents.py                 run all ten
    python incidents.py cache-miss      run one

Every call is labelled with the case and the variant, so each pair can be found
in the console (Logs filter: metadata.case = cache-miss) and compared.

  serial-checks   three independent model calls made one after another
  slow-tool       the wait is in your own code, not in the model
  cache-miss      a timestamp at the top of the prompt defeats prompt caching
  retry-loop      a reply check that cannot pass while a tool is down, so the whole turn is retried
  cut-off         an output cap ends every reply before it starts, and the code retries
  reasoning       the model thinks before one-sentence answers that do not need it
  context-growth  every old lookup result is sent again on every later turn
  wrong-model     a frontier model does one-sentence classification
  bad-history     the saved conversation drops a tool call, so the first turn works and the second is rejected
  replay-mismatch a user message is saved without a suffix it was sent with, so a long history never hits the cache
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAIError

from agent import ANALYST_MODEL, PLANNER_MODEL, SYSTEM, call
from run import billing_client, cost_of
from tracing import Run, new_id

MANUAL = (Path(__file__).parent / "policy_manual.md").read_text()
QUESTION = "My order A-48213 arrived but the dock does not charge my laptop. Can I send it back?"
CHECKS = {
    "sentiment": "How does the customer feel?",
    "policy-check": "Which policy topics does this message touch?",
    "risk": "Is there anything in this message a human should review?",
}


def new_run(case: str, variant: str, session: str | None = None) -> Run:
    return Run(session or new_id(case), labels={"feature": "support-agent", "case": case, "variant": variant})


async def check(run: Run, span: str, question: str, **kwargs):
    messages = [{"role": "user", "content": f"{question}\n\nCustomer message: {QUESTION}\n\nAnswer in one sentence."}]
    return await call(run, span, model=ANALYST_MODEL, messages=messages, agent="analyst", **kwargs)


# 1. serial-checks -----------------------------------------------------------------------------

async def serial_checks(variant: str) -> list[Run]:
    run = new_run("serial-checks", variant)
    if variant == "before":
        for span, question in CHECKS.items():        # each call waits for the one before it
            await check(run, span, question)
    else:
        await asyncio.gather(*(check(run, span, q) for span, q in CHECKS.items()))
    return [run]


# 2. slow-tool ---------------------------------------------------------------------------------

async def lookup(table: str) -> dict:
    await asyncio.sleep(1.1)                         # stands in for one database query
    return {"table": table, "rows": 1}


async def slow_tool(variant: str) -> list[Run]:
    run = new_run("slow-tool", variant)
    await check(run, "classify", CHECKS["policy-check"])
    tables = ["orders", "order_items", "shipments", "returns"]
    if variant == "before":
        rows = [await lookup(t) for t in tables]     # four queries, one at a time
    else:
        rows = await asyncio.gather(*(lookup(t) for t in tables))
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"{QUESTION}\n\nWhat we looked up: {json.dumps(rows)}"}]
    await call(run, "answer", model=PLANNER_MODEL, messages=messages)
    return [run]


# 3. cache-miss --------------------------------------------------------------------------------

TURNS = [QUESTION, "How long does the refund take?", "Who pays for the return shipping?",
         "Can I get store credit instead?"]


async def cache_miss(variant: str) -> list[Run]:
    session, runs, history = new_id("cache-miss"), [], []
    for i, text in enumerate(TURNS):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if variant == "before":
            # The clock is the first line, so the prompt differs from its first token on every turn.
            system, user = f"Current time: {now}\n\n{SYSTEM}\n\n{MANUAL}", text
        else:
            # Stable text first, the part that changes last.
            system, user = f"{SYSTEM}\n\n{MANUAL}", f"{text}\n\n(Current time: {now})"
        run = new_run("cache-miss", variant, session)
        resp = await call(run, f"turn-{i + 1}", model=PLANNER_MODEL,
                          messages=[{"role": "system", "content": system}, *history,
                                    {"role": "user", "content": user}])
        # Save the user message exactly as it was sent, clock included, so the next turn
        # replays a prefix the cache has already seen (replay-mismatch is what happens otherwise).
        history += [{"role": "user", "content": user},
                    {"role": "assistant", "content": resp.choices[0].message.content or ""}]
        runs.append(run)
        await asyncio.sleep(1.5)
    return runs


# 4. retry-loop --------------------------------------------------------------------------------

TRACK = [{"type": "function", "function": {
    "name": "track_parcel", "description": "Live carrier status of an order's parcel.",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}}]


async def retry_loop(variant: str) -> list[Run]:
    """The carrier API is down. The code checks each reply for a parcel location and retries without one."""
    run = new_run("retry-loop", variant)
    base = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Where is order A-50977 right now?"}]
    for attempt in range(1, 6):
        # The model is left to decide on the tool. The system prompt tells it to look the order
        # up, and it does; forcing the choice on every step of a loop is not needed for that.
        plan = await call(run, f"plan-{attempt}", model=PLANNER_MODEL, messages=base, tools=TRACK)
        msg = plan.choices[0].message
        results = []
        for tc in msg.tool_calls or []:
            await asyncio.sleep(0.4)                 # the carrier API, which is down
            results.append({"role": "tool", "tool_call_id": tc.id, "content": '{"error": "carrier API timeout"}'})
        tool_failed = any('"error"' in r["content"] for r in results)
        messages = [*base, msg.model_dump(exclude_none=True), *results]
        answer = await call(run, f"answer-{attempt}", model=PLANNER_MODEL, messages=messages, tools=TRACK,
                            tool_choice="none")
        reply = answer.choices[0].message.content or ""
        if "sorting" in reply or "out for delivery" in reply:
            break                                    # the reply names a parcel location
        if variant == "after" and (tool_failed or not results):
            break                                    # a retry cannot help while the tool is down or unused
    return [run]


# 5. cut-off -----------------------------------------------------------------------------------

async def cut_off(variant: str) -> list[Run]:
    run = new_run("cut-off", variant)
    limit = 40 if variant == "before" else 2000
    prompt = ("List every policy topic this message touches and say for each whether the customer is "
              "entitled to what they ask for.")
    for attempt in range(1, 4):
        resp = await check(run, f"policy-check-{attempt}", prompt, max_tokens=limit)
        choice = resp.choices[0]
        if choice.finish_reason != "length" and (choice.message.content or "").strip():
            break                                    # a usable reply; otherwise try again
    return [run]


# 6. reasoning ---------------------------------------------------------------------------------

async def reasoning(variant: str) -> list[Run]:
    """Five short lookup-style answers. The default lets the model think before each one."""
    session, runs = new_id("reasoning"), []
    extra = {} if variant == "before" else {"reasoning_effort": "none"}
    for i, text in enumerate(TURNS + ["Do you match prices from other shops?"]):
        run = new_run("reasoning", variant, session)
        await call(run, "answer", model=PLANNER_MODEL,
                   messages=[{"role": "system", "content": SYSTEM + " Reply in one sentence."},
                             {"role": "user", "content": text}], **extra)
        runs.append(run)
    return runs


# 7. context-growth ----------------------------------------------------------------------------

ORDER_HISTORY = json.dumps([{"order_id": f"A-{48213 - 37 * n}", "placed_on": f"2026-{9 - n // 4:02d}-{28 - n:02d}",
                             "status": "delivered", "items": [{"sku": f"SKU-{1000 + 7 * n}", "name": "USB-C dock",
                             "qty": 1, "unit_price": 89.0}], "carrier": "DHL", "tracking": f"JD0146000{n:05d}",
                             "ship_to_postcode": "10115"} for n in range(12)])


LONG_TURNS = TURNS + ["And if the replacement is faulty too?", "OK, please start the return."]


async def context_growth(variant: str) -> list[Run]:
    session, runs, history = new_id("context-growth"), [], []
    # The length instruction lives in the system prompt, so the user message that is sent is the
    # one that is saved. If the two differed, each turn would rebuild a prefix the previous turn
    # never sent, and the cache numbers of this case would measure that bug instead.
    system = {"role": "system", "content": SYSTEM + " Answer in two sentences."}
    for i, text in enumerate(LONG_TURNS):
        lookup_result = {"role": "user", "content": f"(account lookup for this turn) {ORDER_HISTORY}"}
        run = new_run("context-growth", variant, session)
        resp = await call(run, f"turn-{i + 1}", model=PLANNER_MODEL,
                          messages=[system, *history, lookup_result, {"role": "user", "content": text}])
        reply = resp.choices[0].message.content or ""
        if variant == "before":
            history += [lookup_result]               # every old lookup is sent again on every later turn
        history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
        runs.append(run)
    return runs


# 8. wrong-model -------------------------------------------------------------------------------

async def wrong_model(variant: str) -> list[Run]:
    run = new_run("wrong-model", variant)
    model = "gpt-6.1-sol" if variant == "before" else ANALYST_MODEL
    async def one(span, question):
        messages = [{"role": "user", "content": f"{question}\n\nCustomer message: {QUESTION}\n\nAnswer in one sentence."}]
        return await call(run, span, model=model, messages=messages, agent="analyst")
    await asyncio.gather(*(one(span, q) for span, q in CHECKS.items()))
    await call(run, "answer", model=PLANNER_MODEL,
               messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": QUESTION}])
    return [run]


# 9. bad-history -------------------------------------------------------------------------------

async def bad_history(variant: str) -> list[Run]:
    """Turn one runs from messages held in memory and works. Turn two is rebuilt from what was saved."""
    session, runs = new_id("bad-history"), []
    opening = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Where is order A-50977 right now?"}]

    # Turn one: plan, tool, answer. Nothing has been saved yet, so both variants succeed.
    run = new_run("bad-history", variant, session)
    runs.append(run)
    plan = await call(run, "plan", model=PLANNER_MODEL, messages=opening, tools=TRACK, tool_choice="required")
    msg = plan.choices[0].message
    results = [{"role": "tool", "tool_call_id": tc.id, "content": '{"status": "in transit"}'} for tc in msg.tool_calls]
    answer = await call(run, "answer", model=PLANNER_MODEL, tools=TRACK, tool_choice="none",
                        messages=[*opening, msg.model_dump(exclude_none=True), *results])

    # What the app stores for the next turn.
    saved = msg.model_dump(exclude_none=True)
    if variant == "before":
        saved.pop("tool_calls", None)                # the bug: the assistant message is saved as text only
        saved["content"] = saved.get("content") or ""
    history = [saved, *results, {"role": "assistant", "content": answer.choices[0].message.content or ""}]

    # Turn two: the customer writes again, and the request is built from the saved history.
    run = new_run("bad-history", variant, session)
    runs.append(run)
    try:
        await call(run, "plan", model=PLANNER_MODEL, tools=TRACK,
                   messages=[*opening, *history, {"role": "user", "content": "And when will it arrive?"}])
    except OpenAIError as e:
        print(f"    turn two failed: {getattr(e, 'status_code', '')} {str(e)[:200]}")
    return runs

# 10. replay-mismatch --------------------------------------------------------------------------

async def replay_mismatch(variant: str) -> list[Run]:
    """A long history is cheap only while it is replayed exactly as it was sent.

    Both variants keep every old lookup, as the broken context-growth does, and both send each
    user message with a short instruction appended. The broken one saves the message without
    it, so every turn rebuilds a prompt the turn before never sent and the cache has nothing
    to match. The fixed one saves what it sent.
    """
    session, runs, history = new_id("replay-mismatch"), [], []
    for i, text in enumerate(LONG_TURNS):
        lookup_result = {"role": "user", "content": f"(account lookup for this turn) {ORDER_HISTORY}"}
        sent = text + " Answer in two sentences."
        run = new_run("replay-mismatch", variant, session)
        resp = await call(run, f"turn-{i + 1}", model=PLANNER_MODEL,
                          messages=[{"role": "system", "content": SYSTEM}, *history, lookup_result,
                                    {"role": "user", "content": sent}])
        saved = text if variant == "before" else sent    # the bug: saved is not what was sent
        history += [lookup_result, {"role": "user", "content": saved},
                    {"role": "assistant", "content": resp.choices[0].message.content or ""}]
        runs.append(run)
    return runs


CASES = {"serial-checks": serial_checks, "slow-tool": slow_tool, "cache-miss": cache_miss,
         "retry-loop": retry_loop, "cut-off": cut_off, "reasoning": reasoning,
         "context-growth": context_growth, "wrong-model": wrong_model, "bad-history": bad_history,
         "replay-mismatch": replay_mismatch}


async def report(http, case: str, variant: str, runs: list[Run], wall: float) -> dict:
    spans = [s for r in runs for s in r.spans]
    costs = await asyncio.gather(*(cost_of(http, s.request_id) for s in spans))
    missing = sum(c is None and bool(s.request_id) for s, c in zip(spans, costs))
    row = {"case": case, "variant": variant, "wall_s": round(wall, 1), "calls": len(spans),
           "input": sum(s.prompt_tokens for s in spans), "cached": sum(s.cached_tokens for s in spans),
           "output": sum(s.completion_tokens for s in spans), "cost": round(sum(c or 0 for c in costs), 6),
           "cost_missing": missing, "session": runs[0].session_id, "traces": [r.trace_id for r in runs]}
    print(f"  {variant:6s} {row['wall_s']:5.1f} s  {row['calls']:2d} calls  {row['input']:6d} in "
          f"({row['cached']:6d} cached)  {row['output']:5d} out  ${row['cost']:.6f}"
          f"{' +' + str(missing) + ' n/a' if missing else ''}   {runs[0].trace_id}")
    return row


OUT = Path(__file__).with_name(".data") / "incidents.json"


def save(row: dict):
    """Written after every run, so a case that fails later cannot take the earlier results with it."""
    OUT.parent.mkdir(exist_ok=True)
    kept = [r for r in (json.loads(OUT.read_text()) if OUT.exists() else [])
            if (r["case"], r["variant"]) != (row["case"], row["variant"])]
    OUT.write_text(json.dumps(kept + [row], indent=1))


async def main(names: list[str]):
    unknown = [n for n in names if n not in CASES]
    if unknown:
        sys.exit(f"unknown case: {', '.join(unknown)}\nchoose from: {', '.join(CASES)}")
    async with billing_client() as http:
        for name in names or list(CASES):
            print(f"\n{name}")
            for variant in ("before", "after"):
                t0 = time.time()
                try:
                    runs = await CASES[name](variant)
                except OpenAIError as e:
                    # One case failing (a timeout, an outage) should not end the other nine.
                    print(f"  {variant:6s} failed: {type(e).__name__}: {str(e)[:160]}")
                    continue
                save(await report(http, name, variant, runs, time.time() - t0))

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
