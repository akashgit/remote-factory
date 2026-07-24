#!/usr/bin/env python3
"""Generate Harbor-compatible task directories from SearchQA JSONL data."""

import argparse
import json
import stat
import textwrap
from pathlib import Path
from typing import Any


def generate_instruction(question: str, context: str) -> str:
    return (
        "Answer the following question using the provided search results.\n"
        "\n"
        "## Question\n"
        f"{question}\n"
        "\n"
        "## Search Results\n"
        f"{context}\n"
        "\n"
        "## Instructions\n"
        "Provide your answer inside <answer> tags. Example: <answer>Paris</answer>\n"
        "Write your answer to /workspace/answer.txt\n"
    )


def generate_task_toml(question_id: str) -> str:
    return textwrap.dedent(f"""\
        schema_version = "1.3"

        [task]
        name = "searchqa/{question_id}"
        description = "Answer a question using search results"

        [environment]
        network_mode = "none"
        cpus = 1
        memory_mb = 512
        storage_mb = 1024

        [agent]
        timeout_sec = 120.0

        [verifier]
        timeout_sec = 30.0
    """)


DOCKERFILE = textwrap.dedent("""\
    FROM python:3.11-slim
    WORKDIR /workspace
    COPY gold.json /workspace/.gold_answers.json
""")


TEST_SH = textwrap.dedent("""\
    #!/bin/bash
    set -e

    ANSWER_FILE="/workspace/answer.txt"

    if [ ! -f "$ANSWER_FILE" ]; then
      echo "FAIL: No answer found at $ANSWER_FILE"
      exit 1
    fi

    python3 -c "
    import json, re, string, sys

    def normalize(s):
        s = s.lower()
        s = re.sub(r'\\\\b(a|an|the)\\\\b', ' ', s)
        s = ''.join(c for c in s if c not in string.punctuation)
        return ' '.join(s.split())

    answer = open('/workspace/answer.txt').read().strip()
    m = re.search(r'<answer>(.*?)</answer>', answer, re.DOTALL | re.IGNORECASE)
    if m:
        answer = m.group(1).strip()

    gold = json.load(open('/workspace/.gold_answers.json'))
    pred_norm = normalize(answer)
    match = any(normalize(g) == pred_norm for g in gold)
    print(f'Predicted: {answer}')
    print(f'Gold: {gold}')
    print(f'Match: {match}')
    sys.exit(0 if match else 1)
    "
""")


def generate_task(task_dir: Path, item: dict[str, Any]) -> None:
    question_id: str = item["id"]
    question: str = item["question"]
    context: str = item["context"]
    answers: list[str] = item["answers"]

    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "task.toml").write_text(generate_task_toml(question_id))
    (task_dir / "instruction.md").write_text(generate_instruction(question, context))
    (task_dir / "gold.json").write_text(json.dumps(answers) + "\n")

    env_dir = task_dir / "environment"
    env_dir.mkdir(exist_ok=True)
    (env_dir / "Dockerfile").write_text(DOCKERFILE)

    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    test_sh_path = tests_dir / "test.sh"
    test_sh_path.write_text(TEST_SH)
    test_sh_path.chmod(test_sh_path.stat().st_mode | stat.S_IEXEC)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Harbor tasks from SearchQA JSONL")
    parser.add_argument("--input", required=True, help="Path to JSONL file")
    parser.add_argument("--output", required=True, help="Output directory for Harbor tasks")
    parser.add_argument("--limit", type=int, default=None, help="Max number of tasks to generate")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)

    items = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
            if args.limit and len(items) >= args.limit:
                break

    output_dir.mkdir(parents=True, exist_ok=True)

    for item in items:
        task_dir = output_dir / item["id"]
        generate_task(task_dir, item)

    print(f"Generated {len(items)} Harbor tasks in {output_dir}")


if __name__ == "__main__":
    main()
