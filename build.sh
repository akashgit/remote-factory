#!/usr/bin/env bash
set -euo pipefail

CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-podman}"
BUILD_STAGE="${BUILD_STAGE:-0}"
STAGE_TIMEOUT="${STAGE_TIMEOUT:-3600}"
IMAGE_NAME="${IMAGE_NAME:-build-root-env}"
RESULTS_DIR="${RESULTS_DIR:-results}"

mkdir -p "$RESULTS_DIR"

build_image() {
    if [ ! -f Containerfile ]; then
        echo "ERROR: Containerfile not found" >&2
        exit 1
    fi
    echo "Building container image: $IMAGE_NAME"
    "$CONTAINER_RUNTIME" build -t "$IMAGE_NAME" -f Containerfile .
}

run_in_container() {
    local cmd="$1"
    echo "Running: $cmd (timeout: ${STAGE_TIMEOUT}s)"
    timeout "$STAGE_TIMEOUT" "$CONTAINER_RUNTIME" run --rm \
        -v "$(pwd)/gradle/init.d:/root/.gradle/init.d:ro" \
        -v "$(pwd)/local-repo:/root/.m2/repository:ro" \
        -v "$(pwd)/$RESULTS_DIR:/results:rw" \
        "$IMAGE_NAME" \
        bash -c "$cmd"
}

stage_deps() {
    echo "=== Stage 1: Dependency Resolution ==="
    local output
    output=$(run_in_container \
        "./gradlew dependencies --configuration compileClasspath --continue 2>&1" || true)
    echo "$output"
    echo "$output" | python3 scripts/parse_gradle_deps.py > "$RESULTS_DIR/stage1.json"
}

stage_compile() {
    echo "=== Stage 3: Compile ==="
    local output
    output=$(run_in_container \
        "./gradlew compileJava --continue 2>&1" || true)
    echo "$output"
    echo "$output" | python3 scripts/parse_compile_results.py > "$RESULTS_DIR/stage3.json"
}

stage_test() {
    echo "=== Stage 4: Test ==="
    local output
    output=$(run_in_container \
        "./gradlew test --continue 2>&1" || true)
    echo "$output"

    if [ -d "$RESULTS_DIR/test-results" ]; then
        python3 scripts/parse_test_reports.py "$RESULTS_DIR/test-results" \
            > "$RESULTS_DIR/stage4.json"
    else
        echo '{"tests":0,"passed":0,"failed":0,"skipped":0,"failures":[]}' \
            > "$RESULTS_DIR/stage4.json"
    fi
}

case "$BUILD_STAGE" in
    0)
        echo "Running all stages..."
        build_image
        stage_deps
        stage_compile
        stage_test
        ;;
    1)
        build_image
        stage_deps
        ;;
    2)
        echo "=== Stage 2: Artifact Recovery ==="
        echo "Artifact recovery is handled by the CEO agent, not this script."
        ;;
    3)
        build_image
        stage_compile
        ;;
    4)
        build_image
        stage_test
        ;;
    *)
        echo "ERROR: Unknown BUILD_STAGE=$BUILD_STAGE (expected 0-4)" >&2
        exit 1
        ;;
esac

echo "Stage $BUILD_STAGE complete. Results in $RESULTS_DIR/"
