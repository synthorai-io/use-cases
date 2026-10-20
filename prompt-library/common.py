"""What every script of the demo shares: keys, the two APIs, one call by prompt id, and the results files.

Two keys, two jobs:

  SYNTHORAI_API_KEY           the inference key: model calls, by prompt id or not, and billing records.
  SYNTHORAI_PROMPTS_ADMIN_KEY an admin key with prompts.write and evals.write (and logs.read for
                              rollout.py): it saves versions, moves labels and records eval runs.
                              It cannot call a model.

Every model call becomes one row in results/<script>.jsonl, and every number the guides quote
is read from those files. A row is written only after its billing record is read, so a row's
cost is what the workspace was charged, not an estimate.
"""
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")

# The tracing headers come from the agent tracing demo, so these calls show up in the console
# as sessions and traces the same way.
sys.path.insert(0, str(HERE.parent / "agent-tracing"))
from tracing import Run, new_id  # noqa: E402

BASE_URL = os.environ.get("BASE_URL", "https://synthorai.io/v1")
API_URL = os.environ.get("API_URL", "https://synthorai.io/api/v1")
CONSOLE_URL = os.environ.get("CONSOLE_URL", "https://synthorai.io/console")
RESULTS = HERE / "results"

ANSWER = "support-answer"
CHECK = "policy-check"


def need(*keys: str):
    for key in keys:
        if not os.environ.get(key):
            sys.exit(f"{key} is not set. Put it in .env at the repo root; the README says what each key is for.")


def salt() -> str:
    """A short random tag added to every customer message, so no two calls are the same request."""
    return f" [ref {secrets.token_hex(3)}]"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# The management API ---------------------------------------------------------------------------

def admin() -> httpx.Client:
    need("SYNTHORAI_PROMPTS_ADMIN_KEY")
    return httpx.Client(base_url=API_URL, timeout=60,
                        headers={"Authorization": f"Bearer {os.environ['SYNTHORAI_PROMPTS_ADMIN_KEY']}"})


def api(client: httpx.Client, method: str, path: str, *, ok=(200, 201), idempotency: str | None = None, **kwargs):
    """One management call. A failure stops the script with the API's own code and message."""
    headers = {"Idempotency-Key": idempotency} if idempotency else {}
    for attempt in range(3):
        try:
            r = client.request(method, path, headers=headers, **kwargs)
            break
        except httpx.TransportError:
            # A dropped connection. Reads and label moves are safe to send again, and a write
            # that creates something carries an Idempotency-Key, so the retry cannot double it.
            if attempt == 2:
                raise
            time.sleep(1 + attempt)
    if r.status_code not in ok:
        error = (r.json().get("error") if r.headers.get("content-type", "").startswith("application/json") else None) or {}
        sys.exit(f"{method} {path}: {r.status_code} {error.get('code', '')}: {error.get('message', r.text[:300])}")
    return r.json()["data"] if r.status_code != 204 else None


# One model call ------------------------------------------------------------------------------

def inference(**kwargs) -> httpx.Client:
    need("SYNTHORAI_API_KEY")
    return httpx.Client(base_url=BASE_URL, timeout=300,
                        headers={"Authorization": f"Bearer {os.environ['SYNTHORAI_API_KEY']}"}, **kwargs)


def prompt_body(prompt_id: str, variables: dict, *, version: int | None = None, label: str | None = None,
                messages: list | None = None, **extra) -> dict:
    """A Chat Completions body that calls a library prompt. The model is chosen by the version."""
    body = {"model": "any", "messages": messages or [], "prompt_id": prompt_id, "prompt_variables": variables, **extra}
    if version is not None:
        body["prompt_version"] = version
    if label is not None:
        body["prompt_label"] = label
    return body


def call(http: httpx.Client, script: str, case: str, body: dict, *, run: Run | None = None, span: str = "answer",
         path: str = "/chat/completions", requested: str = "", headers: dict | None = None) -> dict:
    """Send one request and return its row. The cost is filled in later by settle()."""
    headers = {**(run.headers(span) if run else {}), **(headers or {})}
    t0 = time.perf_counter()
    try:
        r = http.post(path, json=body, headers=headers)
    except httpx.HTTPError as e:
        return {"time": now(), "script": script, "case": case, "status": 0, "error_code": type(e).__name__,
                "latency_ms": round((time.perf_counter() - t0) * 1000), "request_id": "", "cost_usd": None}
    latency = round((time.perf_counter() - t0) * 1000)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    usage = data.get("usage") or {}
    error = data.get("error") or {}
    row = {
        "time": now(), "script": script, "case": case, "endpoint": path,
        "prompt_id": body.get("prompt_id"), "requested": requested or describe(body),
        "served_version": int(r.headers["x-synthorai-prompt-version"]) if r.headers.get("x-synthorai-prompt-version") else None,
        "stale": r.headers.get("x-synthorai-prompt-stale") == "1",
        "model": data.get("model"), "status": r.status_code,
        "error_code": error.get("code") or (error.get("type") if error else None),
        "error_message": error.get("message"),
        "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "reasoning_tokens": (usage.get("completion_tokens_details") or usage.get("output_tokens_details")
                             or {}).get("reasoning_tokens"),
        "cached_tokens": ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
                          or usage.get("cache_read_input_tokens") or 0),
        "latency_ms": latency, "request_id": r.headers.get("x-request-id", ""),
        "session_id": run.session_id if run else None, "trace_id": run.trace_id if run else None,
        # A model on your own provider key (BYOK) is billed $0 by the gateway; what the provider
        # charged is reported separately in the response.
        "byok": bool(usage.get("is_byok")),
        "upstream_cost_usd": (usage.get("cost_details") or {}).get("upstream_inference_cost"),
        "cost_usd": None, "answer": answer_text(data),
        "tool_calls": ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls"),
    }
    return row


def describe(body: dict) -> str:
    if "prompt_version" in body:
        return f"version {body['prompt_version']}"
    if "prompt_label" in body:
        return f"label {body['prompt_label']}"
    return "production" if body.get("prompt_id") else "inline"


def answer_text(data: dict) -> str:
    """The reply text of any of the three endpoints."""
    if data.get("choices"):
        return (data["choices"][0].get("message") or {}).get("content") or ""
    if isinstance(data.get("content"), list):
        return "".join(part.get("text", "") for part in data["content"] if part.get("type") == "text")
    if data.get("output"):
        return "".join(c.get("text", "") for item in data["output"] for c in item.get("content", []) or []
                       if c.get("type") in ("output_text", "text"))
    return ""


def settle(rows: list) -> list:
    """Read the billing record of every row that has a request id. A record is written a moment after the call."""
    with httpx.Client(base_url=BASE_URL, timeout=30,
                      headers={"Authorization": f"Bearer {os.environ['SYNTHORAI_API_KEY']}"}) as http:
        for row in rows:
            if not row.get("request_id") or row.get("cost_usd") is not None:
                continue
            for _ in range(8):
                try:
                    r = http.get("/generation", params={"id": row["request_id"]})
                    if r.status_code == 200:
                        row["cost_usd"] = r.json()["data"]["total_cost"]
                        break
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    pass
                time.sleep(1.5)
    return rows


def save(script: str, rows: list):
    """Append the rows to results/<script>.jsonl and print what the demo has spent so far."""
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / f"{script}.jsonl").open("a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"\n{len(rows)} rows written to results/{script}.jsonl; spent so far by the demo: ${spent():.4f}")


def spent() -> float:
    total = 0.0
    for path in RESULTS.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            total += json.loads(line).get("cost_usd") or 0.0
    return total


def median(values: list) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


__all__ = ["ANSWER", "CHECK", "CONSOLE_URL", "Run", "admin", "api", "call", "inference", "median", "need",
           "new_id", "now", "prompt_body", "salt", "save", "settle", "spent"]
