#!/usr/bin/env bash
set -euo pipefail

passed=0
failed=0

check() {
    local label="$1" ok="$2" detail="$3"
    if [ "$ok" -eq 1 ]; then
        printf '[PASS] %s\n' "$detail"
        passed=$((passed + 1))
    else
        printf '[FAIL] %s\n' "$detail"
        failed=$((failed + 1))
    fi
}

check_optional() {
    local detail="$1"
    printf '[SKIP] %s\n' "$detail"
}

version_ge() {
    local have="$1" need="$2"
    printf '%s\n%s\n' "$need" "$have" | sort -V | head -n1 | grep -qx "$need"
}

# Podman >= 4.0
if command -v podman >/dev/null 2>&1; then
    podman_ver=$(podman --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
    if version_ge "$podman_ver" "4.0"; then
        check "podman" 1 "Podman $podman_ver (>= 4.0)"
    else
        check "podman" 0 "Podman $podman_ver — need >= 4.0"
    fi
else
    check "podman" 0 "Podman not found — install: apt install podman / dnf install podman / brew install podman"
fi

# Python >= 3.11
if command -v python3 >/dev/null 2>&1; then
    py_ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
    if version_ge "$py_ver" "3.11"; then
        check "python" 1 "Python $py_ver (>= 3.11)"
    else
        check "python" 0 "Python $py_ver — need >= 3.11"
    fi
else
    check "python" 0 "Python 3 not found"
fi

# git >= 2.30
if command -v git >/dev/null 2>&1; then
    git_ver=$(git --version | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
    if version_ge "$git_ver" "2.30"; then
        check "git" 1 "git $git_ver (>= 2.30)"
    else
        check "git" 0 "git $git_ver — need >= 2.30"
    fi
else
    check "git" 0 "git not found"
fi

# factory CLI
if command -v factory >/dev/null 2>&1; then
    check "factory" 1 "factory CLI available"
else
    check "factory" 0 "factory CLI not found — run: uv sync"
fi

# Optional: Docker
if command -v docker >/dev/null 2>&1; then
    docker_ver=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
    check_optional "Docker $docker_ver available (optional — set CONTAINER_RUNTIME=docker to use)"
else
    check_optional "Docker not found (optional — set CONTAINER_RUNTIME=docker to use)"
fi

echo ""
echo "Required: $passed passed, $failed failed"

if [ "$failed" -gt 0 ]; then
    exit 1
fi
