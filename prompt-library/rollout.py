"""Send a share of production's calls to another version, and check how the gateway splits them.

    python rollout.py                    10%, then 50%
    python rollout.py --percents 10      one share only
    python rollout.py --percents 50 --cases "body user"
                                         some kinds of call only
    python rollout.py --logs START END   the per-version figures from Logs for an earlier run

A call's bucket is the first of these it carries: its session id, the _user label, the body's
user, the body's safety_identifier, Anthropic's metadata.user_id; with none of them, the request
itself. At each share, production stays on v1 and v3 gets the share:

  sessions    200 sessions of 3 turns, each with its own X-Session-Id. A session should get one
              version for all its turns.
  end user    100 calls from 50 end users (_user in X-Synthorai-Metadata) and no session: each
              end user should get one version.
  body user   100 calls from 50 body users (the body's user field only) and no session. Each
              user should get one version. (Before a gateway fix in October 2026 the field was not
              read for this, and about half the users got both versions at 50%.)
  safety id   40 calls from 20 users that send only the body's safety_identifier, and no
              session. Each user should get one version.
  no session  100 calls with neither a session nor an end user: split call by call.
  pinned      20 calls that pin prompt_version=1, which a rollout never touches.

The rollout is stopped at the end, and the per-version calls, tokens (reasoning tokens among
them) and cost are read back from GET /api/v1/requests to compare with what the calls themselves
recorded.
"""
import argparse
import asyncio
import math
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from common import ANSWER, API_URL, BASE_URL, Run, admin, api, median, new_id, now, prompt_body, salt, save, settle
from prompts import MESSAGES

SCRIPT = "rollout"
SESSIONS, TURNS, LOOSE, PINNED, END_USER = 200, 3, 100, 20, 100
CASES = ["sessions", "no session", "pinned v1", "end user", "body user", "safety id"]
SAFETY_USERS = 20
PARALLEL = 10


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a share of k in n."""
    if not n:
        return 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


async def one(http, rows, case: str, n: int, *, session: str | None = None, user: str | None = None,
              body_user: str | None = None, safety_id: str | None = None, version: int | None = None,
              turn: int = 1):
    message, status = MESSAGES[n % len(MESSAGES)]
    labels = {"feature": "prompt-library", "case": f"rollout {case.split(':')[0]}"}
    if user:
        labels["_user"] = user
    run = Run(session or "", labels=labels)
    headers = run.headers(f"turn-{turn}")
    if not session:
        headers.pop("X-Session-Id")
    body = prompt_body(ANSWER, {"customer_message": message + salt(), "order_status": status}, version=version)
    if body_user:
        body["user"] = body_user
    if safety_id:
        body["safety_identifier"] = safety_id
    t0 = time.perf_counter()
    try:
        r = await http.post("/chat/completions", json=body, headers=headers)
        usage = r.json().get("usage") or {}
        served = int(r.headers["x-synthorai-prompt-version"]) if r.headers.get("x-synthorai-prompt-version") else None
        status_code, request_id, model = r.status_code, r.headers.get("x-request-id", ""), r.json().get("model")
    except httpx.HTTPError as e:
        usage, served, status_code, request_id, model = {}, None, 0, "", type(e).__name__
    rows.append({"time": now(), "script": SCRIPT, "case": case, "prompt_id": ANSWER,
                 "requested": f"version {version}" if version else "production", "served_version": served,
                 "model": model, "status": status_code, "session_id": session, "user": user or body_user or safety_id,
                 "turn": turn,
                 "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                 "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
                 "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0,
                 "byok": bool(usage.get("is_byok")),
                 "upstream_cost_usd": (usage.get("cost_details") or {}).get("upstream_inference_cost"),
                 "latency_ms": round((time.perf_counter() - t0) * 1000), "request_id": request_id, "cost_usd": None})


async def session(http, rows, gate, case: str, n: int):
    async with gate:
        sid = new_id("rollout")
        for turn in range(1, TURNS + 1):
            await one(http, rows, case, n + turn, session=sid, turn=turn)


async def loose(http, rows, gate, case: str, n: int, **kwargs):
    async with gate:
        await one(http, rows, case, n, **kwargs)


def split(rows, tag: str, case: str, label: str):
    """The share of one kind of call on v3, with its interval."""
    mine = [r for r in rows if r["case"] == f"{tag}: {case}" and r["status"] == 200]
    k = sum(r["served_version"] == 3 for r in mine)
    low, high = wilson(k, len(mine))
    print(f"  {label}: {k} of {len(mine)} calls on v3 = {k / max(1, len(mine)):.1%} (95% interval {low:.1%} to {high:.1%})")
    return mine


def per_user(rows, tag: str, case: str, label: str):
    """Users (or sessions) on v3, and how many kept one version over all their calls."""
    mine = [r for r in rows if r["case"] == f"{tag}: {case}" and r["status"] == 200]
    users = {}
    for r in mine:
        users.setdefault(r["session_id"] if case == "sessions" else r["user"], set()).add(r["served_version"])
    on_v3 = sum(1 for v in users.values() if v == {3})
    low, high = wilson(on_v3, len(users))
    print(f"  {label}: {on_v3} of {len(users)} only on v3 = {on_v3 / max(1, len(users)):.1%} "
          f"(95% interval {low:.1%} to {high:.1%}); {sum(1 for v in users.values() if len(v) == 1)} kept one "
          f"version, {sum(1 for v in users.values() if len(v) > 1)} got both")
    return mine


def report(rows, percent: int, cases: list):
    tag = f"{percent}%"
    print(f"\n{tag} of production to v3")
    kept = []
    if "sessions" in cases:
        kept += per_user(rows, tag, "sessions", f"sessions of {TURNS} turns")
    if "end user" in cases:
        per_user(rows, tag, "end user", "end users (_user label), 2 calls each, no session")
    if "body user" in cases:
        kept += per_user(rows, tag, "body user", "body users (user field only), 2 calls each, no session")
    if "safety id" in cases:
        kept += per_user(rows, tag, "safety id", "safety_identifier only, 2 calls each, no session")
    if "no session" in cases:
        kept += split(rows, tag, "no session", "no session, no user")
    if "pinned v1" in cases:
        pinned = [r for r in rows if r["case"] == f"{tag}: pinned v1" and r["status"] == 200]
        print(f"  pinned prompt_version=1: {sum(r['served_version'] == 3 for r in pinned)} of {len(pinned)} on v3")
    for version in (1, 3):
        mine = [r for r in kept if r["served_version"] == version]
        print(f"  v{version}: {len(mine)} calls, median {median([r['latency_ms'] for r in mine])} ms, "
              f"median {median([r['completion_tokens'] for r in mine])} output tokens")


def from_logs(start: str, end: str):
    """What Logs holds per version for the same time range."""
    key = os.environ.get("SYNTHORAI_ADMIN_KEY") or os.environ.get("SYNTHORAI_PROMPTS_ADMIN_KEY")
    print("\nper version, from GET /api/v1/requests")
    for version in (1, 3):
        rows, cursor = [], None
        while True:
            params = {"start": start, "end": end, "prompt_id": ANSWER, "prompt_version": version, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = httpx.get(f"{API_URL}/requests", params=params, headers={"Authorization": f"Bearer {key}"}, timeout=60)
            if r.status_code != 200:
                print(f"  v{version}: {r.status_code} {r.text[:200]}")
                break
            page = r.json()
            rows += page["data"]
            cursor = page["meta"].get("next_cursor")
            if not page["meta"].get("has_more"):
                break
        if rows:
            n = len(rows)
            output = [r["usage"]["output_tokens"] for r in rows]
            reasoning = [r["usage"].get("reasoning_tokens") or 0 for r in rows]
            print(f"  v{version}: {n} requests, ${sum(r['cost_usd'] for r in rows):.4f} (${sum(r['cost_usd'] for r in rows) / n:.6f} "
                  f"a call), {sum(r['usage']['input_tokens'] for r in rows):,} input and {sum(output):,} output tokens, "
                  f"median {median([r['duration_ms'] for r in rows])} ms")
            print(f"       per call: median {median(output)} output tokens, of them median {median(reasoning)} reasoning "
                  f"and {median([o - z for o, z in zip(output, reasoning)])} in the answer; "
                  f"on average {sum(output) / n:.0f} output and {sum(reasoning) / n:.0f} reasoning a call, "
                  f"{sum(reasoning) / max(1, sum(output)):.0%} of all output tokens")


async def main_async(percents: list, cases: list):
    rows = []
    start = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with admin() as admin_http:
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=300,
                                     headers={"Authorization": f"Bearer {os.environ['SYNTHORAI_API_KEY']}"}) as http:
            gate = asyncio.Semaphore(PARALLEL)
            for percent in percents:
                api(admin_http, "PUT", f"/prompts/{ANSWER}/labels/production/rollout", json={"version": 3, "percent": percent})
                print(f"production: v1, v3 at {percent}%; waiting 15 s for the change to reach calls")
                await asyncio.sleep(15)
                tag = f"{percent}%"
                jobs = []
                if "sessions" in cases:
                    jobs += [session(http, rows, gate, f"{tag}: sessions", n * TURNS) for n in range(SESSIONS)]
                if "no session" in cases:
                    jobs += [loose(http, rows, gate, f"{tag}: no session", n) for n in range(LOOSE)]
                if "pinned v1" in cases:
                    jobs += [loose(http, rows, gate, f"{tag}: pinned v1", n, version=1) for n in range(PINNED)]
                if "end user" in cases:
                    jobs += [loose(http, rows, gate, f"{tag}: end user", n, user=f"u_{2000 + n // 2}")
                             for n in range(END_USER)]
                if "body user" in cases:
                    jobs += [loose(http, rows, gate, f"{tag}: body user", n, body_user=f"b_{3000 + n // 2}")
                             for n in range(END_USER)]
                if "safety id" in cases:
                    jobs += [loose(http, rows, gate, f"{tag}: safety id", n, safety_id=f"s_{4000 + n // 2}")
                             for n in range(2 * SAFETY_USERS)]
                await asyncio.gather(*jobs)
                report(rows, percent, cases)
            api(admin_http, "DELETE", f"/prompts/{ANSWER}/labels/production/rollout")
            print("\nrollout stopped: production is v1 only")
    save(SCRIPT, settle(rows))
    time.sleep(20)                                   # let the last calls reach Logs
    from_logs(start, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--percents", type=int, nargs="+", default=[10, 50])
    ap.add_argument("--cases", nargs="+", choices=CASES, default=CASES)
    ap.add_argument("--logs", nargs=2, metavar=("START", "END"), help="UTC times, e.g. 2026-10-19T15:49:00Z")
    args = ap.parse_args()
    if args.logs:
        return from_logs(*args.logs)
    asyncio.run(main_async(args.percents, args.cases))


if __name__ == "__main__":
    main()
