#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
set -euo pipefail
VEIL_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$VEIL_ROOT"
printf '\n  Veil · Local medical document redaction\n\n'
if [ ! -f frontend/dist/index.html ]; then
  printf 'The bundled interface is missing. Developers: run npm ci --prefix frontend && npm run build --prefix frontend.\n' >&2
  exit 1
fi
if [ "$(uname -s)" != Darwin ]; then
  printf 'This launcher is tested on macOS. Linux also needs Tesseract for OCR; see README.\n'
fi
if command -v uv >/dev/null 2>&1; then
  VEIL_UV="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
  VEIL_UV="$HOME/.local/bin/uv"
else
  printf 'Installing the Python runtime manager from Astral (one-time setup)…\n'
  VEIL_INSTALLER="$(mktemp)"
  trap 'rm -f "$VEIL_INSTALLER"' EXIT
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location https://astral.sh/uv/0.11.16/install.sh -o "$VEIL_INSTALLER"
  UV_NO_MODIFY_PATH=1 sh "$VEIL_INSTALLER"
  rm -f "$VEIL_INSTALLER"
  trap - EXIT
  VEIL_UV="$HOME/.local/bin/uv"
fi
if [ ! -x .venv/bin/python ]; then
  printf 'Preparing isolated Python 3.12…\n'
  "$VEIL_UV" venv --python 3.12 .venv
fi
VEIL_FINGERPRINT="$(.venv/bin/python -c 'import hashlib; from pathlib import Path; print(hashlib.sha256(Path("backend/requirements.lock").read_bytes()).hexdigest())')-$(uname -m)"
VEIL_INSTALLED="$(cat .venv/.veil-requirements 2>/dev/null || true)"
if [ "$VEIL_FINGERPRINT" != "$VEIL_INSTALLED" ]; then
  printf 'Installing local dependencies. This can take a few minutes on the first launch…\n'
  "$VEIL_UV" pip install --python .venv/bin/python --require-hashes -r backend/requirements.lock
  printf '%s' "$VEIL_FINGERPRINT" > .venv/.veil-requirements
fi
if [ "${1:-}" = --setup-only ]; then
  .venv/bin/python scripts/setup_model.py
  exit $?
fi
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export TOKENIZERS_PARALLELISM=false
exec .venv/bin/python scripts/serve.py "$@"
