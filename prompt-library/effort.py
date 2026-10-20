"""What sending reasoning_effort from the caller does to each version of support-answer.

    python effort.py              50 calls in each of the four cells (default)
    python effort.py --calls 10
    python effort.py --judge      grade the saved answers: does each address the customer's question?

v1 and v3 store no reasoning_effort, so a model reasons as much as it likes unless the caller
sends the setting with the call. (Since a gateway fix in October 2026 a version can store it too,
on Chat Completions and Responses; the results here were measured before that, with the setting
sent by the caller.) This script calls v1 (gpt-6-luna) and v3 (deepseek-v4.1-flash)
by version, each with the model's own default and with reasoning_effort "none", on the messages
of the 40-row orders test set. Every answer is scored with the orders set's order-number check, so
a setting that saves tokens but breaks a version shows up as a lower pass rate.

The four cells are interleaved in a shuffled order, so a slow minute does not land on one cell.
Per cell it prints cost per call, output and reasoning tokens, median latency and the
order-number pass rate, and per version a Fisher's exact test of "none" against the default.

The pattern says nothing about whether the answer is any good, so --judge sends every saved
answer to the same judge and criterion as evals.py ("addresses the customer's question") and
reports that per cell too. The judge calls are rows of results/effort.jsonl as well.
"""
import argparse
import json
import random
from concurrent.futures import ThreadPoolExecutor

from common import ANSWER, RESULTS, call, inference, median, prompt_body, salt, save, settle
from evals import ANSWERS, EVAL, JUDGE_MODEL, ORDER, ORDERS, fisher
from rollout import wilson

SCRIPT = "effort"
PARALLEL = 5
CELLS = [(1, None), (1, "none"), (3, None), (3, "none")]
ROWS = ORDERS[:40]                                   # the 40-row orders set


def name(version: int, effort: str | None) -> str:
    return f"v{version} {effort or 'default'}"


def one(http, version: int, effort: str | None, n: int) -> dict:
    message, status = ROWS[n % len(ROWS)]
    variables = {"product": "home electronics", "customer_message": message + salt(), "order_status": status}
    extra = {"reasoning_effort": effort} if effort else {}
    row = call(http, SCRIPT, name(version, effort), prompt_body(ANSWER, variables, version=version, **extra),
               requested=f"version {version}")
    row["order_number"] = bool(ORDER.search(row.get("answer") or ""))
    row["row"] = n % len(ROWS) + 1
    return row


def mean(values: list) -> float:
    return sum(values) / len(values) if values else 0.0


def report(rows: list):
    print(f"{'cell':12s} {'calls':>5s} {'$/call':>9s} {'output mean (median)':>21s} "
          f"{'reasoning mean (median)':>24s} {'median ms':>9s}  order number")
    passed = {}
    for version, effort in CELLS:
        mine = [r for r in rows if r["case"] == name(version, effort)]
        ok = [r for r in mine if r["status"] == 200]
        output = [r["completion_tokens"] or 0 for r in ok]
        reasoning = [r["reasoning_tokens"] or 0 for r in ok]
        k = sum(r["order_number"] for r in ok)
        low, high = wilson(k, len(ok))
        passed[(version, effort)] = (k, len(ok))
        print(f"{name(version, effort):12s} {len(ok):2d}/{len(mine):<2d} {mean([r['cost_usd'] or 0 for r in ok]):9.6f} "
              f"{mean(output):11.0f} ({median(output)}) {mean(reasoning):14.0f} ({median(reasoning)}) "
              f"{median([r['latency_ms'] for r in ok])!s:>9s}  {k}/{len(ok)} ({low:.0%} to {high:.0%})")
    for version in (1, 3):
        (a, n_a), (c, n_c) = passed[(version, "none")], passed[(version, None)]
        print(f"v{version}: order number with none {a}/{n_a} against default {c}/{n_c}, "
              f"Fisher p = {fisher(a, n_a - a, c, n_c - c):.3f}")
        misses = sorted({r["row"] for r in rows if r["case"] == name(version, "none") and r["status"] == 200
                         and not r["order_number"]})
        if misses:
            print(f"    rows missed with none: {misses}")


def judge_saved():
    """Grade every saved answer that has no grade yet, then report the grades per cell."""
    path = RESULTS / f"{SCRIPT}.jsonl"
    saved = [json.loads(line) for line in path.open()]
    judged = {r["judged_request_id"] for r in saved if r.get("judged_request_id")}
    todo = [r for r in saved if r["case"] in {name(*c) for c in CELLS} and r["status"] == 200
            and r["request_id"] not in judged]

    def grade(row):
        body = {"model": JUDGE_MODEL, "max_tokens": 10, "messages": [
            {"role": "system", "content": "You grade a support reply against one criterion. Reply with exactly PASS or FAIL."},
            {"role": "user", "content": f"Criterion: {ANSWERS}\n\nCustomer message: {ROWS[row['row'] - 1][0]}\n\n"
                                        f"Answer: {row['answer']}"}]}
        result = call(http, SCRIPT, f"judge: {row['case']}", body, requested=JUDGE_MODEL, headers=EVAL)
        result["judged_request_id"] = row["request_id"]
        result["passed"] = (result.get("answer") or "").strip().upper().startswith("PASS")
        return result

    with inference() as http, ThreadPoolExecutor(PARALLEL) as pool:
        grades = list(pool.map(grade, todo))
    if grades:
        save(SCRIPT, settle(grades))
    grades = [r for r in saved + grades if r.get("judged_request_id")]
    print(f"\n{ANSWERS!r}, judged by {JUDGE_MODEL}")
    counts = {}
    for version, effort in CELLS:
        mine = [r for r in grades if r["case"] == f"judge: {name(version, effort)}" and r["status"] == 200]
        k = sum(r["passed"] for r in mine)
        counts[(version, effort)] = (k, len(mine))
        low, high = wilson(k, len(mine))
        print(f"  {name(version, effort):12s} {k}/{len(mine)} ({low:.0%} to {high:.0%})")
    for version in (1, 3):
        (a, n_a), (c, n_c) = counts[(version, "none")], counts[(version, None)]
        print(f"  v{version}: none {a}/{n_a} against default {c}/{n_c}, Fisher p = {fisher(a, n_a - a, c, n_c - c):.3f}")
    print(f"  judge calls: ${sum(r['cost_usd'] or 0 for r in grades):.4f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calls", type=int, default=50, help="calls per cell")
    ap.add_argument("--judge", action="store_true", help="grade the saved answers instead of making new ones")
    args = ap.parse_args()
    if args.judge:
        return judge_saved()
    jobs = [(version, effort, n) for version, effort in CELLS for n in range(args.calls)]
    random.Random(7).shuffle(jobs)
    with inference() as http, ThreadPoolExecutor(PARALLEL) as pool:
        rows = list(pool.map(lambda job: one(http, *job), jobs))
    save(SCRIPT, settle(rows))
    report(rows)


if __name__ == "__main__":
    main()
