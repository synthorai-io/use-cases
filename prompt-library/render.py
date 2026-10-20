"""Show the request a library prompt turns into, without calling a model.

    python render.py support-answer 3
    python render.py policy-check

POST /v1/prompts/{id}/render returns what a call would send and the version it resolved to. It is
not billed and does not appear in Logs.
"""
import argparse
import json

from common import inference


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt_id")
    ap.add_argument("version", nargs="?", type=int)
    ap.add_argument("--format", default="chat", choices=["chat", "responses", "messages"])
    args = ap.parse_args()
    body = {"format": args.format, "prompt_variables": {
        "customer_message": "Where is order A-50977? It has been a week.", "order_status": "in transit",
        "tone": "friendly"}}
    if args.version:
        body["prompt_version"] = args.version
    with inference() as http:
        r = http.post(f"/prompts/{args.prompt_id}/render", json=body)
        print(r.status_code)
        print(json.dumps(r.json(), indent=2))


if __name__ == "__main__":
    main()
