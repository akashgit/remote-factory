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

        # 在 [metadata] section 后添加 SOTA 信息
        lines = content.split("\n")
        new_lines = []

        for i, line in enumerate(lines):
            new_lines.append(line)

            # 找到 [metadata] section 的结尾
            if line.startswith("[metadata]"):
                # 找到下一个 section 开始的位置
                next_section_idx = None
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("["):
                        next_section_idx = j
                        break

                # 在下一个 section 前插入 SOTA 信息
                if next_section_idx:
                    insert_idx = i + 1
                    while insert_idx < next_section_idx:
                        new_lines.append(lines[insert_idx])
                        insert_idx += 1

                    # 插入 SOTA section
                    new_lines.append("")
                    new_lines.append("[metadata.sota]")
                    new_lines.append(f"score = {top1_score}")
                    new_lines.append(f'agent = "{top1_agent}"')
                    new_lines.append(f'source = "https://einsteinarena.com/"')

                    # 跳过已添加的行
                    while i < next_section_idx - 1:
                        i += 1

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
