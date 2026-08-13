#!/bin/bash
# Quick environment check for Lumen workflow

set -e

echo "=========================================="
echo "Lumen Environment Quick Check"
echo "=========================================="
echo

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Determine script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LUMEN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$LUMEN_DIR/.venv/bin/python"

# Check 1: Virtual environment exists
echo -n "1. Lumen venv... "
if [ -f "$VENV_PYTHON" ]; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    echo "   Not found: $VENV_PYTHON"
    echo "   Run: cd factory/lumen && uv venv .venv --python 3.11"
    exit 1
fi

# Check 2: NVIDIA GPU
echo -n "2. NVIDIA GPU... "
if command -v nvidia-smi &> /dev/null; then
    GPU_COUNT=$(nvidia-smi --list-gpus 2>/dev/null | wc -l)
    echo -e "${GREEN}✓${NC} (${GPU_COUNT} GPU(s))"
else
    echo -e "${YELLOW}⚠${NC} (no nvidia-smi, training will fail)"
fi

# Check 3: Critical packages
echo "3. Critical packages:"

check_package() {
    local pkg=$1
    echo -n "   - ${pkg}... "
    if "$VENV_PYTHON" -c "import ${pkg}" 2>/dev/null; then
        local version=$("$VENV_PYTHON" -c "import ${pkg}; print(getattr(${pkg}, '__version__', 'unknown'))" 2>/dev/null)
        echo -e "${GREEN}✓${NC} ${version}"
        return 0
    else
        echo -e "${RED}✗${NC}"
        return 1
    fi
}

ALL_OK=1
check_package "torch" || ALL_OK=0
check_package "vllm" || ALL_OK=0
check_package "verl" || ALL_OK=0
check_package "numpy" || ALL_OK=0
check_package "pandas" || ALL_OK=0

# Check 4: CUDA availability
echo -n "4. CUDA available... "
CUDA_CHECK=$("$VENV_PYTHON" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null)
if [ "$CUDA_CHECK" = "True" ]; then
    DEVICE_NAME=$("$VENV_PYTHON" -c "import torch; print(torch.cuda.get_device_name(0) if torch.cuda.device_count() > 0 else 'N/A')" 2>/dev/null)
    echo -e "${GREEN}✓${NC} ${DEVICE_NAME}"
else
    echo -e "${RED}✗${NC}"
    ALL_OK=0
fi

echo
echo "=========================================="

if [ $ALL_OK -eq 1 ]; then
    echo -e "${GREEN}✓ All critical checks passed!${NC}"
    echo
    echo "Ready to run:"
    echo "  factory ceo /path/to/project --mode lumen"
    exit 0
else
    echo -e "${RED}✗ Some checks failed${NC}"
    echo
    echo "Installation guide:"
    echo "  factory/lumen/README.md"
    exit 1
fi
