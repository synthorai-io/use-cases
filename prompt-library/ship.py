"""Ship a version by moving a label, and measure how long the move takes to reach calls.

    python ship.py                          5 moves each way, calls from 2 s before each move
    python ship.py --before 0 --pause 15    no calls in the 15 s before each move

Calls go out every 0.5 s from 2 s before each move. The move points production at the other
version through the API; the calls continue until one is answered by the new version, and for
10 s more to see whether any call went back to the old one. Times are counted from the moment
the move's answer arrived, so a call sent before it has a negative time.

How soon a move shows depends on when a call last read the label: the gateway keeps what it read
for a few seconds. With calls arriving all the time, the new version shows up within that
window; after a quiet spell the next call reads the label afresh. --before and --pause set up
each case. The calls are tiny (an output cap of 16 tokens): only the version
header matters here. Production is left on v1 at the end.
"""
import argparse
import asyncio
import os
import time

import httpx

from common import ANSWER, BASE_URL, admin, api, median, now, prompt_body, salt, save, settle
from prompts import MESSAGES

SCRIPT = "ship"
EVERY = 0.5          # seconds between calls
SETTLE = 10.0        # seconds of calls after the first new answer
BEFORE = 2.0         # seconds of calls before the move
GIVE_UP = 90.0


async def probe(http: httpx.AsyncClient, rows: list, move: int, direction: str, clock: dict):
    message, status = MESSAGES[move % len(MESSAGES)]
    body = prompt_body(ANSWER, {"customer_message": message + salt(), "order_status": status}, max_tokens=16)
    sent = time.monotonic()
    try:
        r = await http.post("/chat/completions", json=body)
        version = int(r.headers["x-synthorai-prompt-version"]) if r.headers.get("x-synthorai-prompt-version") else None
        status_code, request_id = r.status_code, r.headers.get("x-request-id", "")
        usage = r.json().get("usage") or {}
    except (httpx.HTTPError, ValueError) as e:
        version, status_code, request_id, usage = None, 0, "", {}
        print(f"    a probe failed: {type(e).__name__}")
    answered = time.monotonic()
    rows.append({"time": now(), "script": SCRIPT, "case": f"move {move}: {direction}", "prompt_id": ANSWER,
                 "requested": "production", "served_version": version, "status": status_code,
                 "sent": sent, "answered": answered,
                 "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                 "byok": bool(usage.get("is_byok")),
                 "upstream_cost_usd": (usage.get("cost_details") or {}).get("upstream_inference_cost"),
                 "request_id": request_id, "cost_usd": None})


async def one_move(admin_http, http, rows, move: int, old: int, new: int, before: float) -> dict:
    direction = f"v{old} -> v{new}"
    clock = {"moved_at": time.monotonic() + before}  # a guess until the move's answer arrives
    tasks, first_new = [], None
    for _ in range(int(before / EVERY)):
        tasks.append(asyncio.create_task(probe(http, rows, move, direction, clock)))
        await asyncio.sleep(EVERY)
    await asyncio.to_thread(api, admin_http, "PUT", f"/prompts/{ANSWER}/labels/production", json={"version": new})
    clock["moved_at"] = moved_at = time.monotonic()
    while time.monotonic() - moved_at < GIVE_UP:
        tasks.append(asyncio.create_task(probe(http, rows, move, direction, clock)))
        await asyncio.sleep(EVERY)
        mine = [r for r in rows if r["case"] == f"move {move}: {direction}" and r["served_version"] == new]
        if mine and first_new is None:
            first_new = min(r["sent"] - moved_at for r in mine)
        if first_new is not None and time.monotonic() - moved_at > first_new + SETTLE:
            break
    await asyncio.gather(*tasks)
    mine = [r for r in rows if r["case"] == f"move {move}: {direction}"]
    for r in mine:
        r["sent_after_move_s"] = round(r.pop("sent") - moved_at, 3)
        r["answered_after_move_s"] = round(r.pop("answered") - moved_at, 3)
    new_rows = [r for r in mine if r["served_version"] == new]
    first = min(new_rows, key=lambda r: r["sent_after_move_s"]) if new_rows else None
    old_rows = [r for r in mine if r["served_version"] == old]
    last_old = max(old_rows, key=lambda r: r["sent_after_move_s"]) if old_rows else None
    back = [r for r in old_rows if first and r["sent_after_move_s"] > first["sent_after_move_s"]]
    result = {"move": move, "direction": direction, "calls": len(mine),
              "last_old_sent_s": last_old["sent_after_move_s"] if last_old else None,
              "first_new_sent_s": first["sent_after_move_s"] if first else None,
              "first_new_answered_s": first["answered_after_move_s"] if first else None,
              "old_after_first_new": len(back)}
    for r in mine:
        r["calls_before_move_s"] = before
    print(f"  move {move:2d} {direction}: last v{old} call sent at {result['last_old_sent_s']} s, first v{new} call "
          f"sent at {result['first_new_sent_s']} s (answered at {result['first_new_answered_s']} s); "
          f"{result['old_after_first_new']} v{old} answers after it ({len(mine)} calls)")
    return result


async def main_async(moves: int, before: float, pause: float):
    rows, results = [], []
    with admin() as admin_http:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=120,
                                     headers={"Authorization": f"Bearer {os.environ['SYNTHORAI_API_KEY']}"}) as http:
            labels = {i["label"]: i["version"] for i in api(admin_http, "GET", f"/prompts/{ANSWER}")["labels"]}
            if labels.get("production") != 1:
                api(admin_http, "PUT", f"/prompts/{ANSWER}/labels/production", json={"version": 1})
                await asyncio.sleep(15)
            print(f"production starts on v1; {moves} moves to v3 and {moves} back\n")
            n = 0
            for _ in range(moves):
                for old, new in ((1, 3), (3, 1)):
                    n += 1
                    results.append(await one_move(admin_http, http, rows, n, old, new, before))
                    await asyncio.sleep(pause)
    save(SCRIPT, settle(rows))
    for direction in ("v1 -> v3", "v3 -> v1"):
        mine = [r for r in results if r["direction"] == direction]
        sent = [r["first_new_sent_s"] for r in mine if r["first_new_sent_s"] is not None]
        last = [r["last_old_sent_s"] for r in mine if r["last_old_sent_s"] is not None]
        line = f"\n{direction}, {len(mine)} moves:"
        if sent:
            line += (f" the first call the new version answered was sent at median {median(sent)} s "
                     f"(range {min(sent)} to {max(sent)} s) after the move's answer;")
        if last:
            line += (f" the last call the old version answered was sent at median {median(last)} s "
                     f"(range {min(last)} to {max(last)} s);")
        print(line + f" {sum(r['old_after_first_new'] for r in mine)} old-version answers after a new one")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--moves", type=int, default=5)
    ap.add_argument("--before", type=float, default=BEFORE, help="seconds of calls before each move")
    ap.add_argument("--pause", type=float, default=5.0, help="seconds with no calls between moves")
    args = ap.parse_args()
    asyncio.run(main_async(args.moves, args.before, args.pause))


if __name__ == "__main__":
    main()
