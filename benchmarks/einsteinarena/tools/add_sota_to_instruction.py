#!/usr/bin/env python3
"""将 SOTA 和 minImprovement 信息添加到 instruction.md

只更新 instruction.md（agent 能看到），不更新 task.toml（无用）。

用法:
    python3 add_sota_to_instruction.py circle-packing
    python3 add_sota_to_instruction.py --all
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    print("ERROR: requests library not found", file=sys.stderr)
    sys.exit(1)

API_BASE = "https://einsteinarena.com/api"


def get_problem_info(slug: str) -> dict | None:
    """获取问题信息（SOTA + minImprovement）"""
    try:
        # 获取问题详情
        resp = requests.get(f"{API_BASE}/problems/{slug}", timeout=30)
        resp.raise_for_status()
        problem = resp.json()

        problem_id = problem["id"]
        min_improvement = problem.get("minImprovement", 0)
        scoring = problem.get("scoring", "maximize")

        # 获取排行榜 Top1
        resp = requests.get(
            f"{API_BASE}/leaderboard",
            params={"problem_id": problem_id, "limit": 1},
            timeout=30,
        )
        resp.raise_for_status()
        lb = resp.json()

        if not lb:
            return {
                "min_improvement": min_improvement,
                "scoring": scoring,
                "sota_score": None,
            }

        return {
            "min_improvement": min_improvement,
            "scoring": scoring,
            "sota_score": lb[0]["bestScore"],
        }

    except Exception as e:
        print(f"ERROR: Failed to fetch info for {slug}: {e}", file=sys.stderr)
        return None


def update_instruction_md(slug: str, info: dict) -> bool:
    """更新 instruction.md（添加 SOTA section）"""
    md_path = Path(f"benchmarks/einsteinarena/{slug}/instruction.md")

    if not md_path.exists():
        print(f"ERROR: {md_path} not found", file=sys.stderr)
        return False

    try:
        content = md_path.read_text()

        # 检查是否已有 SOTA section
        if "## Current Best Score" in content or "## State of the Art" in content:
            # 删除旧的 section
            lines = content.split("\n")
            new_lines = []
            skip = False

            for line in lines:
                if line.startswith("## Current Best Score") or line.startswith("## State of the Art"):
                    skip = True
                elif skip and line.startswith("##"):
                    skip = False
                    new_lines.append(line)
                elif not skip:
                    new_lines.append(line)

            content = "\n".join(new_lines).rstrip()

        # 构建新的 SOTA section
        sota_section = "\n\n## State of the Art\n\n"

        if info["sota_score"] is not None:
            sota_section += f"**Current best score:** {info['sota_score']}\n"
        else:
            sota_section += "**Current best score:** No submissions yet\n"

        sota_section += f"**Updated:** {datetime.now().strftime('%Y-%m-%d')}\n"

        if info["min_improvement"] > 0:
            sota_section += f"\n**Minimum improvement:** Your score must improve by at least {info['min_improvement']} to be considered meaningful.\n"

        # 添加到文件末尾
        md_path.write_text(content + sota_section)
        return True

    except Exception as e:
        print(f"ERROR: Failed to update {md_path}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description="添加 SOTA 和 minImprovement 信息到 instruction.md"
    )
    parser.add_argument("task", nargs="?", help="任务 slug")
    parser.add_argument("--all", action="store_true", help="更新所有任务")
    args = parser.parse_args()

    if not args.all and not args.task:
        parser.error("必须指定任务 slug 或使用 --all")

    # 获取任务列表
    if args.all:
        harbor_dir = Path("benchmarks/einsteinarena")
        tasks = sorted([d.name for d in harbor_dir.iterdir() if d.is_dir()])
    else:
        tasks = [args.task]

    print("Einstein Arena Instruction Updater")
    print("=" * 80)
    print()

    for slug in tasks:
        print(f"Processing: {slug}")

        # 获取问题信息
        info = get_problem_info(slug)
        if not info:
            print("  ✗ Failed to fetch info")
            continue

        score_str = f"{info['sota_score']}" if info['sota_score'] else "No submissions"
        print(f"  Best score: {score_str}")
        print(f"  Min improvement: {info['min_improvement']}")

        # 更新 instruction.md
        updated = update_instruction_md(slug, info)
        print(f"  Status: {'✓ updated' if updated else '✗ failed'}")
        print()

    print("=" * 80)
    print("✓ Done! Agent can now see SOTA and min improvement in instruction.md")


if __name__ == "__main__":
    sys.exit(main())
