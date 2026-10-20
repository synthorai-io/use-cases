"""Create the two library prompts, save their versions and point the labels. Safe to run again.

    python setup.py

A prompt that already exists is read first, and only the versions it does not have yet are
saved, so a second run changes nothing. Every write also carries an Idempotency-Key, so a retry
after a dropped connection cannot save a version twice. Labels are moved only when they point
somewhere else.

The keys are new on every run. A key the API has seen before is answered from its record of the
first request (with Idempotency-Replayed: true) and nothing is written, so a prompt deleted in
the console and created again with the same key would never come back.
"""
import hashlib
import json
import secrets

from common import CONSOLE_URL, admin, api
from prompts import PROMPTS


RUN = secrets.token_hex(8)


def key(*parts) -> str:
    """The same write in the same run always sends the same Idempotency-Key."""
    return f"prompt-library-demo-{RUN}-" + hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]


def main():
    with admin() as http:
        existing = {p["id"]: p for p in api(http, "GET", "/prompts")}
        for prompt in PROMPTS:
            pid = prompt["id"]
            if pid not in existing:
                api(http, "POST", "/prompts", idempotency=key("create", pid),
                    json={"id": pid, "name": prompt["name"], "description": prompt["description"]})
                print(f"{pid}: created")
            have = api(http, "GET", f"/prompts/{pid}")
            saved = len(have.get("versions") or [])
            for number, version in enumerate(prompt["versions"], 1):
                if number <= saved:
                    continue
                made = api(http, "POST", f"/prompts/{pid}/versions", idempotency=key("version", pid, number, version),
                           json=version)
                print(f"{pid}: saved version {made['version']} ({version['note']})")
            labels = {item["label"]: item["version"] for item in api(http, "GET", f"/prompts/{pid}")["labels"]}
            for label, target in prompt["labels"].items():
                if labels.get(label) != target:
                    api(http, "PUT", f"/prompts/{pid}/labels/{label}", json={"version": target})
                    print(f"{pid}: {label} moved to v{target}")
            final = api(http, "GET", f"/prompts/{pid}")
            versions = ", ".join(f"v{v['version']}" for v in final["versions"])
            labels = ", ".join(f"{item['label']} -> v{item['version']}" for item in final["labels"] if item["version"])
            print(f"{pid}: versions {versions}; labels {labels}\n  {CONSOLE_URL}/prompts?p={pid}")


if __name__ == "__main__":
    main()
