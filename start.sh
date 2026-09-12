#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

# Ensure Ollama daemon is running before starting the web server
if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  echo "Ollama is offline. Starting service..."
  if [ -d "/Applications/Ollama.app" ]; then
    open -a Ollama --hide
  elif command -v ollama >/dev/null 2>&1; then
    nohup ollama serve >/dev/null 2>&1 &
  fi

  # Poll until port 11434 is accepting connections (up to 10 seconds)
  for _ in {1..10}; do
    if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      echo "Ollama daemon connected."
      break
    fi
    sleep 1
  done
fi

exec python run.py "$@"