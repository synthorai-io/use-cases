#!/bin/bash
# Full-study driver. Incremental at every stage; safe to relaunch.
set -u
cd "$(dirname "$0")"
MODELS=gpt-5.6-sol,claude-fable-5,gemini-3.1-pro-preview,claude-sonnet-5,gemini-3.7-flash,deepseek-v4-flash-0731,qwen3.8-max,glm-5.2,kimi-k3
CANDS=claude-fable-5,gemini-3.1-pro-preview,claude-sonnet-5,gemini-3.7-flash,deepseek-v4-flash-0731,qwen3.8-max,glm-5.2,kimi-k3
LANGS=zh_CN,zh_TW,ja_JP,ko_KR,fr_FR,de_DE,es_MX,pt_BR,it_IT
echo "=== phase translate"
python3 run_translate.py --models "$MODELS" --langs "$LANGS" --workers 6 || exit 1
echo "=== phase judge model-arm"
python3 run_judge.py --arm model --candidates "$CANDS" --langs "$LANGS" --subset --workers 6 || exit 1
echo "=== phase judge reference-arm (SOTA tier + baseline)"
python3 run_judge.py --arm reference --candidates gpt-5.6-sol,claude-fable-5,gemini-3.1-pro-preview --langs "$LANGS" --subset --workers 6 || exit 1
echo "ALL DONE"
