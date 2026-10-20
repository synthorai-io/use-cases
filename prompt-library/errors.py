"""What a call by prompt id answers when it cannot be served, and what that costs.

    python errors.py

Five cases, each sent once on /v1/chat/completions: a variable with no value and no default,
a version and a label both sent, a prompt id that does not exist, a version that does not
exist, and a label that points nowhere. The docs say these are answered before any model is
called; the billing record, or its absence, says whether anything was charged.
"""
from common import ANSWER, call, inference, prompt_body, salt, save, settle
from prompts import MESSAGES

SCRIPT = "errors"


def main():
    message, status = MESSAGES[0]
    values = {"customer_message": message + salt(), "order_status": status}
    cases = [
        ("v4 called without its tone variable", prompt_body(ANSWER, values, version=4)),
        ("a version and a label both sent", prompt_body(ANSWER, values, version=1, label="staging")),
        ("a prompt id that does not exist", prompt_body("support-answer-missing", values)),
        ("a version that does not exist", prompt_body(ANSWER, values, version=99)),
        ("a label that points nowhere", prompt_body(ANSWER, values, label="development")),
    ]
    rows = []
    with inference() as http:
        for case, body in cases:
            rows.append(call(http, SCRIPT, case, body))
    settle(rows)
    for row in rows:
        billed = "no billing record" if row["cost_usd"] is None else f"billed ${row['cost_usd']:.6f}"
        print(f"{row['case']}\n  {row['status']} {row['error_code']}: {row['error_message']}\n  {billed}\n")
    save(SCRIPT, rows)


if __name__ == "__main__":
    main()
