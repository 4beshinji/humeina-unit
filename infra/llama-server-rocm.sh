#!/usr/bin/env bash
set -euo pipefail

# 複数GPU環境での自動選択はiGPUや想定外GPUを掴み得るため禁止する。
: "${YOMIAGE_LLM_GPU_DEVICE:?set YOMIAGE_LLM_GPU_DEVICE to the ROCm GPU index}"
: "${LLAMA_MODEL_PATH:?set LLAMA_MODEL_PATH to a GGUF file}"

if [[ ! "${YOMIAGE_LLM_GPU_DEVICE}" =~ ^[0-9]+$ ]]; then
  echo "YOMIAGE_LLM_GPU_DEVICE must be a non-negative integer" >&2
  exit 64
fi
if [[ ! -f "${LLAMA_MODEL_PATH}" ]]; then
  echo "LLAMA_MODEL_PATH does not exist: ${LLAMA_MODEL_PATH}" >&2
  exit 66
fi

export ROCR_VISIBLE_DEVICES="${YOMIAGE_LLM_GPU_DEVICE}"
export HIP_VISIBLE_DEVICES="${YOMIAGE_LLM_GPU_DEVICE}"
export GPU_DEVICE_ORDINAL="${YOMIAGE_LLM_GPU_DEVICE}"

exec "${LLAMA_SERVER_BIN:-llama-server}" \
  --model "${LLAMA_MODEL_PATH}" \
  --host "${LLAMA_SERVER_HOST:-127.0.0.1}" \
  --port "${LLAMA_SERVER_PORT:-8080}" \
  --ctx-size "${LLAMA_CTX_SIZE:-32768}" \
  --parallel "${LLAMA_PARALLEL:-1}" \
  --device ROCm0 \
  --split-mode none \
  --main-gpu 0 \
  --n-gpu-layers "${LLAMA_GPU_LAYERS:-auto}" \
  --fit on \
  --fit-target "${LLAMA_FIT_TARGET_MIB:-2048}" \
  --flash-attn on \
  --cache-type-k "${LLAMA_CACHE_TYPE_K:-q8_0}" \
  --cache-type-v "${LLAMA_CACHE_TYPE_V:-q8_0}" \
  --jinja \
  --reasoning "${LLAMA_REASONING:-off}" \
  --metrics \
  "$@"
