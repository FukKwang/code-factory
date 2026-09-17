#!/usr/bin/env bash
# Serve the finetuned model via llama.cpp for code-factory
#
# Usage:
#   ./serve.sh                    # defaults
#   ./serve.sh /path/to/model.gguf
#   CTX=8192 PORT=9090 ./serve.sh

MODEL="${1:-models/monty-coder-gguf_gguf/qwen3-4b.Q4_K_M.gguf}"
PORT="${PORT:-8081}"
HOST="${HOST:-0.0.0.0}"
CTX="${CTX:-32768}"
GPU_LAYERS="${GPU_LAYERS:-99}"
PARALLEL="${PARALLEL:-4}"

LLAMA_SERVER="${LLAMA_SERVER:-llama-server}"

exec "$LLAMA_SERVER" \
  --model "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --ctx-size "$CTX" \
  --n-gpu-layers "$GPU_LAYERS" \
  --parallel "$PARALLEL" \
  --flash-attn on
