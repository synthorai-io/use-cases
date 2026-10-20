"""Replace an OpenAI stored prompt with a library prompt under the same pmpt_ id, and call it unchanged.

    python migrate.py

An application that calls OpenAI's Responses API with a stored prompt sends
prompt={"id": "pmpt_...", "version": "2"}. Recreate the prompt in the library under that id and
point the client's base URL at the gateway: the same request is served from the library, the
version picks the library version, and without one the call gets production.

The id below is made up for the demo. Use your own pmpt_ id when you migrate. Each call is one
row in results/migrate.jsonl.
"""
import os
import secrets
import time

from openai import OpenAI

from common import BASE_URL, admin, api, need, salt, save, settle
from prompts import ANSWER_SYSTEM

SCRIPT = "migrate"
RUN = secrets.token_hex(8)                           # idempotency keys are new on every run, as in setup.py

PROMPT_ID = "pmpt_68f2a1c09d4e4b7a8c3f5e21d0b97a64"
VERSIONS = [
    {"note": "v1: as it was in OpenAI", "body": {
        "system": ANSWER_SYSTEM, "model": "gpt-6-luna", "params": {"max_tokens": "1500"},
        "defaults": {"product": "home electronics"}}},
    {"note": "v2: the same, one sentence shorter", "body": {
        "system": ANSWER_SYSTEM.replace(" plainly and specifically", ""), "model": "gpt-6-luna",
        "params": {"max_tokens": "1500"}, "defaults": {"product": "home electronics"}}},
]


def ensure_prompt():
    with admin() as http:
        if PROMPT_ID not in {p["id"] for p in api(http, "GET", "/prompts")}:
            api(http, "POST", "/prompts", idempotency=f"migrate-{RUN}-{PROMPT_ID}",
                json={"id": PROMPT_ID, "name": "Support answer (from OpenAI)", "description": "Recreated stored prompt."})
        saved = len(api(http, "GET", f"/prompts/{PROMPT_ID}").get("versions") or [])
        for number, version in enumerate(VERSIONS, 1):
            if number > saved:
                api(http, "POST", f"/prompts/{PROMPT_ID}/versions", idempotency=f"migrate-{RUN}-{PROMPT_ID}-v{number}",
                    json=version)
        labels = {item["label"]: item["version"] for item in api(http, "GET", f"/prompts/{PROMPT_ID}")["labels"]}
        if labels.get("production") != 1:
            api(http, "PUT", f"/prompts/{PROMPT_ID}/labels/production", json={"version": 1})


def main():
    need("SYNTHORAI_API_KEY")
    ensure_prompt()
    # The application's code, unchanged except for the base URL and the key.
    client = OpenAI(base_url=BASE_URL, api_key=os.environ["SYNTHORAI_API_KEY"], max_retries=0)
    rows = []
    for prompt in ({"id": PROMPT_ID, "version": "2"}, {"id": PROMPT_ID}):
        t0 = time.perf_counter()
        raw = client.responses.with_raw_response.create(
            model="gpt-6-luna", prompt=prompt, input="Where is order A-50977? It has been a week." + salt())
        latency = round((time.perf_counter() - t0) * 1000)
        reply = raw.parse()
        served = raw.headers.get("x-synthorai-prompt-version")
        rows.append({
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "script": SCRIPT,
            "case": f"responses prompt={prompt}", "endpoint": "/v1/responses", "prompt_id": PROMPT_ID,
            "requested": f"version {prompt['version']}" if "version" in prompt else "production",
            "served_version": int(served) if served else None, "model": reply.model,
            "status": raw.http_response.status_code, "error_code": None,
            "prompt_tokens": reply.usage.input_tokens, "completion_tokens": reply.usage.output_tokens,
            "cached_tokens": 0, "latency_ms": latency, "request_id": raw.headers.get("x-request-id", ""),
            "cost_usd": None, "answer": reply.output_text})
        print(f"prompt={prompt}\n  served version {served}: {' '.join(reply.output_text.split())[:160]}")
    save(SCRIPT, settle(rows))


if __name__ == "__main__":
    main()
