# translation-bench

A reproducible benchmark of LLM translation quality and cost: 9 models,
9 languages, 52 source segments in 6 domains, 4,210 generated
translations, and 8,455 blind pairwise verdicts from a 3-family LLM
judge panel. Full write-up:
[Best LLM for Translation](https://synthorai.io/blog/llm-translation-quality/).

Everything needed to verify or re-run the study is in this directory:
the corpus with its sampling scripts, the runners, the raw results, and
the aggregation code.

## Layout

```text
corpus/
  wmt24pp.json      32 segments x 9 human references, seeded-sampled
                    from google/wmt24pp (Apache-2.0)
  techdoc.json      10 fresh technical-doc paragraphs (CC BY 4.0)
  ui_strings.json   10 synthetic UI strings, one localization hazard each
  judge_subset.json the 16 segment ids used for pairwise judging
sample_corpus.py    deterministic WMT24++ sampling (re-run with --seed N)
run_translate.py    generates candidate translations, incremental
run_judge.py        blind pairwise judging, incremental
analyze.py          aggregation: win rates, domains, cost, judge noise
run_all.sh          full-study driver
results/
  translations.jsonl  every generated translation with usage and cost
  verdicts.jsonl      every raw verdict with dimension, severity, reason
  summary.txt         analyze.py output snapshot
```

## Method in one paragraph

Each candidate model translates every segment into all nine languages.
Every candidate translation is compared blind against the same segment
translated by a fixed baseline (`gpt-5.6-sol`), by three judge families
(`claude-sonnet-5`, `gpt-5.6-luna`, `gemini-3.1-pro-preview`) under a
priority-ordered rubric derived from the
[MQM error typology](https://themqm.org/error-types-2/typology/)
(accuracy > terminology > fluency > style > locale conventions >
markup), in the spirit of WMT's
[Error Span Annotation](https://aclanthology.org/2024.wmt-1.131/)
protocol adapted to pairwise LLM judging. A second arm compares the
frontier models against the human post-edited references of
[WMT24++](https://arxiv.org/abs/2502.12404).

Bias guards: judges never see model names; every pair is judged in both
presentation orders and a hard win/loss contradiction counts as a tie;
the three judge families are averaged with equal weight and also
reported separately; placeholder integrity is graded by script, not by
judges; corpus sampling is seeded with the chosen segment ids published.

## Reproduce

Any OpenAI-compatible endpoint works. Claude judging additionally uses
the Anthropic-native `/v1/messages` path (forced tool call), because
`response_format` is ignored for Claude models on compat surfaces.

```bash
export API_KEY=...                        # your key
export BASE_URL=https://synthorai.io/v1   # or any OpenAI-compatible endpoint

python3 sample_corpus.py                  # rebuild corpus/wmt24pp.json (same seed = same file)
bash run_all.sh                           # full study; resumable, skips completed cells
python3 analyze.py                        # tables to stdout
```

The full study makes roughly 4,200 translation calls and 8,500 short
judge calls. Budget about $60-90 against production APIs. Both runners
are incremental: rerunning skips every completed cell, so interruptions
are cheap.

## Reading the raw data

- `results/verdicts.jsonl`: one row per (arm, candidate, language,
  segment, judge, order) with the winner, decisive rubric dimension,
  severity, and a one-sentence reason.
- Rows with `"judge": "claude-fable-5"` come from the pilot round; they
  remain in the file for completeness but `analyze.py` restricts the
  matrix to the three official judges.
- `results/translations.jsonl` contains 2 rows with an `err` field
  (transport failures); `analyze.py` skips them.
- `gemini-3.1-pro-preview` is both a judge and a reference-arm
  candidate; per-judge numbers are reported precisely so that cells
  like this stay auditable.
- The `techdoc` segments were selected from blog posts published within
  60 days of the study (objective filters: 60-150 words, at least one
  inline-code span or number, no tables or code blocks, max 2 segments
  per post, seed 20260826). Each segment carries its source post slug;
  the originals are public at `https://synthorai.io/blog/<post>/`.

## Licenses

- Code: repository license (see the repo root).
- `corpus/wmt24pp.json`: derived from
  [google/wmt24pp](https://huggingface.co/datasets/google/wmt24pp),
  Apache License 2.0; see NOTICE.
- `corpus/techdoc.json`, `corpus/ui_strings.json`: CC BY 4.0,
  attribution "Synthorai".
- `results/*.jsonl` contain model outputs generated for this study and
  are published for verification.
