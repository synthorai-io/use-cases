#!/usr/bin/env python3
"""Blind pairwise judging with an MQM-derived rubric.

Arms:
  --arm model      candidate vs the fixed baseline model's translation
  --arm reference  candidate vs the human post-edited reference (WMT24++ only)

Bias guards: every pair is judged twice, once in each presentation
order, and a hard win/loss contradiction between the two orders is
counted as a tie by analyze.py; three judge families run independently
and are reported separately. Judges
never see model names. Verdicts append to results/verdicts.jsonl
incrementally (reruns skip done cells).

  API_KEY / BASE_URL as in run_translate.py.
Usage:
  API_KEY=... python3 run_judge.py --arm model --candidates qwen3.8-max --langs zh_CN
"""
import argparse, json, os, pathlib, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).parent
BASE = os.environ.get("BASE_URL", "https://synthorai.io/v1")
KEY = os.environ.get("API_KEY") or os.environ.get("SYNTHORAI_API_KEY")
OUT = HERE / "results" / "verdicts.jsonl"
BASELINE = "gpt-5.6-sol"
JUDGES = ["claude-sonnet-5", "gpt-5.6-luna", "gemini-3.1-pro-preview"]

LANG_NAME = {"zh_CN": "Simplified Chinese", "zh_TW": "Traditional Chinese (Taiwan)",
             "ja_JP": "Japanese", "ko_KR": "Korean", "fr_FR": "French",
             "de_DE": "German", "es_MX": "Spanish (Latin America)",
             "pt_BR": "Brazilian Portuguese", "it_IT": "Italian"}

RUBRIC = ("You are a professional translation evaluator. Judge two candidate "
          "translations of the same English source into {lang}.\n"
          "Evaluate in this priority order (an error higher on the list "
          "outweighs any number of issues below it):\n"
          "1. Accuracy: mistranslation, omission, addition, hallucination.\n"
          "2. Terminology: domain terms handled as a native speaker of the "
          "field would (technical terms like API or token stay in English; "
          "no clumsy calques).\n"
          "3. Fluency: grammar and natural reading for a native speaker.\n"
          "4. Style: register appropriate to the text type, no added flourish.\n"
          "5. Locale conventions: numbers, dates, units, punctuation width.\n"
          "6. Markup: inline code, placeholders, links preserved exactly.\n\n"
          "SOURCE:\n{src}\n\nTRANSLATION A:\n{a}\n\nTRANSLATION B:\n{b}\n\n"
          "Pick the better translation overall, or TIE if genuinely equal.")

SCHEMA = {"type": "object", "properties": {
    "winner": {"type": "string", "enum": ["A", "B", "TIE"]},
    "decisive_dimension": {"type": "integer", "minimum": 0, "maximum": 6},
    "severity": {"type": "string", "enum": ["major", "minor", "none"]},
    "reason": {"type": "string"}},
    "required": ["winner", "decisive_dimension", "severity", "reason"],
    "additionalProperties": False}

def judge_call(judge, lang, src, a, b):
    prompt = RUBRIC.format(lang=LANG_NAME[lang], src=src, a=a, b=b)
    if judge.startswith("claude"):
        # Claude ignores response_format on the compat surface; use the
        # native forced tool call (measured fully constrained).
        body = {"model": judge, "max_tokens": 8192,
                "tools": [{"name": "verdict", "description": "Record the verdict.",
                           "input_schema": SCHEMA}],
                "tool_choice": {"type": "tool", "name": "verdict"},
                "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(f"{BASE}/messages", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}",
                     "anthropic-version": "2023-06-01"})
        d = json.load(urllib.request.urlopen(req, timeout=300))
        return next(bl["input"] for bl in d["content"] if bl["type"] == "tool_use")
    body = {"model": judge, "max_tokens": 8192,
            "response_format": {"type": "json_schema", "json_schema":
                {"name": "verdict", "strict": True, "schema": SCHEMA}},
            "messages": [{"role": "user", "content": prompt}]}
    if judge.startswith("gpt"): body["reasoning_effort"] = "low"
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    d = json.load(urllib.request.urlopen(req, timeout=300))
    txt = d["choices"][0]["message"]["content"]
    return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))

def load_translations():
    t = {}
    for l in open(HERE / "results" / "translations.jsonl"):
        r = json.loads(l)
        if "err" not in r: t[(r["model"], r["lang"], str(r["segment_id"]))] = r["text"]
    return t

def load_sources():
    src, ref = {}, {}
    for fn in ("wmt24pp.json", "techdoc.json", "ui_strings.json"):
        doc = json.load(open(HERE / "corpus" / fn))
        for s in doc["segments"]:
            sid = str(s["segment_id"])
            src[sid] = s.get("source") or s["text"]
            for lang, r in (s.get("references") or {}).items(): ref[(sid, lang)] = r
    return src, ref

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["model", "reference"], required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--langs", required=True)
    ap.add_argument("--judges", default=",".join(JUDGES))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--subset", action="store_true",
                    help="restrict to corpus/judge_subset.json segment ids")
    a = ap.parse_args()
    if not KEY: sys.exit("API_KEY (or SYNTHORAI_API_KEY) is required")
    trans = load_translations(); src, ref = load_sources()
    subset = set(json.load(open(HERE / "corpus" / "judge_subset.json"))["segment_ids"]) if a.subset else None
    done = set()
    if OUT.exists():
        for l in open(OUT):
            r = json.loads(l)
            done.add((r["arm"], r["candidate"], r["lang"], r["segment_id"], r["judge"], r["order"]))
    jobs = []
    for cand in a.candidates.split(","):
        for lang in a.langs.split(","):
            for (m, lg, sid), text in sorted(trans.items()):
                if m != cand or lg != lang: continue
                if subset is not None and sid not in subset: continue
                if a.arm == "model":
                    if cand == BASELINE: continue
                    opp = trans.get((BASELINE, lang, sid))
                else:
                    opp = ref.get((sid, lang))
                if not opp: continue
                for judge in a.judges.split(","):
                    for order in ("cand-first", "opp-first"):
                        k = (a.arm, cand, lang, sid, judge, order)
                        if k not in done: jobs.append((k, src[sid], text, opp))
    print(f"{len(jobs)} verdicts to collect ({len(done)} cached)", file=sys.stderr)
    lock = __import__("threading").Lock()
    def work(j):
        (arm, cand, lang, sid, judge, order), source, cand_t, opp_t = j
        A, B = (cand_t, opp_t) if order == "cand-first" else (opp_t, cand_t)
        row = dict(arm=arm, candidate=cand, lang=lang, segment_id=sid,
                   judge=judge, order=order)
        try:
            try:
                v = judge_call(judge, lang, source, A, B)
            except Exception:
                time.sleep(2)
                v = judge_call(judge, lang, source, A, B)
            w = v.get("winner")
            row.update(winner_raw=w, dim=v.get("decisive_dimension"),
                       severity=v.get("severity"), reason=str(v.get("reason"))[:200],
                       cand_wins=(w == "A") == (order == "cand-first") and w != "TIE",
                       tie=(w == "TIE"))
        except Exception as e:
            row["err"] = str(e)[:200]
        with lock:
            with open(OUT, "a") as f: f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f'{cand:14s} {lang} {sid:>6s} {judge:22s} {order:10s} '
              f'{"ERR" if "err" in row else ("W" if row.get("cand_wins") else ("T" if row.get("tie") else "L"))}',
              file=sys.stderr)
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, jobs))
    print("done", file=sys.stderr)

if __name__ == "__main__":
    main()
