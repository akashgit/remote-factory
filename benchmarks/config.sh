#!/usr/bin/env bash
# benchmarks/config.sh — Benchmark configuration mapping.
# Source this after lib.sh to get benchmark_config, benchmark_dataset,
# benchmark_all_names, and benchmark_instance_id.

benchmark_all_names() {
    echo "swebench featurebench terminalbench programbench harborindex"
}

benchmark_config() {
    local name="$1"

    BENCH_DATASET=""
    BENCH_LOCAL_PATH=""
    BENCH_AGENT_CLASS=""
    BENCH_AGENT_IMPORT_FLAG=""
    BENCH_EXTRA_INSTRUCTION=""
    BENCH_FILTER_STYLE=""
    BENCH_ALLOW_HOSTS=""
    BENCH_POST_EVAL_CMD=""

    case "${name}" in
        swebench)
            BENCH_DATASET="swe-bench/swe-bench-verified"
            BENCH_AGENT_CLASS="factory_harbor_agent:SwebenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_FILTER_STYLE="glob"
            ;;
        featurebench)
            BENCH_DATASET="featurebench"
            BENCH_AGENT_CLASS="factory_harbor_agent:FeaturebenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_EXTRA_INSTRUCTION="featurebench-extra-instructions.md"
            BENCH_FILTER_STYLE="exact"
            ;;
        terminalbench)
            BENCH_DATASET="terminal-bench@2.0"
            BENCH_AGENT_CLASS="factory_harbor_agent:TerminalbenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_EXTRA_INSTRUCTION="terminalbench-extra-instructions.md"
            BENCH_FILTER_STYLE="exact"
            ;;
        programbench)
            BENCH_LOCAL_PATH="${HARNESS_DIR}/benchmarks/programbench-harbor"
            BENCH_AGENT_CLASS="factory_harbor_agent:ProgramBenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent"
            BENCH_FILTER_STYLE="none"
            BENCH_ALLOW_HOSTS="api.anthropic.com sentry.io statsig.anthropic.com"
            BENCH_POST_EVAL_CMD="uvx programbench eval"
            ;;
        legacybench)
            BENCH_DATASET="factory-ai/legacy-bench"
            BENCH_AGENT_CLASS="factory_harbor_agent:LegacybenchFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_FILTER_STYLE="glob"
            ;;
        harborindex)
            BENCH_DATASET="harbor-index/harbor-index-1.0"
            BENCH_AGENT_CLASS="factory_harbor_agent:HarborIndexFactoryCeo"
            BENCH_AGENT_IMPORT_FLAG="--agent-import-path"
            BENCH_FILTER_STYLE="exact"
            ;;
        *)
            echo "ERROR: Unknown benchmark '${name}'"
            echo "Valid benchmarks: swebench, featurebench, terminalbench, programbench, legacybench, harborindex"
            return 1
            ;;
    esac
}

benchmark_dataset() {
    local name="$1"
    local split="${2:-}"

    case "${name}" in
        featurebench)
            case "${split:-full}" in
                lite)       BENCH_DATASET="featurebench-lite" ;;
                full|fast)  BENCH_DATASET="featurebench" ;;
                *)          BENCH_DATASET="featurebench-${split}" ;;
            esac
            ;;
    esac
}

benchmark_instance_id() {
    local benchmark="$1"
    local task="$2"
    if [ "${benchmark}" = "programbench" ]; then
        case "${task}" in
            cmatrix) echo "abishekvashok__cmatrix.5c082c6" ;;
            *)       echo "${task}" ;;
        esac
    else
        echo "${task}"
    fi
}
