#!/usr/bin/env bash
# Convenience wrapper: activates the venv (if present) and runs the agent.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -d ".venv" ]; then
  source .venv/bin/activate
fi

python main.py apply "$@"
