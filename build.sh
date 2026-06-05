#!/usr/bin/env bash
set -euo pipefail

CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-podman}"
BUILD_STAGE="${BUILD_STAGE:-0}"
BUILD_MODE="${BUILD_MODE:-compile}"
STAGE_TIMEOUT="${STAGE_TIMEOUT:-3600}"
IMAGE_NAME="${IMAGE_NAME:-build-root-env}"
RESULTS_DIR="${RESULTS_DIR:-results}"
PODMAN_STORAGE_ROOT="${PODMAN_STORAGE_ROOT:-}"
PROJECT_SOURCE="${PROJECT_SOURCE:-}"

DISK_WARN_THRESHOLD_GB=20

mkdir -p "$RESULTS_DIR"

check_disk_space() {
    local target_path
    if [ -n "$PODMAN_STORAGE_ROOT" ]; then
        target_path="$PODMAN_STORAGE_ROOT"
    elif [ "$CONTAINER_RUNTIME" = "podman" ]; then
        target_path="${HOME}/.local/share/containers"
    else
        target_path="/var/lib/docker"
    fi

    if [ ! -d "$target_path" ]; then
        target_path="$(dirname "$target_path")"
    fi

    local avail_kb
    avail_kb=$(df --output=avail "$target_path" 2>/dev/null | tail -1 | tr -d ' ') || return 0
    local avail_gb=$(( avail_kb / 1048576 ))

    if [ "$avail_gb" -lt "$DISK_WARN_THRESHOLD_GB" ]; then
        echo "WARNING: Only ${avail_gb}GB free on $target_path (threshold: ${DISK_WARN_THRESHOLD_GB}GB)" >&2
        echo "WARNING: Large Java builds need 5-10GB for Gradle caches + container layers" >&2
        if [ -n "$PODMAN_STORAGE_ROOT" ]; then
            echo "INFO: Using custom storage root: $PODMAN_STORAGE_ROOT" >&2
        else
            echo "HINT: Set PODMAN_STORAGE_ROOT=/path/to/large/drive to use alternate storage" >&2
        fi
    else
        echo "Disk space OK: ${avail_gb}GB free on $target_path"
    fi
}

build_image() {
    if [ ! -f Containerfile ]; then
        echo "ERROR: Containerfile not found" >&2
        exit 1
    fi

    local storage_args=()
    if [ -n "$PODMAN_STORAGE_ROOT" ] && [ "$CONTAINER_RUNTIME" = "podman" ]; then
        storage_args=(--root "$PODMAN_STORAGE_ROOT")
    fi

    echo "Building container image: $IMAGE_NAME"
    "$CONTAINER_RUNTIME" "${storage_args[@]}" build \
        --layers \
        -t "$IMAGE_NAME" \
        -f Containerfile .
}

run_in_container() {
    local cmd="$1"
    echo "Running: $cmd (timeout: ${STAGE_TIMEOUT}s)"

    local storage_args=()
    if [ -n "$PODMAN_STORAGE_ROOT" ] && [ "$CONTAINER_RUNTIME" = "podman" ]; then
        storage_args=(--root "$PODMAN_STORAGE_ROOT")
    fi

    local mount_args=(
        -v "$(pwd)/gradle/init.d:/root/.gradle/init.d:ro"
        -v "$(pwd)/local-repo:/root/.m2/repository:ro"
        -v "$(pwd)/$RESULTS_DIR:/results:rw"
    )

    if [ -n "$PROJECT_SOURCE" ]; then
        mount_args+=(-v "$PROJECT_SOURCE:/workspace/project:ro")
    fi

    timeout "$STAGE_TIMEOUT" "$CONTAINER_RUNTIME" "${storage_args[@]}" run --rm \
        "${mount_args[@]}" \
        "$IMAGE_NAME" \
        bash -c "$cmd"
}

fast_build() {
    echo "=== Fast Build (compileJava + compileTestJava) ==="
    local output
    output=$(run_in_container \
        './gradlew compileJava compileTestJava --continue 2>&1' || true)
    echo "$output"
    echo "$output" > "$RESULTS_DIR/fast-build.log"
}

clean_build() {
    echo "=== Full Build (clean test build) ==="
    local output
    output=$(run_in_container \
        './gradlew clean test build --continue 2>&1' || true)
    echo "$output"
    echo "$output" > "$RESULTS_DIR/full-build.log"
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
    local cmd
    case "$BUILD_MODE" in
        fast)
            fast_build
            return
            ;;
        full)
            clean_build
            return
            ;;
        compile|*)
            cmd="./gradlew compileJava --continue 2>&1"
            ;;
    esac
    local output
    output=$(run_in_container "$cmd" || true)
    echo "$output"
    echo "$output" | python3 scripts/parse_compile_results.py > "$RESULTS_DIR/stage3.json"
}

stage_test() {
    echo "=== Stage 4: Test ==="
    if [ "$BUILD_MODE" = "full" ]; then
        clean_build
        return
    fi
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

check_disk_space

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

echo "Stage $BUILD_STAGE complete (mode: $BUILD_MODE). Results in $RESULTS_DIR/"
