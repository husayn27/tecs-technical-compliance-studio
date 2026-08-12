#!/bin/zsh

set -e

TECS_PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
TECS_ENGINE_BINARY="$TECS_PROJECT_ROOT/services/local_engine/dist/tecs-engine"
TECS_ENGINE_PYTHON="$TECS_PROJECT_ROOT/services/local_engine/.venv/bin/python"
TECS_DESKTOP_DIR="$TECS_PROJECT_ROOT/apps/desktop"
TECS_BUNDLED_NODE="/Users/husayn/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
TECS_LOCAL_AI_RUNTIME="$TECS_PROJECT_ROOT/services/local_engine/runtime/llama-server"

cleanup_tecs() {
  [[ -n "${TECS_ENGINE_PID:-}" ]] && kill "$TECS_ENGINE_PID" 2>/dev/null || true
  [[ -n "${TECS_UI_PID:-}" ]] && kill "$TECS_UI_PID" 2>/dev/null || true
}

trap cleanup_tecs EXIT INT TERM

echo "Starting TECS Technical Compliance Studio..."

if [[ -x "$TECS_ENGINE_PYTHON" ]]; then
  TECS_LLAMA_SERVER_PATH="$TECS_LOCAL_AI_RUNTIME" \
  TECS_VISION_MODEL_REPOSITORY="ggml-org/Qwen2.5-VL-7B-Instruct-GGUF:Q4_K_M" \
  PYTHONPATH="$TECS_PROJECT_ROOT/services/local_engine/src" "$TECS_ENGINE_PYTHON" -m tecs_engine.main &
elif [[ -x "$TECS_ENGINE_BINARY" ]]; then
  "$TECS_ENGINE_BINARY" &
else
  echo "The local drawing engine was not found. Open this project in Codex and ask it to restore dependencies."
  exit 1
fi
TECS_ENGINE_PID=$!

if command -v pnpm >/dev/null 2>&1; then
  (cd "$TECS_DESKTOP_DIR" && pnpm dev --host 127.0.0.1) &
elif [[ -x "$TECS_BUNDLED_NODE" ]]; then
  (cd "$TECS_DESKTOP_DIR" && "$TECS_BUNDLED_NODE" node_modules/vite/bin/vite.js --host 127.0.0.1 --port 1420) &
else
  echo "The development runtime was not found. Open this project in Codex and ask it to restore dependencies."
  exit 1
fi
TECS_UI_PID=$!

sleep 3
open "http://127.0.0.1:1420"

echo ""
echo "TECS Technical Compliance Studio is running."
echo "If the browser did not open, visit: http://127.0.0.1:1420"
echo "Press Control-C here when you are finished."

wait
