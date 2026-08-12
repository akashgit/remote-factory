#!/bin/bash
# 快速获取 Einstein Arena Top1 分数的便捷脚本

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 使用示例
if [ $# -eq 0 ]; then
    echo "用法:"
    echo "  $0 <task-slug>           # 单个任务"
    echo "  $0 --all                 # 所有任务"
    echo "  $0 --all --json          # JSON 格式"
    echo "  $0 --all --update-toml   # 更新 task.toml"
    echo ""
    echo "示例:"
    echo "  $0 circle-packing"
    echo "  $0 --all"
    echo "  $0 --all --json > top1_scores.json"
    exit 1
fi

# 调用 Python 脚本
python3 "$SCRIPT_DIR/get_einsteinarena_top1.py" "$@"
