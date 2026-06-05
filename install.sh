#!/usr/bin/env bash
set -euo pipefail

# ── Remote Factory Installer ─────────────────────────────────────
# Usage: curl -sSf https://raw.githubusercontent.com/akashgit/remote-factory/main/install.sh | bash

REPO_URL="https://github.com/akashgit/remote-factory.git"
MIN_PYTHON="3.11"

# ── banner ────────────────────────────────────────────────────────

echo ""
echo "  ┏━╸┏━┓┏━╸╺┳╸┏━┓┏━┓╻ ╻"
echo "  ┣╸ ┣━┫┃   ┃ ┃ ┃┣┳┛┗┳┛"
echo "  ╹  ╹ ╹┗━╸ ╹ ┗━┛╹┗╸ ╹ "
echo "  Multi-Agent Software Evolution"
echo ""
echo "  Installing Remote Factory..."
echo ""

# ── detect OS and arch ────────────────────────────────────────────

OS="$(uname -s)"
ARCH="$(uname -m)"
echo "  OS:   ${OS}"
echo "  Arch: ${ARCH}"
echo ""

# ── check Python 3.11+ ───────────────────────────────────────────

check_python_version() {
    local py_cmd="$1"
    if ! command -v "$py_cmd" &>/dev/null; then
        return 1
    fi
    local version
    version="$("$py_cmd" --version 2>&1 | awk '{print $2}')"
    local major minor
    major="$(echo "$version" | cut -d. -f1)"
    minor="$(echo "$version" | cut -d. -f2)"
    if [[ "$major" -ge 3 ]] && [[ "$minor" -ge 11 ]]; then
        echo "  Python: $version ($py_cmd)"
        return 0
    fi
    return 1
}

PYTHON_OK=false
for cmd in python3 python; do
    if check_python_version "$cmd"; then
        PYTHON_OK=true
        break
    fi
done

if [[ "$PYTHON_OK" != "true" ]]; then
    echo "  ERROR: Python ${MIN_PYTHON}+ is required but not found."
    echo "  Install Python ${MIN_PYTHON}+ and try again."
    exit 1
fi

echo ""

# ── check/install uv ─────────────────────────────────────────────

if command -v uv &>/dev/null; then
    echo "  uv: $(uv --version) (already installed)"
else
    echo "  uv: not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is on PATH for this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv &>/dev/null; then
        echo "  uv: $(uv --version) (installed)"
    else
        echo "  ERROR: Failed to install uv."
        exit 1
    fi
fi

echo ""

# ── install remote-factory ───────────────────────────────────────

echo "  Installing remote-factory..."
uv tool install "remote-factory @ git+${REPO_URL}"
echo ""

# ── register CEO agent ───────────────────────────────────────────

if command -v factory &>/dev/null; then
    echo "  Registering Factory CEO agent..."
    factory install || true
    echo ""
fi

# ── verify ────────────────────────────────────────────────────────

if command -v factory &>/dev/null; then
    echo "  Verification: factory CLI is available"
    factory --help >/dev/null 2>&1 && echo "  factory --help: OK" || echo "  factory --help: FAILED"
else
    echo "  WARNING: 'factory' not found on PATH."
    echo "  You may need to add ~/.local/bin to your PATH:"
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo ""

# ── success ───────────────────────────────────────────────────────

echo "  ============================================"
echo "  Remote Factory installed successfully!"
echo "  ============================================"
echo ""
echo "  Next steps:"
echo ""
echo "    1. Set up Vertex AI credentials:"
echo "       export CLAUDE_CODE_USE_VERTEX=1"
echo "       export CLOUD_ML_REGION=your-region"
echo "       export ANTHROPIC_VERTEX_PROJECT_ID=<your-project-id>"
echo ""
echo "    2. Run the factory on a project:"
echo "       factory ceo /path/to/your/project"
echo ""
echo "    3. Or build from a prompt:"
echo "       factory ceo --prompt \"Build a weather CLI\""
echo ""
echo "    4. Update later with:"
echo "       factory self-update"
echo ""
