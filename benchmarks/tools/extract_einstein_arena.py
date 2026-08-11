#!/usr/bin/env python3
"""Extract Einstein Arena tasks from public API to Harbor format.

Usage:
    python3 extract_einstein_arena.py [--output DIR] [--dry-run]
"""

import json
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: requests library not found", file=sys.stderr)
    print("Install with: pip install requests", file=sys.stderr)
    sys.exit(1)

API_BASE = "https://einsteinarena.com/api"

# 网页显示的 17 个任务（从 https://einsteinarena.com/ "Optimization Problems" 部分）
WEBPAGE_TASKS = [
    "circle-packing",
    "circles-rectangle",
    "difference-bases",
    "edges-vs-triangles",
    "erdos-min-overlap",
    "first-autocorrelation-inequality",
    "flat-polynomials",
    "heilbronn-triangles",
    "kissing-number-d11-605",
    "kissing-number-d12-842",
    "min-distance-ratio-2d",
    "second-autocorrelation-inequality",
    "tammes-problem",
    "prime-number-theorem",
    "third-autocorrelation-inequality",
    "thomson-problem",
    "uncertainty-principle",
]


class APIClient:
    """Einstein Arena API 客户端"""

    def __init__(self, base_url: str = API_BASE):
        self.base_url = base_url

    def list_problems(self) -> list[dict]:
        """获取所有问题列表"""
        url = f"{self.base_url}/problems"
        print(f"Fetching: {url}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def get_problem(self, slug: str) -> dict:
        """获取单个问题详情"""
        url = f"{self.base_url}/problems/{slug}"
        print(f"  Fetching: {slug}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()


class HarborConverter:
    """转换为 Harbor 格式"""

    def generate_task_toml(self, problem: dict) -> str:
        """生成 task.toml"""
        slug = problem["slug"]
        title = problem["title"]
        scoring = problem["scoring"]
        min_improvement = problem.get("minImprovement", 1e-4)

        return f'''schema_version = "1.3"

[task]
name = "einsteinarena/{slug}"
description = "{title}"
authors = ["Einstein Arena"]
keywords = ["einsteinarena", "{scoring}", "mathematics"]

[metadata]
difficulty = "hard"
category = "mathematics"
tags = ["{scoring}", "optimization"]

[environment]
network_mode = "none"
build_timeout_sec = 900.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
gpus = 0
mcp_servers = []

[environment.env]

[agent]
timeout_sec = 7200.0

[verifier]
timeout_sec = 600.0

[verifier.env]

[solution.env]
'''

    def generate_instruction_md(self, problem: dict) -> str:
        """生成 instruction.md"""
        description = problem["description"]
        solution_schema = problem["solutionSchema"]
        scoring = problem["scoring"]

        # 添加 solution schema 说明
        schema_section = "\n\n## Solution Format\n\n"
        schema_section += (
            "Submit a JSON file named `solution.json` with the following structure:\n\n"
        )
        schema_section += "```json\n{\n"
        for key, desc in solution_schema.items():
            schema_section += f'  "{key}": // {desc}\n'
        schema_section += "}\n```\n"

        scoring_section = f"\n\n## Scoring Direction\n\n**{scoring.upper()}**\n"
        scoring_section += "\nThe verifier will evaluate your solution and return a numerical score.\n"

        return description + schema_section + scoring_section

    def generate_test_sh(self, problem: dict) -> str:
        """生成 tests/test.sh（包装 verifier）"""
        verifier_code = problem["verifier"]

        return f'''#!/bin/bash
set -euo pipefail

# Einstein Arena verifier wrapper
SOLUTION_FILE="/workspace/solution.json"
SCORE_FILE="/workspace/score.txt"

if [ ! -f "$SOLUTION_FILE" ]; then
    echo "ERROR: solution.json not found at $SOLUTION_FILE" >&2
    exit 1
fi

# Create verifier script
cat > /tmp/verifier.py << 'VERIFIER_EOF'
{verifier_code}

# Wrapper to read from file and handle errors
if __name__ == "__main__":
    import json
    import sys

    try:
        with open("/workspace/solution.json", "r") as f:
            data = json.load(f)

        score = evaluate(data)

        # Write score to file
        with open("/workspace/score.txt", "w") as f:
            f.write(str(score))

        print(f"Score: {{score}}")
        sys.exit(0)

    except Exception as e:
        print(f"Verifier failed: {{e}}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
VERIFIER_EOF

# Run verifier
python3 /tmp/verifier.py
EXIT_CODE=$?

if [ -f "$SCORE_FILE" ]; then
    echo "Verification complete. Score: $(cat $SCORE_FILE)"
else
    echo "ERROR: Verifier did not produce a score" >&2
fi

exit $EXIT_CODE
'''

    def generate_dockerfile(self, problem: dict) -> str:
        """生成 environment/Dockerfile"""
        verifier = problem["verifier"]

        # 检查是否需要 decimal 模块（内置，但注释中说明）
        needs_decimal = "from decimal import" in verifier or "Decimal" in verifier

        base = "FROM python:3.11-slim\n\n"
        deps = "RUN pip install --no-cache-dir numpy\n"
        if needs_decimal:
            deps += "# Note: decimal module is built-in\n"
        deps += "\n"
        workdir = "WORKDIR /workspace\n"

        return base + deps + workdir


class HarborWriter:
    """写入 Harbor 格式文件"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def write_task(self, slug: str, files: dict[str, str]) -> None:
        """写入完整的任务目录"""
        task_dir = self.output_dir / slug
        task_dir.mkdir(parents=True, exist_ok=True)

        # task.toml
        (task_dir / "task.toml").write_text(files["task_toml"])

        # instruction.md
        (task_dir / "instruction.md").write_text(files["instruction_md"])

        # environment/Dockerfile
        env_dir = task_dir / "environment"
        env_dir.mkdir(exist_ok=True)
        (env_dir / "Dockerfile").write_text(files["dockerfile"])

        # tests/test.sh
        tests_dir = task_dir / "tests"
        tests_dir.mkdir(exist_ok=True)
        test_sh = tests_dir / "test.sh"
        test_sh.write_text(files["test_sh"])
        test_sh.chmod(0o755)  # Make executable

        print(f"  ✓ Written: {slug}")


def main():
    """Main extraction logic"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract Einstein Arena tasks to Harbor format"
    )
    parser.add_argument(
        "--output",
        default="benchmarks/einsteinarena-harbor",
        help="Output directory (default: benchmarks/einsteinarena-harbor)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing files",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)

    print(f"Einstein Arena Task Extractor")
    print(f"=" * 60)
    print(f"API Base: {API_BASE}")
    print(f"Output: {output_dir}")
    print(f"Dry run: {args.dry_run}")
    print()

    api = APIClient()
    converter = HarborConverter()
    writer = HarborWriter(output_dir)

    # Fetch all problems
    print("Fetching problem list...")
    try:
        all_problems = api.list_problems()
    except Exception as e:
        print(f"ERROR: Failed to fetch problem list: {e}", file=sys.stderr)
        return 1

    print(f"Found {len(all_problems)} problems in API")

    # Filter to webpage tasks only
    tasks_to_extract = [p for p in all_problems if p["slug"] in WEBPAGE_TASKS]
    print(f"Extracting {len(tasks_to_extract)}/{len(WEBPAGE_TASKS)} webpage-visible tasks")
    print()

    if len(tasks_to_extract) < len(WEBPAGE_TASKS):
        missing = set(WEBPAGE_TASKS) - {p["slug"] for p in tasks_to_extract}
        print(f"WARNING: {len(missing)} tasks not found in API: {missing}")
        print()

    success_count = 0
    error_count = 0

    for problem_summary in tasks_to_extract:
        slug = problem_summary["slug"]

        try:
            # Get full details
            problem = api.get_problem(slug)

            # Add slug to problem details (API doesn't return it)
            problem["slug"] = slug

            # Convert to Harbor format
            files = {
                "task_toml": converter.generate_task_toml(problem),
                "instruction_md": converter.generate_instruction_md(problem),
                "test_sh": converter.generate_test_sh(problem),
                "dockerfile": converter.generate_dockerfile(problem),
            }

            if args.dry_run:
                print(f"  [DRY RUN] Would write: {slug}")
            else:
                writer.write_task(slug, files)

            success_count += 1

        except Exception as e:
            print(f"  ✗ ERROR: {slug}: {e}", file=sys.stderr)
            error_count += 1

    print()
    print(f"=" * 60)
    print(f"Extraction complete:")
    print(f"  Success: {success_count}/{len(tasks_to_extract)}")
    print(f"  Errors:  {error_count}")

    if not args.dry_run and success_count > 0:
        print()
        print(f"Output written to: {output_dir.absolute()}")
        print()
        print("Next steps:")
        print("  1. Review the generated files")
        print("  2. Implement workflow: factory/workflow/contributed/einsteinarena/workflow.py")
        print("  3. Test a single task: benchmarks/run.sh einsteinarena circles-rectangle")

    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
