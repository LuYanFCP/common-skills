#!/usr/bin/env bash
set -euo pipefail

readonly LOCAL_BIN_DIR="${HOME}/.local/bin"
readonly CONFIG_DIR="${HOME}/.local/glm-ocr"
readonly CONFIG_PATH="${CONFIG_DIR}/config.yaml"
readonly OLLAMA_STATE_DIR="${HOME}/.local/state/ollama"
readonly OLLAMA_LOG_PATH="${OLLAMA_STATE_DIR}/serve.log"
readonly MODEL_NAME="glm-ocr:latest"

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This setup script only supports macOS." >&2
    exit 1
  fi
}

ensure_path() {
  export PATH="${LOCAL_BIN_DIR}:/opt/homebrew/bin:/usr/local/bin:${PATH}"
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return
  fi

  curl -LsSf https://astral.sh/uv/install.sh | sh
  ensure_path

  if ! command -v uv >/dev/null 2>&1; then
    echo "uv was installed but is still not on PATH. Add ${LOCAL_BIN_DIR} to PATH and retry." >&2
    exit 1
  fi
}

ensure_glmocr() {
  uv tool install --upgrade "glmocr[selfhosted]"

  if ! command -v glmocr >/dev/null 2>&1; then
    echo "glmocr installation completed but the command is not on PATH." >&2
    exit 1
  fi
}

ensure_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    return
  fi

  if command -v brew >/dev/null 2>&1; then
    brew install ollama
    export PATH="$(brew --prefix)/bin:${PATH}"
  else
    curl -fsSL https://ollama.com/install.sh | sh
  fi

  if ! command -v ollama >/dev/null 2>&1; then
    echo "Failed to install ollama." >&2
    exit 1
  fi
}

ollama_ready() {
  ollama list >/dev/null 2>&1
}

ensure_ollama_service() {
  if ollama_ready; then
    return
  fi

  mkdir -p "${OLLAMA_STATE_DIR}"
  nohup ollama serve >"${OLLAMA_LOG_PATH}" 2>&1 &

  for _ in {1..15}; do
    sleep 2
    if ollama_ready; then
      return
    fi
  done

  echo "ollama serve did not become ready. Check ${OLLAMA_LOG_PATH}." >&2
  exit 1
}

ensure_model() {
  local models
  models="$(ollama list || true)"

  if [[ "${models}" == *"${MODEL_NAME}"* ]]; then
    return
  fi

  ollama pull "${MODEL_NAME}"
}

write_config() {
  mkdir -p "${CONFIG_DIR}"
  local glmocr_cmd
  local glmocr_python
  local default_config_path

  glmocr_cmd="$(command -v glmocr)"
  glmocr_python="$(sed -n '1s/^#!//p' "${glmocr_cmd}")"

  if [[ -z "${glmocr_python}" || ! -x "${glmocr_python}" ]]; then
    echo "Could not locate the Python runtime behind glmocr." >&2
    exit 1
  fi

  default_config_path="$("${glmocr_python}" - <<'PY'
from glmocr.config import GlmOcrConfig
print(GlmOcrConfig.default_path())
PY
)"

  "${glmocr_python}" - <<'PY' "${default_config_path}" "${CONFIG_PATH}" "${MODEL_NAME}"
from pathlib import Path
import sys

import yaml

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
model_name = sys.argv[3]

data = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
pipeline = data.setdefault("pipeline", {})
pipeline.setdefault("maas", {})["enabled"] = False

ocr_api = pipeline.setdefault("ocr_api", {})
ocr_api["api_host"] = "localhost"
ocr_api["api_port"] = 11434
ocr_api["api_path"] = "/api/generate"
ocr_api["model"] = model_name
ocr_api["api_mode"] = "ollama_generate"

target_path.parent.mkdir(parents=True, exist_ok=True)
target_path.write_text(
    yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
    encoding="utf-8",
)
PY
}

verify_setup() {
  glmocr parse --help >/dev/null
  ollama list >/dev/null
}

main() {
  require_macos
  ensure_path
  ensure_uv
  ensure_glmocr
  ensure_ollama
  ensure_ollama_service
  ensure_model
  write_config
  verify_setup

  printf 'Ready\n'
  printf 'glmocr: %s\n' "$(command -v glmocr)"
  printf 'ollama: %s\n' "$(command -v ollama)"
  printf 'config: %s\n' "${CONFIG_PATH}"
  printf 'model: %s\n' "${MODEL_NAME}"
}

main "$@"
