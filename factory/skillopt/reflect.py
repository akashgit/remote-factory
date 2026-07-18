"""Minibatch reflection — analyze batches of traces and produce structured patches."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

import structlog

from factory.skillopt.types import (
    Edit,
    FailureSummaryEntry,
    Patch,
    RawPatch,
    RolloutResult,
)

log = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text()


def _call_llm(prompt: str, timeout: int = 300) -> str | None:
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
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def fmt_trajectory(trace_data: dict) -> str:
    parts = [f"ID: {trace_data.get('id', 'unknown')}"]
    if trace_data.get("fail_reason"):
        parts.append(f"Failure: {trace_data['fail_reason']}")
    if trace_data.get("trace_dump"):
        parts.append(f"Trace:\n{trace_data['trace_dump'][:8000]}")
    return "\n".join(parts)


def fmt_minibatch_trajectories(items: list[RolloutResult]) -> str:
    sections: list[str] = []
    for i, item in enumerate(items):
        header = f"--- Trace {i + 1}/{len(items)} (id={item.id}, hard={item.hard}) ---"
        trace_dump = item.extras.get("trace_dump", "")
        fail_info = f"Failure: {item.fail_reason}" if item.fail_reason else ""
        body = trace_dump[:8000] if trace_dump else "(no trace data)"
        sections.append(f"{header}\n{fail_info}\n{body}")
    return "\n\n".join(sections)


def _parse_raw_patch(
    data: dict, source_type: Literal["failure", "success"], batch_size: int,
) -> RawPatch | None:
    try:
        patch_data = data.get("patch", data)
        edits_raw = patch_data.get("edits", [])
        edits = [
            Edit(
                op=e.get("op", "append"),
                content=e.get("content", ""),
                target=e.get("target", ""),
                support_count=e.get("support_count"),
                source_type=e.get("source_type", source_type),
            )
            for e in edits_raw
        ]
        patch = Patch(
            edits=edits,
            reasoning=patch_data.get("reasoning", ""),
        )
        failure_summary = [
            FailureSummaryEntry(**fs)
            for fs in data.get("failure_summary", [])
        ]
        return RawPatch(
            patch=patch,
            source_type=source_type,
            batch_size=batch_size,
            failure_summary=failure_summary,
        )
    except Exception as exc:
        log.warning("failed to parse raw patch", error=str(exc))
        return None


def _parse_slot_edits_to_raw_patch(
    data: dict,
    source_type: Literal["failure", "success"],
    batch_size: int,
    prompt_slots: dict[str, str],
) -> RawPatch | None:
    """Parse SlotEdit-style LLM output into a RawPatch with replace Edit objects."""
    try:
        patch_data = data.get("patch", data)
        edits_raw = patch_data.get("edits", [])
        edits: list[Edit] = []
        for e in edits_raw:
            slot_name = e.get("slot_name", "")
            new_value = e.get("new_value", "")
            old_value = prompt_slots.get(slot_name, "")
            if not old_value or not new_value or old_value == new_value:
                continue
            edits.append(Edit(
                op="replace",
                target=old_value,
                content=new_value,
                support_count=e.get("support_count"),
                source_type=source_type,
            ))
        patch = Patch(
            edits=edits,
            reasoning=patch_data.get("reasoning", ""),
        )
        failure_summary = [
            FailureSummaryEntry(**fs)
            for fs in data.get("failure_summary", [])
        ]
        return RawPatch(
            patch=patch,
            source_type=source_type,
            batch_size=batch_size,
            failure_summary=failure_summary,
        )
    except Exception as exc:
        log.warning("failed to parse slot edits", error=str(exc))
        return None


def run_error_analyst_minibatch(
    skill_content: str,
    items: list[RolloutResult],
    edit_budget: int = 5,
    step_buffer_context: str = "",
    prompt_slots: dict[str, str] | None = None,
    prompt_slots_text: str | None = None,
    learning_rate: int = 10,
) -> RawPatch | None:
    template = _load_prompt("analyst_error.md")
    traces_text = fmt_minibatch_trajectories(items)

    if prompt_slots is not None and prompt_slots_text is not None:
        prompt = (
            template
            .replace("{{PROMPT_SLOTS}}", prompt_slots_text)
            .replace("{{TRACES}}", traces_text)
            .replace("{{BATCH_SIZE}}", str(len(items)))
            .replace("{{EDIT_BUDGET}}", str(edit_budget))
            .replace("{{LEARNING_RATE}}", str(learning_rate))
        )
    else:
        prompt = (
            template
            .replace("{{SKILL_CONTENT}}", skill_content)
            .replace("{{TRACES}}", traces_text)
            .replace("{{BATCH_SIZE}}", str(len(items)))
            .replace("{{EDIT_BUDGET}}", str(edit_budget))
            .replace("{{LEARNING_RATE}}", str(learning_rate))
        )

    if step_buffer_context:
        prompt += "\n\n" + step_buffer_context
    raw = _call_llm(prompt)
    if not raw:
        return None
    parsed = _extract_json(raw)
    if not parsed:
        log.warning("failed to parse error analyst JSON")
        return None

    if prompt_slots is not None:
        return _parse_slot_edits_to_raw_patch(parsed, "failure", len(items), prompt_slots)
    return _parse_raw_patch(parsed, "failure", len(items))


def run_success_analyst_minibatch(
    skill_content: str,
    items: list[RolloutResult],
    edit_budget: int = 5,
    step_buffer_context: str = "",
    prompt_slots: dict[str, str] | None = None,
    prompt_slots_text: str | None = None,
    learning_rate: int = 10,
) -> RawPatch | None:
    template = _load_prompt("analyst_success.md")
    traces_text = fmt_minibatch_trajectories(items)

    if prompt_slots is not None and prompt_slots_text is not None:
        prompt = (
            template
            .replace("{{PROMPT_SLOTS}}", prompt_slots_text)
            .replace("{{TRACES}}", traces_text)
            .replace("{{BATCH_SIZE}}", str(len(items)))
            .replace("{{EDIT_BUDGET}}", str(edit_budget))
            .replace("{{LEARNING_RATE}}", str(learning_rate))
        )
    else:
        prompt = (
            template
            .replace("{{SKILL_CONTENT}}", skill_content)
            .replace("{{TRACES}}", traces_text)
            .replace("{{BATCH_SIZE}}", str(len(items)))
            .replace("{{EDIT_BUDGET}}", str(edit_budget))
            .replace("{{LEARNING_RATE}}", str(learning_rate))
        )

    if step_buffer_context:
        prompt += "\n\n" + step_buffer_context
    raw = _call_llm(prompt)
    if not raw:
        return None
    parsed = _extract_json(raw)
    if not parsed:
        log.warning("failed to parse success analyst JSON")
        return None

    if prompt_slots is not None:
        return _parse_slot_edits_to_raw_patch(parsed, "success", len(items), prompt_slots)
    return _parse_raw_patch(parsed, "success", len(items))


def run_minibatch_reflect(
    results: list[RolloutResult],
    skill_content: str,
    minibatch_size: int = 4,
    edit_budget: int = 5,
    workers: int = 4,
    step_buffer_context: str = "",
    prompt_slots: dict[str, str] | None = None,
    prompt_slots_text: str | None = None,
    learning_rate: int = 10,
) -> list[RawPatch]:
    failures = [r for r in results if r.hard < 1.0]
    successes = [r for r in results if r.hard >= 1.0]

    log.info(
        "reflect: splitting results",
        failures=len(failures),
        successes=len(successes),
        minibatch_size=minibatch_size,
    )

    def _chunk(lst: list, size: int) -> list[list]:
        return [lst[i:i + size] for i in range(0, len(lst), size)]

    failure_batches = _chunk(failures, minibatch_size)
    success_batches = _chunk(successes, minibatch_size)

    patches: list[RawPatch] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for batch in failure_batches:
            f = pool.submit(
                run_error_analyst_minibatch, skill_content, batch, edit_budget,
                step_buffer_context, prompt_slots, prompt_slots_text, learning_rate,
            )
            futures[f] = "failure"
        for batch in success_batches:
            f = pool.submit(
                run_success_analyst_minibatch, skill_content, batch, edit_budget,
                step_buffer_context, prompt_slots, prompt_slots_text, learning_rate,
            )
            futures[f] = "success"

        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
                if result:
                    patches.append(result)
                    log.info("minibatch reflect done", source=source, edits=len(result.patch.edits))
            except Exception as exc:
                log.warning("minibatch reflect failed", source=source, error=str(exc))

    log.info("reflect complete", total_patches=len(patches))
    return patches
