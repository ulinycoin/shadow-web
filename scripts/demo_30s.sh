#!/usr/bin/env bash
# Paste-ready 30-second Shadow Web demo (v0.4.1).
# Requires: Python 3.10+, network, DEEPSEEK_API_KEY (or OPENAI_API_KEY).
set -euo pipefail

echo "==> 1/3 install (agent extra)"
python3 -m pip install -q "shadow-web[agent]"

echo "==> 2/3 browser once (Chromium for this demo)"
python3 -m playwright install chromium

if [[ -z "${DEEPSEEK_API_KEY:-}${OPENAI_API_KEY:-}${OPENROUTER_API_KEY:-}" ]]; then
  echo "Set DEEPSEEK_API_KEY (or OPENAI_API_KEY / OPENROUTER_API_KEY) and re-run."
  exit 1
fi

echo "==> 3/3 one command → one JSON"
shadow-web exec \
  "What is the heading text on https://example.com? Reply with the heading only." \
  --browser chromium \
  --model "${SHADOW_WEB_LLM_MODEL:-deepseek-chat}" \
  --max-steps 5
