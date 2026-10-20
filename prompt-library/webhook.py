"""Verify a prompt-change webhook before you act on it.

    python webhook.py            check verify() against a delivery signed here

The gateway POSTs to each address you add on the Prompts page when a prompt is created, gets a
version, has a label moved or a rollout changed, or is deleted. It signs the request:

    X-Synthorai-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>" with your secret>

verify() is what a receiver runs on every delivery, against the raw body as received (parse the
JSON only after it passes). The self-test signs a sample delivery locally, because receiving a
real one needs an address the gateway can reach.
"""
import hashlib
import hmac
import json
import time


def verify(secret: str, signature_header: str, raw_body: bytes, tolerance_s: int = 300, now: float | None = None) -> bool:
    """True when the signature matches and is recent. Compare in constant time; refuse old ones."""
    try:
        parts = dict(item.split("=", 1) for item in signature_header.split(","))
        stamp, given = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now or time.time()) - stamp) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def sign(secret: str, raw_body: bytes, stamp: int) -> str:
    return f"t={stamp},v1=" + hmac.new(secret.encode(), f"{stamp}.".encode() + raw_body, hashlib.sha256).hexdigest()


def main():
    secret = "whsec_demo_only"
    body = json.dumps({"type": "prompt.label_moved", "created_at": "2026-10-05T03:12:44Z", "prompt": "support-answer",
                       "kind": "prompt", "label": "production", "from": 1, "version": 3, "by": 42}).encode()
    stamp = int(time.time())
    header = sign(secret, body, stamp)
    cases = [
        ("a delivery as sent", verify(secret, header, body), True),
        ("the body changed by one byte", verify(secret, header, body.replace(b'"version": 3', b'"version": 4')), False),
        ("the wrong secret", verify("whsec_other", header, body), False),
        ("a delivery an hour old", verify(secret, sign(secret, body, stamp - 3600), body), False),
        ("a header with no signature", verify(secret, f"t={stamp}", body), False),
    ]
    for name, got, want in cases:
        print(f"  {'ok ' if got == want else 'BAD'} {name}: verify() = {got}")
    if any(got != want for _, got, want in cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
