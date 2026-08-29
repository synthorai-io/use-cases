#!/usr/bin/env python3
"""Aggregate translations + verdicts into the study tables.

Consensus rule per (arm, candidate, lang, segment, judge): the two
presentation orders must agree; a hard W/L flip counts as a TIE (judge
position noise), W+TIE or L+TIE leans to the non-tie verdict. Judges are
aggregated per family AND pooled; the pooled score is the mean of the
three per-judge win rates (excluding ties), so no family outvotes another.
Placeholder integrity for UI strings is graded by script here, not by
judges."""
import json, pathlib, statistics, collections

HERE = pathlib.Path(__file__).parent
LANGS = ["zh_CN", "zh_TW", "ja_JP", "ko_KR", "fr_FR", "de_DE", "es_MX", "pt_BR", "it_IT"]

def consensus(rows):
    """rows -> {(arm,cand,lang,sid,judge): 'W'|'L'|'T'}"""
    by = collections.defaultdict(dict)
    for r in rows:
        k = (r["arm"], r["candidate"], r["lang"], str(r["segment_id"]), r["judge"])
        by[k][r["order"]] = "T" if r["tie"] else ("W" if r["cand_wins"] else "L")
    out = {}
    for k, v in by.items():
        if len(v) < 2: continue
        a, b = v.get("cand-first"), v.get("opp-first")
        if a == b: out[k] = a
        elif {a, b} == {"W", "L"}: out[k] = "T"
        else: out[k] = a if a != "T" else b
    return out

def winrate(cells):
    w = sum(1 for x in cells if x == "W"); t = sum(1 for x in cells if x == "T")
    n = len(cells)
    return (w / (n - t)) if n - t else None, w, t, n

def main():
    vs = [json.loads(l) for l in open(HERE / "results" / "verdicts.jsonl")]
    ts = [json.loads(l) for l in open(HERE / "results" / "translations.jsonl")]
    cons = consensus(vs)
    doms = {}
    for fn in ("wmt24pp.json", "techdoc.json", "ui_strings.json"):
        for s in json.load(open(HERE / "corpus" / fn))["segments"]:
            doms[str(s["segment_id"])] = s["domain"]

    print("=== MODEL ARM: win rate vs gpt-5.6-sol (excl ties), per judge and pooled")
    cands = sorted({k[1] for k in cons if k[0] == "model"})
    judges = ["claude-sonnet-5", "gpt-5.6-luna", "gemini-3.1-pro-preview"]  # pilot fable-judge rows stay in the file but out of the matrix
    for cand in cands:
        print(f"\n{cand}")
        for lang in LANGS:
            per = []
            for j in judges:
                cells = [v for k, v in cons.items()
                         if k[0] == "model" and k[1] == cand and k[2] == lang and k[4] == j]
                wr = winrate(cells)[0]
                if wr is not None: per.append(wr)
            if per:
                pool = statistics.mean(per)
                print(f"  {lang}: pooled {pool:.0%}  per-judge {[f'{x:.0%}' for x in per]}")

    print("\n=== MODEL ARM: per-domain pooled win rate (all langs)")
    for cand in cands:
        line = f"{cand:26s}"
        for dom in ("news", "social", "speech", "literary", "techdoc", "ui"):
            per = []
            for j in judges:
                cells = [v for k, v in cons.items()
                         if k[0] == "model" and k[1] == cand and k[4] == j
                         and doms.get(k[3]) == dom]
                wr = winrate(cells)[0]
                if wr is not None: per.append(wr)
            line += f" {dom}:{statistics.mean(per):.0%}" if per else f" {dom}:-"
        print(line)

    print("\n=== REFERENCE ARM: win rate vs human post-edit (excl ties), pooled")
    for cand in sorted({k[1] for k in cons if k[0] == "reference"}):
        line = f"{cand:26s}"
        for lang in LANGS:
            per = []
            for j in judges:
                cells = [v for k, v in cons.items()
                         if k[0] == "reference" and k[1] == cand and k[2] == lang and k[4] == j]
                wr = winrate(cells)[0]
                if wr is not None: per.append(wr)
            line += f" {lang}:{statistics.mean(per):.0%}" if per else ""
        print(line)

    print("\n=== PLACEHOLDER INTEGRITY (script-graded, UI strings with placeholders)")
    ph = {"ui-02": ["{seconds}"], "ui-03": ["%d"], "ui-08": ["{workspace_name}"]}
    models = sorted({r["model"] for r in ts})
    for m in models:
        bad = total = 0
        for r in ts:
            if r["model"] != m or str(r["segment_id"]) not in ph: continue
            total += 1
            if not all(p in r["text"] for p in ph[str(r["segment_id"])]): bad += 1
        print(f"{m:26s} broken {bad}/{total}")

    print("\n=== COST & BURN per model (all 52 segs x 9 langs)")
    for m in models:
        rs = [r for r in ts if r["model"] == m and "err" not in r]
        cost = sum(r.get("cost") or 0 for r in rs)
        rt = sum(r.get("rt") or 0 for r in rs)
        chars = sum(len(r["text"]) for r in rs)
        print(f"{m:26s} n={len(rs):4d} cost=${cost:7.3f} reasoning_tokens={rt:6d} "
              f"$/1M-output-chars={1e6 * cost / chars:7.2f}" if chars else m)

    print("\n=== JUDGE NOISE: order-consistency per judge")
    by = collections.defaultdict(dict)
    for r in vs:
        k = (r["arm"], r["candidate"], r["lang"], str(r["segment_id"]), r["judge"])
        by[k][r["order"]] = "T" if r["tie"] else ("W" if r["cand_wins"] else "L")
    for j in judges:
        pairs = [v for k, v in by.items() if k[4] == j and len(v) == 2]
        agree = sum(1 for v in pairs if v["cand-first"] == v["opp-first"])
        flip = sum(1 for v in pairs if {v["cand-first"], v["opp-first"]} == {"W", "L"})
        print(f"{j:26s} agree {agree}/{len(pairs)} hard-flips {flip} ({flip/len(pairs):.0%})")

if __name__ == "__main__":
    main()
