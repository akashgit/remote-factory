#!/usr/bin/env python3
"""获取 Einstein Arena 任务的 Top1 分数

用法:
    # 单个任务
    python3 get_einsteinarena_top1.py circle-packing

    # 所有任务
    python3 get_einsteinarena_top1.py --all

    # 输出为 JSON
    python3 get_einsteinarena_top1.py --all --json > top1_scores.json

    # 更新 Harbor task.toml 文件
    python3 get_einsteinarena_top1.py --all --update-toml
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests library not found", file=sys.stderr)
    print("Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

API_BASE = "https://einsteinarena.com/api"


def get_problem_id(slug: str) -> int | None:
    """获取问题的 ID"""
    try:
        resp = requests.get(f"{API_BASE}/problems/{slug}", timeout=30)
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception as e:
        print(f"ERROR: Failed to get problem ID for {slug}: {e}", file=sys.stderr)
        return None


def get_top1_score(problem_id: int) -> dict | None:
    """获取 Top1 分数"""
    try:
        resp = requests.get(
            f"{API_BASE}/leaderboard",
            params={"problem_id": problem_id, "limit": 1},
            timeout=30,
        )
        resp.raise_for_status()
        lb = resp.json()

        if not lb:
            return None

        top1 = lb[0]
        return {
            "rank": top1["rank"],
            "agent": top1["agentName"],
            "score": top1["bestScore"],
            "submissions": top1["submissions"],
        }
    except Exception as e:
        print(f"ERROR: Failed to get leaderboard for problem {problem_id}: {e}", file=sys.stderr)
        return None


def get_all_tasks() -> list[str]:
    """获取所有任务的 slug"""
    harbor_dir = Path("benchmarks/einsteinarena-harbor")
    if not harbor_dir.exists():
        return []

    return sorted([d.name for d in harbor_dir.iterdir() if d.is_dir()])


def update_task_toml(slug: str, top1_score: float, top1_agent: str) -> bool:
    """更新 task.toml 文件，添加 Top1 分数"""
    toml_path = Path(f"benchmarks/einsteinarena-harbor/{slug}/task.toml")

    if not toml_path.exists():
        print(f"WARNING: {toml_path} does not exist", file=sys.stderr)
        return False

    try:
        # 读取现有内容
        content = toml_path.read_text()

        # 检查是否已有 [metadata.sota] section
        if "[metadata.sota]" in content:
            print(f"INFO: {slug} already has [metadata.sota], skipping", file=sys.stderr)
            return False

        # 找到 [metadata] section 结束位置（下一个 section 开始前）
        lines = content.split("\n")
        metadata_end_idx = None

        for i, line in enumerate(lines):
            if line.startswith("[metadata]"):
                # 找到 [metadata] 后的下一个 section
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("[") and not lines[j].startswith("[metadata."):
                        metadata_end_idx = j
                        break
                break

        if metadata_end_idx is None:
            print(f"ERROR: Could not find metadata section end in {toml_path}", file=sys.stderr)
            return False

        # 在 metadata section 结束前插入 SOTA subsection
        sota_lines = [
            "",
            "[metadata.sota]",
            f"score = {top1_score}",
            f'agent = "{top1_agent}"',
            f'source = "https://einsteinarena.com/"',
        ]

        new_lines = lines[:metadata_end_idx] + sota_lines + lines[metadata_end_idx:]

        # 写回文件
        toml_path.write_text("\n".join(new_lines))
        return True

    except Exception as e:
        print(f"ERROR: Failed to update {toml_path}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="获取 Einstein Arena 任务的 Top1 分数"
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="任务 slug (例如: circle-packing)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="获取所有任务的 Top1 分数",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出",
    )
    parser.add_argument(
        "--update-toml",
        action="store_true",
        help="更新 Harbor task.toml 文件（添加 [metadata.sota] section）",
    )
    args = parser.parse_args()

    if not args.all and not args.task:
        parser.error("必须指定任务 slug 或使用 --all")

    # 获取任务列表
    if args.all:
        tasks = get_all_tasks()
        if not tasks:
            print("ERROR: No tasks found in benchmarks/einsteinarena-harbor/", file=sys.stderr)
            return 1
    else:
        tasks = [args.task]

    results = {}

    for slug in tasks:
        # 获取问题 ID
        problem_id = get_problem_id(slug)
        if problem_id is None:
            results[slug] = {"error": "Failed to get problem ID"}
            continue

        # 获取 Top1 分数
        top1 = get_top1_score(problem_id)
        if top1 is None:
            results[slug] = {"error": "No leaderboard data"}
            continue

        results[slug] = {
            "problem_id": problem_id,
            "top1_score": top1["score"],
            "top1_agent": top1["agent"],
            "submissions": top1["submissions"],
        }

        # 更新 task.toml（如果需要）
        if args.update_toml:
            updated = update_task_toml(slug, top1["score"], top1["agent"])
            results[slug]["toml_updated"] = updated

    # 输出结果
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("Einstein Arena Top1 Scores")
        print("=" * 80)
        for slug, data in results.items():
            if "error" in data:
                print(f"✗ {slug}: {data['error']}")
            else:
                score = data["top1_score"]
                agent = data["top1_agent"]
                print(f"✓ {slug}:")
                print(f"    Score: {score}")
                print(f"    Agent: {agent}")
                print(f"    Submissions: {data['submissions']}")
                if args.update_toml and "toml_updated" in data:
                    status = "✓ updated" if data["toml_updated"] else "○ skipped"
                    print(f"    TOML: {status}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
