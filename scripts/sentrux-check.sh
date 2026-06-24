#!/usr/bin/env bash
set -euo pipefail

SENTRUX_VERSION="${SENTRUX_VERSION:-v0.5.7}"

arch="$(uname -m)"
case "$arch" in
  x86_64)  platform="linux-x86_64" ;;
  aarch64) platform="linux-aarch64" ;;
  *)
    echo >&2 "Unsupported architecture: $arch"
    exit 1
    ;;
esac

if ! command -v sentrux &>/dev/null; then
  echo >&2 "sentrux not found, installing ${SENTRUX_VERSION} for ${platform}..."
  url="https://github.com/sentrux/sentrux/releases/download/${SENTRUX_VERSION}/sentrux-${platform}"
  if [ -w /usr/local/bin ]; then
    dest="/usr/local/bin/sentrux"
  else
    dest="$HOME/.local/bin/sentrux"
    mkdir -p "$HOME/.local/bin"
    export PATH="$HOME/.local/bin:$PATH"
  fi
  curl -fsSL "$url" -o "$dest"
  chmod +x "$dest"
  echo >&2 "Installed sentrux to $dest"
fi

if [ ! -d "$HOME/.sentrux/plugins" ] || [ -z "$(ls -A "$HOME/.sentrux/plugins" 2>/dev/null)" ]; then
  echo >&2 "Installing standard plugins..."
  sentrux plugin add-standard
fi

if [ "${1:-}" = "--install-only" ]; then
  echo >&2 "Sentrux installed successfully."
  exit 0
fi

echo >&2 "Running sentrux check..."
exec sentrux check .
