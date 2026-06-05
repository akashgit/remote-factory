#!/usr/bin/env bash
set -euo pipefail

CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-podman}"
STAGE="${1:-all}"
PROJECT_DIR="${2:-.}"
RESULTS_DIR="${PROJECT_DIR}/results"
mkdir -p "$RESULTS_DIR"

# Build container image
"$CONTAINER_RUNTIME" build \
    --build-arg JDK_VERSION="${JDK_VERSION:-11}" \
    -t build-root:latest \
    -f "${PROJECT_DIR}/Containerfile" \
    "${PROJECT_DIR}"

run_in_container() {
    "$CONTAINER_RUNTIME" run --rm \
        -v "${PROJECT_DIR}/results:/results" \
        -v "${PROJECT_DIR}/scripts:/scripts:ro" \
        build-root:latest \
        "$@"
}

case "$STAGE" in
    deps|1)
        run_in_container ./gradlew dependencies \
            --configuration compileClasspath --continue \
            2>&1 | tee "$RESULTS_DIR/stage1-deps.log"
        python3 "${PROJECT_DIR}/scripts/parse_deps.py" \
            "$RESULTS_DIR/stage1-deps.log" > "$RESULTS_DIR/stage1-result.json"
        ;;
    compile|3)
        run_in_container ./gradlew compileJava --continue \
            2>&1 | tee "$RESULTS_DIR/stage3-compile.log"
        python3 "${PROJECT_DIR}/scripts/parse_compile.py" \
            "$RESULTS_DIR/stage3-compile.log" > "$RESULTS_DIR/stage3-result.json"
        ;;
    test|4)
        timeout 3600 run_in_container ./gradlew test --continue \
            2>&1 | tee "$RESULTS_DIR/stage4-test.log" || true
        python3 "${PROJECT_DIR}/scripts/parse_test_reports.py" \
            "$RESULTS_DIR/stage4-test.log" > "$RESULTS_DIR/stage4-result.json"
        ;;
    all)
        echo "Run individual stages: ./build.sh deps|compile|test"
        ;;
esac

# Aggregate into build-root-status.json
python3 "${PROJECT_DIR}/scripts/aggregate_status.py" "$RESULTS_DIR" > "$RESULTS_DIR/build-root-status.json"
