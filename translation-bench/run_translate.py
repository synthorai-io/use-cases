#!/usr/bin/env python3
"""Generate candidate translations for every (model, language, segment).

Self-contained: any OpenAI-compatible endpoint works.
  BASE_URL   (default https://synthorai.io/v1)
  API_KEY    required
Usage:
  API_KEY=... python3 run_translate.py --models gpt-5.6-sol,qwen3.8-max --langs zh_CN,de_DE
Appends one JSON line per call to results/translations.jsonl and skips
(model, lang, segment) cells already present, so reruns are incremental.
"""
import argparse, json, os, pathlib, random, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).parent
BASE = os.environ.get("BASE_URL", "https://synthorai.io/v1")
KEY = os.environ.get("API_KEY") or os.environ.get("SYNTHORAI_API_KEY")
OUT = HERE / "results" / "translations.jsonl"

LANG_NAME = {"zh_CN": "Simplified Chinese", "zh_TW": "Traditional Chinese (Taiwan)",
             "ja_JP": "Japanese", "ko_KR": "Korean", "fr_FR": "French",
             "de_DE": "German", "es_MX": "Spanish (Latin America)",
             "pt_BR": "Brazilian Portuguese", "it_IT": "Italian"}

# Language-neutral core of a production translation prompt: faithful,
# native register, keep inline code/placeholders/technical terms intact.
PROMPT = ("You are a professional translator. Translate the text between the "
          "<text> tags from English into {lang}. Preserve meaning exactly; do "
          "not add or drop information. Keep inline code spans, placeholders "
          "like {{name}} or %d, product names, and technical terms a native "
          "engineer would keep in English. Write natural, native prose, not "
          "literal calque. Output ONLY the translation, nothing else.\n"
          "<text>\n{text}\n</text>")

def load_corpus():
    segs = []
    for fn in ("wmt24pp.json", "techdoc.json", "ui_strings.json"):
        doc = json.load(open(HERE / "corpus" / fn))
        for s in doc["segments"]:
            segs.append({"segment_id": str(s["segment_id"]), "domain": s["domain"],
                         "text": s.get("source") or s["text"]})
    return segs

def call(model, lang, seg, effort_off):
    body = {"model": model, "max_tokens": 4096,
            "messages": [{"role": "user", "content":
                PROMPT.format(lang=LANG_NAME[lang], text=seg["text"])
                + f"\n<!-- ref {random.random()} -->"}]}
    if effort_off: body.update(effort_off)
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    t0 = time.time()
    d = json.load(urllib.request.urlopen(req, timeout=300))
    u = d.get("usage", {})
    return {"text": d["choices"][0]["message"]["content"].strip(),
            "pt": u.get("prompt_tokens"), "ct": u.get("completion_tokens"),
            "rt": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "cost": u.get("cost"), "secs": round(time.time() - t0, 1)}

# Per-model thinking-off spelling (translation should not think); models
# that reject the off-switch run at default and the burn is reported.
EFFORT_OFF = {
    "gpt-5.6-sol": {"reasoning_effort": "none"},
    "qwen3.8-max": {"reasoning_effort": "none"},
    "glm-5.2": {"reasoning_effort": "none"},
    "kimi-k3": {"reasoning_effort": "none"},
    "deepseek-v4-flash-0731": {"thinking": {"type": "disabled"}},
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--langs", default=",".join(LANG_NAME))
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    if not KEY: sys.exit("API_KEY (or SYNTHORAI_API_KEY) is required")
    segs = load_corpus()
    OUT.parent.mkdir(exist_ok=True)
    done = set()
    if OUT.exists():
        for l in open(OUT):
            r = json.loads(l); done.add((r["model"], r["lang"], str(r["segment_id"])))
    jobs = [(m, l, s) for m in a.models.split(",") for l in a.langs.split(",")
            for s in segs if (m, l, s["segment_id"]) not in done]
    print(f"{len(jobs)} cells to run ({len(done)} cached)", file=sys.stderr)
    lock = __import__("threading").Lock()
    def work(j):
        m, l, s = j
        row = {"model": m, "lang": l, "segment_id": s["segment_id"], "domain": s["domain"]}
        try:
            row.update(call(m, l, s, EFFORT_OFF.get(m)))
        except Exception as e:
            row["err"] = str(e)[:200]
        with lock:
            with open(OUT, "a") as f: f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f'{m:24s} {l} {str(s["segment_id"]):10s} {"ERR" if "err" in row else "ok"}', file=sys.stderr)
    with ThreadPoolExecutor(a.workers) as ex:
        list(ex.map(work, jobs))
    print("done", file=sys.stderr)

if __name__ == "__main__":
    main()
