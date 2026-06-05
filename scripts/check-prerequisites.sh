#!/usr/bin/env bash
# Check all required tools for build-root mode

PASS=0
FAIL=0

check() {
    local name="$1" cmd="$2" min_version="$3"
    if command -v "$cmd" &>/dev/null; then
        echo "  [PASS] $name: $(command -v "$cmd")"
        ((PASS++))
    else
        echo "  [FAIL] $name: not found"
        ((FAIL++))
    fi
}

echo "Build-Root Prerequisites Check"
echo "==============================="
check "Podman" "podman" "4.0"
check "Python" "python3" "3.11"
check "Git" "git" "2.25"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && echo "All prerequisites met." || echo "Install missing tools before running build-root mode."
exit "$FAIL"
