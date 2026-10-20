"""Test v2 of support-answer against v1 before it ships, the way a CI job would.

    python evals.py                  three runs of orders and general (default)
    python evals.py --runs 1
    python evals.py --set orders     one test set only
    python evals.py --set fresh --runs 1
                                     the test fixed before the run (below)
    python evals.py --read RUN_ID    read back a run started elsewhere (the console's Run button)

Test sets, because a check applies to every row of its set:

  orders   40 rows, each naming its own order number, about delivery, returns, wrong or damaged
           items and billing. Checks: the answer contains an order number (a pattern), and a model
           judges that it addresses the customer's question.
  fresh-1  110 more rows like those, none of them in any other set, split at random into three
  fresh-2  sets of 40, 35 and 35 (a test set holds at most 50 rows). Same checks as orders.
  fresh-3
  general  20 rows without order numbers. Checks: the answer contains none of the phrases that
           promise a refund date or timeframe, arrives within 20 s, addresses the question
           (judged), and promises no refund date or timeframe (judged).

Through the API the caller runs the test: this script starts a run, calls v2 and v1 by version
for every row, grades both answers itself, and writes the results back to the run. With several
runs it says, per check, whether v1 and v2 differ by more than chance (Fisher's exact test on the
pass counts; a difference that is not significant is a direction, not a finding), and which rows
failed in how many runs.

The test fixed before the run, for results/: one run of each fresh set, v2 against v1, then a
single two-sided Fisher's exact test on the order-number check over all 110 rows pooled, alpha
0.05 (python evals.py --set fresh --runs 1). Pooling is sound because each row is a different
message and is called once per version. The earlier runs in results/ used the first 15 rows of
orders (three runs) and all 40 (three runs); they describe the difference and are not that test.
"""
import argparse
import json
import math
import random
import re
import time

from common import ANSWER, RESULTS, admin, api, call, inference, now, prompt_body, salt, save, settle

SCRIPT = "evals"
JUDGE_MODEL = "claude-sonnet-5-5"
LATENCY_MS = 20000
ORDER = re.compile(r"\bA-\d{5}\b")
REFUND_PHRASES = ["refund by", "business days"]
ANSWERS = "The answer addresses the customer's question."
# Every call of a test run, answers and judge alike, is labelled as an eval, so that per-version
# stats of production traffic can leave them out.
EVAL = {"X-Synthorai-Metadata": json.dumps({"source": "eval"}, separators=(",", ":"))}
NO_DATE = "Fails if the answer promises a specific refund date or timeframe."

ORDERS = [
    ("My order A-48213 arrived but the dock does not charge my laptop. Can I send it back?", "delivered on 2026-09-28"),
    ("Where is order A-50977? It has been a week.", "in transit, shipped on 2026-10-01"),
    ("Order A-51230 came with a cracked screen protector. What now?", "delivered on 2026-10-03"),
    ("Can you tell me when order A-49902 will be delivered?", "in transit, shipped on 2026-10-05"),
    ("I returned order A-47755 last week. Has it arrived?", "return received on 2026-10-06"),
    ("Please cancel order A-52018, I ordered the wrong colour.", "processing, not shipped"),
    ("The keyboard in order A-50311 is missing two keycaps.", "delivered on 2026-10-02"),
    ("Order A-48890 says delivered but nothing came.", "delivered on 2026-10-04"),
    ("Can I add a cable to order A-52240 before it ships?", "processing, not shipped"),
    ("Order A-49127 was charged twice on my card.", "delivered on 2026-09-21"),
    ("The monitor from order A-50455 has a dead pixel.", "delivered on 2026-09-30"),
    ("I want to exchange the headphones in order A-48002 for another colour.", "delivered on 2026-09-26"),
    ("Order A-51777 has been stuck in customs for days.", "in transit, shipped on 2026-09-29"),
    ("Can I get an invoice with my company name for order A-49540?", "delivered on 2026-09-18"),
    ("The charger in order A-50888 is for the wrong country.", "delivered on 2026-10-02"),
    ("When will order A-53104 get here? I need it for a trip on Saturday.", "in transit, shipped on 2026-10-06"),
    ("Order A-52677 still shows processing. When does it ship?", "processing, not shipped"),
    ("Is order A-53390 going to arrive before the weekend?", "in transit, shipped on 2026-10-07"),
    ("The tracking for order A-51902 has not moved in four days.", "in transit, shipped on 2026-10-02"),
    ("Can order A-53518 be delivered to my office instead of my home?", "processing, not shipped"),
    ("How do I return the speaker from order A-50126? It is too quiet.", "delivered on 2026-10-01"),
    ("I sent back order A-49318 two weeks ago and have heard nothing.", "return received on 2026-10-04"),
    ("Can I return the unopened mouse from order A-51045?", "delivered on 2026-09-27"),
    ("Order A-52833 was a gift. Can the person who got it return it?", "delivered on 2026-10-05"),
    ("Do I need the original box to return order A-48677?", "delivered on 2026-09-24"),
    ("I ordered a black webcam in A-50764 and got a white one.", "delivered on 2026-10-03"),
    ("Order A-51569 contained someone else's router.", "delivered on 2026-10-04"),
    ("The cable in order A-49983 is USB-A, but I ordered USB-C.", "delivered on 2026-09-29"),
    ("Order A-52391 came with a 24-inch monitor instead of the 27-inch one.", "delivered on 2026-10-06"),
    ("I only received one of the two lamps in order A-50219.", "delivered on 2026-10-02"),
    ("The box of order A-51128 was crushed and the tablet will not turn on.", "delivered on 2026-10-05"),
    ("Order A-49761 arrived with a dented speaker grille.", "delivered on 2026-09-30"),
    ("The power bank from order A-52906 gets very hot while charging.", "delivered on 2026-10-01"),
    ("My laptop stand from order A-50642 has a broken hinge.", "delivered on 2026-09-28"),
    ("Order A-53047 arrived wet and the earbuds case is stained.", "delivered on 2026-10-06"),
    ("I paid for express shipping on order A-51483 but it came by standard post.", "delivered on 2026-10-03"),
    ("Order A-50037 was charged at a higher price than the checkout page showed.", "delivered on 2026-09-25"),
    ("My discount code was not applied to order A-52564.", "processing, not shipped"),
    ("Why was VAT added twice on order A-49405?", "delivered on 2026-09-22"),
    ("I cancelled order A-52112 but my card was still charged.", "cancelled on 2026-10-04"),
]
# Rows that no earlier run used, for the test fixed before the run (see the module docstring).
# A test set holds at most 50 rows, so they are split at random into three sets of 40, 35 and 35.
FRESH = [
    ("Order A-53621 was due yesterday. Where is it?", "in transit, shipped on 2026-10-03"),
    ("Has order A-51314 left the warehouse yet?", "processing, not shipped"),
    ("What day will order A-52748 arrive?", "in transit, shipped on 2026-10-08"),
    ("The courier for order A-50583 says my address cannot be found.", "in transit, shipped on 2026-10-04"),
    ("Can you speed up order A-53266? It is a birthday present.", "processing, not shipped"),
    ("Order A-49856 shows delivered to a neighbour I do not know.", "delivered on 2026-10-06"),
    ("Why is order A-52017 coming in two parcels?", "in transit, shipped on 2026-10-05"),
    ("I am away next week. Can order A-53702 wait at the depot?", "in transit, shipped on 2026-10-07"),
    ("Order A-51690 has been out for delivery for three days.", "in transit, shipped on 2026-09-30"),
    ("Will order A-50349 need a signature on delivery?", "in transit, shipped on 2026-10-06"),
    ("Is there a tracking number for order A-52985?", "in transit, shipped on 2026-10-07"),
    ("The courier sent order A-48731 back to you. Can you send it again?", "returned to sender on 2026-10-05"),
    ("Order A-53855 is going to my old address. Can it be redirected?", "in transit, shipped on 2026-10-08"),
    ("Order A-51226 still has not shipped after ten days.", "processing, not shipped"),
    ("How long do I have to return order A-50972?", "delivered on 2026-09-20"),
    ("Can I return just the case from order A-52449 and keep the phone?", "delivered on 2026-10-01"),
    ("The return label for order A-49674 will not print.", "delivered on 2026-09-29"),
    ("Do I pay the postage to return order A-51837?", "delivered on 2026-10-02"),
    ("I dropped off the return for order A-50415 at the post office. What happens next?", "return in transit"),
    ("Can I return order A-53139 to a store?", "delivered on 2026-10-04"),
    ("The speaker from order A-48566 has been opened. Can I still send it back?", "delivered on 2026-09-27"),
    ("Order A-52703 came after I stopped needing it. Can I return it?", "delivered on 2026-10-07"),
    ("You refused my return of order A-49207. Why?", "return refused on 2026-10-03"),
    ("Can I swap the tablet in order A-51958 for the larger model instead of returning it?", "delivered on 2026-10-03"),
    ("I lost the receipt for order A-50861. Can I still return it?", "delivered on 2026-09-23"),
    ("Is there a restocking fee if I return order A-53484?", "delivered on 2026-10-05"),
    ("You have my return for order A-48419. When will it be checked?", "return received on 2026-10-08"),
    ("Can I return order A-52276 from abroad?", "delivered on 2026-09-30"),
    ("Order A-50528 has a UK plug, but I am in Germany.", "delivered on 2026-10-02"),
    ("I ordered a 2 TB drive in order A-53013 and got a 1 TB one.", "delivered on 2026-10-05"),
    ("The box for order A-49432 is right, but the mouse inside is a different model.", "delivered on 2026-09-28"),
    ("Order A-51741 came in silver; I chose space grey.", "delivered on 2026-10-04"),
    ("I got three cables in order A-52158 instead of one charger.", "delivered on 2026-10-06"),
    ("The manual in order A-50196 is only in Japanese.", "delivered on 2026-10-01"),
    ("I ordered a smartwatch in A-53577 and received a fitness band.", "delivered on 2026-10-07"),
    ("The soundbar from order A-48945 came without its remote.", "delivered on 2026-09-26"),
    ("Order A-52592 contained a refurbished unit, but I paid for a new one.", "delivered on 2026-10-03"),
    ("I received two left earbuds in order A-51073.", "delivered on 2026-10-02"),
    ("The printer in order A-49598 came without ink cartridges.", "delivered on 2026-09-30"),
    ("Order A-53218 has the wrong size of laptop sleeve.", "delivered on 2026-10-06"),
    ("Order A-50707 was meant to be a bundle, but only the console came.", "delivered on 2026-10-04"),
    ("The keyboard in order A-52464 has a French layout; I ordered US.", "delivered on 2026-10-05"),
    ("The TV from order A-51385 has a crack across the screen.", "delivered on 2026-10-06"),
    ("Something rattles inside the router from order A-49019.", "delivered on 2026-09-29"),
    ("The phone from order A-52821 will not hold a charge.", "delivered on 2026-10-03"),
    ("The headphones from order A-50274 have a torn ear cushion.", "delivered on 2026-10-01"),
    ("Order A-53691 arrived in a wet box and the camera lens is fogged.", "delivered on 2026-10-07"),
    ("The fan in order A-48808 has been very noisy from the first day.", "delivered on 2026-09-25"),
    ("The monitor arm in order A-51512 has a stripped screw.", "delivered on 2026-10-02"),
    ("The smart bulbs in order A-52037 flicker and then die.", "delivered on 2026-10-04"),
    ("Order A-49786 arrived with a scratched laptop lid.", "delivered on 2026-09-30"),
    ("The e-reader from order A-53352 has a line of dead pixels.", "delivered on 2026-10-06"),
    ("The kettle from order A-50093 leaks from the base.", "delivered on 2026-09-27"),
    ("The speaker in order A-51861 crackles at any volume.", "delivered on 2026-10-03"),
    ("The drone in order A-52680 arrived with a broken propeller arm.", "delivered on 2026-10-05"),
    ("The charging case from order A-48352 will not close.", "delivered on 2026-09-24"),
    ("I was charged for order A-51179 but the site says the payment failed.", "processing, not shipped"),
    ("Order A-53925 was charged in dollars, not euros.", "delivered on 2026-10-04"),
    ("Can I change the card I pay order A-50652 with?", "processing, not shipped"),
    ("My gift card was not used on order A-52339.", "delivered on 2026-10-02"),
    ("I need a VAT invoice for order A-49163.", "delivered on 2026-09-21"),
    ("Order A-51027 was charged for delivery although it was over the free-delivery amount.", "delivered on 2026-10-01"),
    ("The price of order A-53474 dropped the day after I bought it. Can you match it?", "delivered on 2026-10-06"),
    ("Order A-50918 shows two pending charges in my bank app.", "processing, not shipped"),
    ("Why did I pay customs fees on order A-52905?", "delivered on 2026-10-05"),
    ("The instalment plan was not applied to order A-48194.", "delivered on 2026-09-26"),
    ("I was billed for a warranty I did not add on order A-51634.", "delivered on 2026-10-03"),
    ("Order A-53760 was charged to an old card I had removed.", "in transit, shipped on 2026-10-07"),
    ("Can the receipt for order A-50437 go to a different email address?", "delivered on 2026-09-29"),
    ("My company was charged sales tax on order A-52122, but we are exempt.", "delivered on 2026-10-02"),
    ("Order A-54012 was meant to arrive on Monday. Is it late?", "in transit, shipped on 2026-10-06"),
    ("Can someone else sign for order A-47318 if I am out?", "in transit, shipped on 2026-10-07"),
    ("Order A-54377 says it is held at customs. What do I do?", "in transit, shipped on 2026-10-02"),
    ("Which courier is bringing order A-47561?", "in transit, shipped on 2026-10-08"),
    ("Order A-54629 was left outside in the rain. Is that normal?", "delivered on 2026-10-07"),
    ("Can I collect order A-47802 from a parcel locker?", "processing, not shipped"),
    ("Order A-54183 has been processing for a week. Is something wrong?", "processing, not shipped"),
    ("The tracking for order A-47436 shows a different city.", "in transit, shipped on 2026-10-05"),
    ("Can I return order A-54850 if I only opened the box?", "delivered on 2026-10-03"),
    ("Where do I send the return for order A-47195?", "delivered on 2026-09-30"),
    ("My return for order A-54266 has been in transit for two weeks.", "return in transit"),
    ("Can I return one of the three items in order A-47689?", "delivered on 2026-10-04"),
    ("Is a free return label included with order A-54531?", "delivered on 2026-10-06"),
    ("Order A-47944 was a duplicate purchase. Can I return one?", "delivered on 2026-10-02"),
    ("Can I return order A-54718 after 30 days if it is unused?", "delivered on 2026-09-12"),
    ("How do I pack the TV from order A-47253 for a return?", "delivered on 2026-10-01"),
    ("Order A-54095 came with a blue phone case; I ordered black.", "delivered on 2026-10-05"),
    ("I received someone else's invoice inside order A-47507.", 'delivered on 2026-10-03'),
    ("The charger in order A-54402 is 20 W, I ordered 65 W.", "delivered on 2026-10-06"),
    ("Order A-47766 is missing the stand for the monitor.", "delivered on 2026-10-02"),
    ("I got a tablet in order A-54977 but ordered an e-reader.", "delivered on 2026-10-07"),
    ("Order A-47129 has the right camera but the wrong lens.", "delivered on 2026-09-29"),
    ("The router in order A-54348 is an older version than the one listed.", "delivered on 2026-10-04"),
    ("Order A-47873 came with an EU plug, I am in the US.", "delivered on 2026-10-01"),
    ("The screen of the laptop in order A-54561 flickers.", "delivered on 2026-10-05"),
    ("Order A-47380 arrived with a cracked phone case.", "delivered on 2026-10-03"),
    ("The battery of the speaker from order A-54734 is swollen.", "delivered on 2026-10-06"),
    ("Order A-47615 headphones crackle in the left ear.", "delivered on 2026-10-02"),
    ("The webcam from order A-54286 shows a black image.", "delivered on 2026-10-07"),
    ("Order A-47058 arrived with the printer tray snapped off.", "delivered on 2026-09-28"),
    ("The smartwatch strap from order A-54819 broke on the first day.", "delivered on 2026-10-04"),
    ("The fridge camera in order A-47927 does not connect to Wi-Fi.", "delivered on 2026-10-01"),
    ("I was charged twice for order A-54143.", "delivered on 2026-10-05"),
    ("Order A-47482 used my business card instead of my personal one.", "delivered on 2026-10-03"),
    ("Can I get a copy of the invoice for order A-54690?", "delivered on 2026-09-27"),
    ("The voucher on order A-47331 was only applied to one item.", "delivered on 2026-10-02"),
    ("Order A-54457 was charged although I cancelled it within the hour.", "cancelled on 2026-10-06"),
    ("Why does order A-47708 show a currency conversion fee?", "delivered on 2026-10-04"),
    ("The total for order A-54925 is higher than the basket showed.", "processing, not shipped"),
    ("My student discount was not applied to order A-47279.", "delivered on 2026-10-01"),
]
_shuffled = random.Random(110).sample(FRESH, len(FRESH))
FRESH_SETS = [_shuffled[:40], _shuffled[40:75], _shuffled[75:]]
GENERAL = [
    ("How long does a refund take once you have the return?", "no order given"),
    ("Do you ship to Norway?", "no order given"),
    ("My headphones stopped working after two months. Is that covered?", "delivered on 2026-08-02"),
    ("Can I change the delivery address of my order?", "processing, not shipped"),
    ("The parcel says delivered but I never got it.", "delivered on 2026-10-04"),
    ("I was charged twice for the same purchase.", "delivered on 2026-09-21"),
    ("Do you price match other shops?", "no order given"),
    ("Can I return an opened keyboard?", "delivered on 2026-09-25"),
    ("What is your warranty on laptops?", "no order given"),
    ("My discount code did not apply at checkout.", "processing, not shipped"),
    ("Is it possible to exchange the monitor for a bigger one?", "delivered on 2026-09-29"),
    ("If it does not arrive by Friday, can I cancel and get my money back?", "in transit, shipped on 2026-10-01"),
    ("When will I see the refund on my card?", "return received on 2026-10-06"),
    ("Can I pick up my order in a store instead?", "processing, not shipped"),
    ("I sent the laptop back. Can you refund me today?", "return received on 2026-10-07"),
    ("Do you offer gift wrapping?", "no order given"),
    ("My webcam arrived with a scratched lens.", "delivered on 2026-09-30"),
    ("Can I pay in instalments?", "no order given"),
    ("The speaker I returned was refunded only in part. Why?", "refunded in part on 2026-10-05"),
    ("How do I return something I got as a gift?", "no order given"),
]
SETS = {
    "orders": {"name": "support-answer: orders (40 rows)", "rows": ORDERS, "checks": [
        {"type": "regex", "value": ORDER.pattern},
        {"type": "judge", "value": ANSWERS, "model": JUDGE_MODEL}]},
    **{f"fresh-{i}": {"name": f"support-answer: fresh orders {i} ({len(rows)} rows)", "rows": rows, "checks": [
        {"type": "regex", "value": ORDER.pattern},
        {"type": "judge", "value": ANSWERS, "model": JUDGE_MODEL}]} for i, rows in enumerate(FRESH_SETS, 1)},
    "general": {"name": "support-answer: general (20 rows)", "rows": GENERAL, "checks": [
        *({"type": "not_contains", "value": phrase} for phrase in REFUND_PHRASES),
        {"type": "max_latency_ms", "value": str(LATENCY_MS)},
        {"type": "judge", "value": ANSWERS, "model": JUDGE_MODEL},
        {"type": "judge", "value": NO_DATE, "model": JUDGE_MODEL}]},
}


def judge(http, criterion: str, row: dict, answer: str, rows: list) -> bool:
    """One model call that answers PASS or FAIL. It is billed and logged like any other call."""
    body = {"model": JUDGE_MODEL, "max_tokens": 10, "messages": [
        {"role": "system", "content": "You grade a support reply against one criterion. Reply with exactly PASS or FAIL."},
        {"role": "user", "content": f"Criterion: {criterion}\n\nCustomer message: {row['customer_message']}\n\n"
                                    f"Answer: {answer}"}]}
    result = call(http, SCRIPT, "judge", body, requested=JUDGE_MODEL, headers=EVAL)
    rows.append(result)
    return result["answer"].strip().upper().startswith("PASS")


def grade(http, which: str, row: dict, result: dict, rows: list) -> dict:
    """Every check of the set, in its order, with a readable name."""
    answer = result["answer"] or ""
    if which == "orders" or which.startswith("fresh"):
        return {"order number": bool(ORDER.search(answer)), "answers": judge(http, ANSWERS, row, answer, rows)}
    graded = {f'not "{phrase}"': phrase not in answer.lower() for phrase in REFUND_PHRASES}
    graded["latency"] = result["status"] == 200 and result["latency_ms"] <= LATENCY_MS
    graded["answers"] = judge(http, ANSWERS, row, answer, rows)
    graded["no refund date"] = judge(http, NO_DATE, row, answer, rows)
    return graded


def fisher(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher's exact test for [[a, b], [c, d]]."""
    n, row1, col1 = a + b + c + d, a + b, a + c

    def p(x):
        return math.comb(col1, x) * math.comb(n - col1, row1 - x) / math.comb(n, row1)

    observed = p(a)
    return min(1.0, sum(p(x) for x in range(max(0, row1 + col1 - n), min(row1, col1) + 1) if p(x) <= observed + 1e-12))


def find_or_create_set(http, which: str) -> int:
    spec = SETS[which]
    for item in api(http, "GET", f"/prompts/{ANSWER}/test-sets"):
        if item["name"] == spec["name"]:
            return item["id"]
    content = {"rows": [{"variables": {"product": "home electronics", "customer_message": m, "order_status": s}}
                        for m, s in spec["rows"]], "checks": spec["checks"]}
    # The key names the size too: a set that grows is a new set, not a replay of the old one.
    return api(http, "POST", f"/prompts/{ANSWER}/test-sets", idempotency=f"prompt-library-set-{which}-{len(spec['rows'])}",
               json={"name": spec["name"], "content": content})["id"]


def one_run(admin_http, http, which: str, set_id: int, number: int, rows: list) -> dict:
    run = api(admin_http, "POST", f"/prompts/{ANSWER}/eval-runs", json={"set_id": set_id, "candidate": 2, "baseline": 1})
    spec, results, t0 = SETS[which], [], time.monotonic()
    for i, (message, status) in enumerate(spec["rows"]):
        variables = {"product": "home electronics", "customer_message": message + salt(), "order_status": status}
        entry = {"row": i + 1}
        for side, version in (("candidate", 2), ("baseline", 1)):
            result = call(http, SCRIPT, f"{which} run {number}: v{version}", prompt_body(ANSWER, variables, version=version),
                          headers=EVAL)
            rows.append(result)
            entry[side] = {"version": version, "answer": result["answer"], "latency_ms": result["latency_ms"],
                           "request_id": result["request_id"], "checks": grade(http, which, variables, result, rows)}
        results.append(entry)
        done = len(results)
        api(admin_http, "PUT", f"/prompts/{ANSWER}/eval-runs/{run['id']}",
            json={"status": "running" if done < len(spec["rows"]) else "finished", "done": done,
                  "passed": sum(all(e["candidate"]["checks"].values()) for e in results), "checked": done,
                  "results": {"rows": results}})
    print(f"  {which} run {number} (eval run {run['id']}): {len(spec['rows'])} rows in {time.monotonic() - t0:.0f} s")
    return {"set": which, "id": run["id"], "results": results}


def summarise(runs: list, rows: list):
    cost = {r["request_id"]: r["cost_usd"] or 0 for r in rows}
    for run in runs:
        print(f"\n{run['set']}, eval run {run['id']}")
        for name in run["results"][0]["candidate"]["checks"]:
            counts = {}
            for side in ("candidate", "baseline"):
                graded = [e[side]["checks"][name] for e in run["results"]]
                counts[side] = (sum(graded), len(graded))
            (a, n_a), (c, n_c) = counts["candidate"], counts["baseline"]
            print(f"  {name:22s} v2 {a:2d}/{n_a}  v1 {c:2d}/{n_c}  Fisher p = {fisher(a, n_a - a, c, n_c - c):.3f}")
        answers = sum(cost.get(e[s]["request_id"], 0) for e in run["results"] for s in ("candidate", "baseline"))
        print(f"  answers ${answers:.4f}")
    print(f"\njudge calls, all runs: ${sum(r['cost_usd'] or 0 for r in rows if r['case'] == 'judge'):.4f}")
    repeats(runs)
    pooled(runs)


def pooled(runs: list):
    """The test fixed before the run: the order-number check over every fresh row, once each."""
    fresh = [run for run in runs if run["set"].startswith("fresh")]
    if sorted(run["set"] for run in fresh) != [w for w in SETS if w.startswith("fresh")]:
        return
    entries = [(run["set"], e) for run in fresh for e in run["results"]]
    a = sum(e["candidate"]["checks"]["order number"] for _, e in entries)
    c = sum(e["baseline"]["checks"]["order number"] for _, e in entries)
    n = len(entries)
    p = fisher(a, n - a, c, n - c)
    print(f"\nfresh sets pooled, {n} rows, each once: order number v2 {a}/{n}  v1 {c}/{n}  "
          f"two-sided Fisher p = {p:.2g} ({'below' if p < 0.05 else 'not below'} alpha 0.05)")
    for side, version in (("candidate", 2), ("baseline", 1)):
        missed = [f"{which} row {e['row']}" for which, e in entries if not e[side]["checks"]["order number"]]
        print(f"  v{version} missed: {', '.join(missed) or 'none'}")


def repeats(runs: list):
    """Per check and version, the rows that failed, by how many runs they failed in.

    A difference made of the same few rows in every run is about those rows; one spread over
    different rows each run is a rate."""
    by_set = {}
    for run in runs:
        by_set.setdefault(run["set"], []).append(run)
    for which, group in by_set.items():
        if len(group) < 2:
            continue
        print(f"\n{which}, failing rows over {len(group)} runs")
        for name in group[0]["results"][0]["candidate"]["checks"]:
            for side, version in (("candidate", 2), ("baseline", 1)):
                failed = {}
                for run in group:
                    for e in run["results"]:
                        if not e[side]["checks"][name]:
                            failed[e["row"]] = failed.get(e["row"], 0) + 1
                spread = [f"in {k} of {len(group)}: rows {sorted(r for r, n in failed.items() if n == k)}"
                          for k in range(len(group), 0, -1) if k in failed.values()]
                print(f"  {name:22s} v{version}  {'; '.join(spread) or 'none'}")


def read_back(run_id: int):
    """A run started elsewhere, as the API holds it: status, counts and the results it was given."""
    with admin() as http:
        run = api(http, "GET", f"/prompts/{ANSWER}/eval-runs/{run_id}")
    print(json.dumps({k: run.get(k) for k in ("id", "set_name", "candidate", "baseline", "status", "rows", "done",
                                              "passed", "checked")}, indent=1))
    (RESULTS / f"eval-run-{run_id}.json").write_text(json.dumps(run, indent=1))
    print(f"saved to results/eval-run-{run_id}.json")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--read", type=int, metavar="RUN_ID")
    ap.add_argument("--set", choices=[*SETS, "fresh"], help="one test set only, or fresh for the three fresh sets")
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    if args.read:
        return read_back(args.read)
    rows, runs = [], []
    with admin() as admin_http, inference() as http:
        chosen = ([w for w in SETS if w.startswith("fresh")] if args.set == "fresh" else [args.set] if args.set
                  else ["orders", "general"])
        for which in chosen:
            set_id = find_or_create_set(admin_http, which)
            print(f"{which}: test set {set_id}, v2 against v1, judge {JUDGE_MODEL}")
            for number in range(1, args.runs + 1):
                runs.append(one_run(admin_http, http, which, set_id, number, rows))
    settle(rows)
    summarise(runs, rows)
    save(SCRIPT, rows)
    # Earlier runs stay in the file: each run is added, none replaced.
    path = RESULTS / "evals-runs.json"
    earlier = json.loads(path.read_text())["runs"] if path.exists() else []
    path.write_text(json.dumps({"written": now(), "runs": earlier + runs}, indent=1))


if __name__ == "__main__":
    main()
