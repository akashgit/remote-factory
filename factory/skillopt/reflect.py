"""Per-trace reflection — analyze each benchmark trace and suggest prompt edits."""
from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import structlog

from factory.skillopt.models import TraceReflection

log = structlog.get_logger()

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "langfuse"


def _import_langfuse_helpers():
    """Import langfuse helper modules by adding scripts/langfuse to sys.path."""
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    from langfuse_client import fetch_trace  # type: ignore[import-not-found]
    from pull_langfuse_trace import (  # type: ignore[import-not-found]
        extract_factory_commands,
        extract_orchestration,
        print_report,
    )
    return fetch_trace, extract_orchestration, extract_factory_commands, print_report


def _format_trace_dump(trace: dict) -> str:
    """Format a Langfuse trace into a human-readable dump."""
    _, extract_orchestration, extract_factory_commands, print_report = _import_langfuse_helpers()
    timeline, ceo_reasoning = extract_orchestration(trace, full=True)
    factory_commands = extract_factory_commands(trace)
    buf = io.StringIO()
    print_report(timeline, ceo_reasoning, factory_commands, file=buf)
    return buf.getvalue()


def _call_llm(prompt: str, timeout: int = 180) -> str | None:
    """Call claude -p with the given prompt and return stdout."""
    if not shutil.which("claude"):
        log.warning("claude CLI not found, skipping LLM call")
        return None
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("LLM call failed", error=str(exc))
    return None


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from LLM output."""
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def _read_prompt_template(workflow_file: str, node_id: str) -> str | None:
    """Extract the prompt_template string for a given node from a workflow file."""
    path = Path(workflow_file)
    if not path.exists():
        log.error("workflow file not found", path=workflow_file)
        return None
    source = path.read_text()
    pattern = re.compile(
        rf'nodes\["{re.escape(node_id)}"\]\s*=\s*AgentNode\([^)]*prompt_template\s*=\s*\(',
        re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        pattern2 = re.compile(
            rf'nodes\["{re.escape(node_id)}"\]\s*=\s*AgentNode\([^)]*prompt_template\s*=',
            re.DOTALL,
        )
        match = pattern2.search(source)
    if not match:
        log.error("prompt_template not found for node", node_id=node_id)
        return None

    start = match.end()
    depth = 0
    in_string = False
    escape_next = False
    i = start
    while i < len(source):
        ch = source[i]
        if escape_next:
            escape_next = False
            i += 1
            continue
        if ch == "\\":
            escape_next = True
            i += 1
            continue
        if ch == '"' and not in_string:
            in_string = True
        elif ch == '"' and in_string:
            in_string = False
        if not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
        i += 1

    raw = source[match.start():i + 1]
    try:
        local_ns: dict = {}
        exec(f"_val = {raw.split('prompt_template=')[1].strip().rstrip(',')}", {}, local_ns)
        return local_ns.get("_val", "")
    except Exception:
        lines = source[start:i].strip().strip("()")
        return lines


def reflect_trace(
    result_json: dict,
    current_prompt_template: str,
    trace: dict,
) -> TraceReflection | None:
    """Analyze a single benchmark trace and produce a reflection.

    Args:
        result_json: The benchmark result dict (instance_id, resolved, benchmark, details).
        current_prompt_template: The current prompt_template text driving the agent.
        trace: The full Langfuse trace dict.

    Returns:
        A TraceReflection or None if LLM call fails.
    """
    instance_id = result_json.get("instance_id", "unknown")
    benchmark = result_json.get("benchmark", "unknown")
    resolved = result_json.get("resolved", False)
    trace_id = (result_json.get("details") or {}).get("trace_id", "unknown")

    trace_dump = _format_trace_dump(trace)

    outcome = "SUCCEEDED" if resolved else "FAILED"
    action = "reinforced what worked here" if resolved else "helped avoid this failure"

    prompt = (
        f"Here is the trace of a benchmark task that {outcome}.\n\n"
        f"Benchmark: {benchmark}\nInstance: {instance_id}\n\n"
        f"Here is the current prompt_template that drove the agent:\n"
        f"<prompt_template>\n{current_prompt_template}\n</prompt_template>\n\n"
        f"Here is the execution trace:\n<trace>\n{trace_dump[:15000]}\n</trace>\n\n"
        f"Analyze this trace. What specific edit to the prompt_template would have "
        f"{action}?\n\n"
        f"Output ONLY a JSON object with these fields:\n"
        f'{{\n'
        f'  "instance_id": "{instance_id}",\n'
        f'  "resolved": {str(resolved).lower()},\n'
        f'  "diagnosis": "what happened and why",\n'
        f'  "suggested_edit": "specific text change to prompt_template",\n'
        f'  "edit_type": "add_rule|modify_rule|remove_rule|reword_section",\n'
        f'  "confidence": 0.0-1.0\n'
        f"}}\n"
    )

    raw = _call_llm(prompt, timeout=180)
    if not raw:
        log.warning("LLM returned no output for reflection", instance_id=instance_id)
        return None

    parsed = _extract_json(raw)
    if not parsed:
        log.warning("failed to parse LLM JSON", instance_id=instance_id, raw=raw[:200])
        return None

    try:
        return TraceReflection(
            instance_id=parsed.get("instance_id", instance_id),
            benchmark=benchmark,
            resolved=resolved,
            trace_id=trace_id,
            diagnosis=parsed.get("diagnosis", ""),
            suggested_edit=parsed.get("suggested_edit", ""),
            edit_type=parsed.get("edit_type", "modify_rule"),
            confidence=float(parsed.get("confidence", 0.5)),
        )
    except Exception as exc:
        log.warning("failed to construct TraceReflection", error=str(exc))
        return None


def reflect_all(
    results_dir: str,
    workflow_file: str,
    node_id: str,
) -> list[TraceReflection]:
    """Reflect on all benchmark result files in a directory.

    Args:
        results_dir: Path to directory containing benchmark result JSON files.
        workflow_file: Path to the workflow .py file.
        node_id: The AgentNode id whose prompt_template to analyze.

    Returns:
        List of TraceReflection objects (one per successfully analyzed trace).
    """
    fetch_trace_fn = _import_langfuse_helpers()[0]

    prompt_template = _read_prompt_template(workflow_file, node_id)
    if not prompt_template:
        log.error("could not extract prompt_template", workflow_file=workflow_file, node_id=node_id)
        return []

    results_path = Path(results_dir)
    if not results_path.is_dir():
        log.error("results directory not found", path=results_dir)
        return []

    result_files = sorted(results_path.glob("*.json"))
    log.info("found result files", count=len(result_files))

    reflections: list[TraceReflection] = []
    for rf in result_files:
        try:
            result_data = json.loads(rf.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("skipping invalid result file", path=str(rf), error=str(exc))
            continue

        trace_id = (result_data.get("details") or {}).get("trace_id")
        if not trace_id:
            log.warning("no trace_id in result", path=str(rf))
            continue

        try:
            trace = fetch_trace_fn(trace_id, use_cache=True)
        except Exception as exc:
            log.warning("failed to fetch trace", trace_id=trace_id, error=str(exc))
            continue

        reflection = reflect_trace(result_data, prompt_template, trace)
        if reflection:
            reflections.append(reflection)
            log.info(
                "reflected on trace",
                instance_id=result_data.get("instance_id"),
                resolved=result_data.get("resolved"),
            )

    log.info("reflection complete", total=len(reflections))
    return reflections
