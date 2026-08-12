#!/usr/bin/env python3
"""更新 Einstein Arena 任务的 SOTA 信息（正确版本）

同时更新：
1. instruction.md — agent 能看到
2. task.toml — 外部工具能看到

用法:
    python3 update_sota.py circle-packing
    python3 update_sota.py --all
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


def get_sota_info(slug: str) -> dict | None:
    """获取 SOTA 信息"""
    try:
        # 获取问题 ID
        resp = requests.get(f"{API_BASE}/problems/{slug}", timeout=30)
        resp.raise_for_status()
        problem_id = resp.json()["id"]

        # 获取排行榜
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
            "score": top1["bestScore"],
            "agent": top1["agentName"],
            "submissions": top1["submissions"],
        }
    except Exception as e:
        print(f"ERROR: Failed to fetch SOTA for {slug}: {e}", file=sys.stderr)
        return None


def update_instruction_md(slug: str, sota: dict) -> bool:
    """更新 instruction.md（添加 SOTA section）"""
    md_path = Path(f"benchmarks/einsteinarena-harbor/{slug}/instruction.md")

    if not md_path.exists():
        print(f"ERROR: {md_path} not found", file=sys.stderr)
        return False

    try:
        content = md_path.read_text()

        # 检查是否已有 SOTA section
        if "## Current Best Score (SOTA)" in content:
            print(f"INFO: {slug} instruction.md already has SOTA section", file=sys.stderr)
            # 更新现有 section
            lines = content.split("\n")
            new_lines = []
            in_sota = False

            for line in lines:
                if line.startswith("## Current Best Score (SOTA)"):
                    in_sota = True
                    new_lines.append(line)
                    new_lines.append("")
                    new_lines.append(f"**Score:** {sota['score']}")
                    new_lines.append(f"**Agent:** {sota['agent']}")
                    new_lines.append("**Source:** https://einsteinarena.com/")
                    new_lines.append(f"**Updated:** {datetime.now().strftime('%Y-%m-%d')}")
                    new_lines.append("")
                    new_lines.append("Your goal is to match or exceed this score.")
                elif in_sota:
                    # 跳过旧的 SOTA 内容，直到下一个 section
                    if line.startswith("##") and line != "## Current Best Score (SOTA)":
                        in_sota = False
                        new_lines.append(line)
                    elif not line.startswith("**") and line.strip() and not line.startswith("Your goal"):
                        in_sota = False
                        new_lines.append(line)
                else:
                    new_lines.append(line)

            md_path.write_text("\n".join(new_lines))
            return True

        # 添加新的 SOTA section
        sota_section = f"""

## Current Best Score (SOTA)

**Score:** {sota['score']}
**Agent:** {sota['agent']}
**Source:** https://einsteinarena.com/
**Updated:** {datetime.now().strftime('%Y-%m-%d')}

Your goal is to match or exceed this score.
"""

        md_path.write_text(content + sota_section)
        return True

    except Exception as e:
        print(f"ERROR: Failed to update {md_path}: {e}", file=sys.stderr)
        return False


def update_task_toml(slug: str, sota: dict) -> bool:
    """更新 task.toml（添加 [metadata.sota]）"""
    toml_path = Path(f"benchmarks/einsteinarena-harbor/{slug}/task.toml")

    if not toml_path.exists():
        print(f"ERROR: {toml_path} not found", file=sys.stderr)
        return False

    try:
        content = toml_path.read_text()

        # 检查是否已有 [metadata.sota]
        if "[metadata.sota]" in content:
            print(f"INFO: {slug} task.toml already has [metadata.sota]", file=sys.stderr)
            return False

        # 找到 [metadata] section 结束位置
        lines = content.split("\n")
        metadata_end_idx = None

        for i, line in enumerate(lines):
            if line.startswith("[metadata]"):
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith("[") and not lines[j].startswith("[metadata."):
                        metadata_end_idx = j
                        break
                break

        if metadata_end_idx is None:
            print("ERROR: Could not find [metadata] section end", file=sys.stderr)
            return False

        # 插入 [metadata.sota]
        sota_lines = [
            "",
            "[metadata.sota]",
            f"score = {sota['score']}",
            f"agent = \"{sota['agent']}\"",
            "source = \"https://einsteinarena.com/\"",
            f"updated = \"{datetime.now().strftime('%Y-%m-%d')}\"",
        ]

        new_lines = lines[:metadata_end_idx] + sota_lines + lines[metadata_end_idx:]
        toml_path.write_text("\n".join(new_lines))
        return True

    except Exception as e:
        print(f"ERROR: Failed to update {toml_path}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="更新 Einstein Arena SOTA 信息")
    parser.add_argument("task", nargs="?", help="任务 slug")
    parser.add_argument("--all", action="store_true", help="更新所有任务")
    args = parser.parse_args()

    if not args.all and not args.task:
        parser.error("必须指定任务 slug 或使用 --all")

    # 获取任务列表
    if args.all:
        harbor_dir = Path("benchmarks/einsteinarena-harbor")
        tasks = sorted([d.name for d in harbor_dir.iterdir() if d.is_dir()])
    else:
        tasks = [args.task]

    print("Einstein Arena SOTA Updater")
    print("=" * 80)
    print()

    for slug in tasks:
        print(f"Processing: {slug}")

        # 获取 SOTA 信息
        sota = get_sota_info(slug)
        if not sota:
            print("  ✗ Failed to fetch SOTA")
            continue

        print(f"  SOTA: {sota['score']} (by {sota['agent']})")

        # 更新 instruction.md
        md_updated = update_instruction_md(slug, sota)
        print(f"  instruction.md: {'✓ updated' if md_updated else '○ skipped'}")

        # 更新 task.toml
        toml_updated = update_task_toml(slug, sota)
        print(f"  task.toml:      {'✓ updated' if toml_updated else '○ skipped'}")

        print()

    print("=" * 80)
    print("Done!")
    print()
    print("IMPORTANT: Agent can now see SOTA in instruction.md")
    print("           task.toml is for external tools only")


if __name__ == "__main__":
    sys.exit(main())
