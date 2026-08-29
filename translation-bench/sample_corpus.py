#!/usr/bin/env python3
"""Deterministically sample the WMT24++ evaluation subset used by this
benchmark, and write corpus/wmt24pp.json.

Selection is objective and reproducible:
  - exclude the `canary` domain (contamination marker, not eval content)
  - exclude rows with is_bad_source
  - length window on the English source: 40-160 words
  - seeded random sample of SEGMENTS_PER_DOMAIN segment_ids per domain,
    drawn once from the en-de_DE config (sources are shared across all
    language pairs), then the same segment_ids are pulled from every
    language pair so all languages translate identical content.

Dataset: google/wmt24pp (Apache-2.0), via the Hugging Face
datasets-server API. Re-run with --seed N to draw a different sample.
"""
import argparse, json, random, sys, time, urllib.request

LPS = ["en-zh_CN", "en-zh_TW", "en-ja_JP", "en-ko_KR", "en-fr_FR",
       "en-de_DE", "en-es_MX", "en-pt_BR", "en-it_IT"]
DOMAINS = ["news", "social", "speech", "literary"]
SEGMENTS_PER_DOMAIN = 8
LEN_LO, LEN_HI = 40, 160
BASE = "https://huggingface.co/datasets/google/wmt24pp/resolve/main"

def fetch_all(config):
    """One JSONL file per language pair in the dataset repo."""
    u = f"{BASE}/{config}.jsonl"
    for attempt in range(4):
        try:
            body = urllib.request.urlopen(u, timeout=120).read().decode()
            return [json.loads(l) for l in body.splitlines() if l.strip()]
        except Exception:
            if attempt == 3: raise
            time.sleep(3 * (attempt + 1))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260826)
    ap.add_argument("--out", default="corpus/wmt24pp.json")
    a = ap.parse_args()

    print("fetching en-de_DE for sampling ...", file=sys.stderr)
    base = fetch_all("en-de_DE")
    rng = random.Random(a.seed)
    chosen = {}
    for dom in DOMAINS:
        pool = [r for r in base
                if r["domain"] == dom and not r["is_bad_source"]
                and LEN_LO <= len(r["source"].split()) <= LEN_HI]
        if len(pool) < SEGMENTS_PER_DOMAIN:
            sys.exit(f"domain {dom}: pool {len(pool)} < {SEGMENTS_PER_DOMAIN}")
        picks = rng.sample(sorted(pool, key=lambda r: r["segment_id"]),
                           SEGMENTS_PER_DOMAIN)
        chosen[dom] = {r["segment_id"] for r in picks}
        print(f"{dom}: pool {len(pool)}, picked {sorted(chosen[dom])}", file=sys.stderr)

    wanted = set().union(*chosen.values())
    out = {}
    for lp in LPS:
        print(f"fetching {lp} ...", file=sys.stderr)
        rows = fetch_all(lp)
        for r in rows:
            if r["segment_id"] not in wanted: continue
            seg = out.setdefault(r["segment_id"], {
                "segment_id": r["segment_id"], "domain": r["domain"],
                "document_id": r["document_id"], "source": r["source"],
                "references": {}})
            seg["references"][lp.split("-")[1]] = r["target"]
    missing = [s for s, v in out.items() if len(v["references"]) != len(LPS)]
    if missing: sys.exit(f"segments missing references: {missing}")
    doc = {"dataset": "google/wmt24pp", "license": "Apache-2.0",
           "seed": a.seed, "segments_per_domain": SEGMENTS_PER_DOMAIN,
           "length_window_words": [LEN_LO, LEN_HI],
           "segments": sorted(out.values(), key=lambda s: (s["domain"], s["segment_id"]))}
    with open(a.out, "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"wrote {a.out}: {len(out)} segments x {len(LPS)} references")

if __name__ == "__main__":
    main()
