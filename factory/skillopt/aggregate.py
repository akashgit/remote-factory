"""Hierarchical tree-structured patch merging."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import structlog

from factory.skillopt.reflect import _call_llm
from factory.skillopt.types import Edit, Patch, RawPatch

log = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text()


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def _parse_patch(data: dict) -> Patch:
    edits = [
        Edit(
            op=e.get("op", "append"),
            content=e.get("content", ""),
            target=e.get("target", ""),
            support_count=e.get("support_count"),
            source_type=e.get("source_type"),
        )
        for e in data.get("edits", [])
    ]
    return Patch(edits=edits, reasoning=data.get("reasoning", ""))


def _merge_batch(skill: str, patches: list[Patch], system_prompt: str) -> Patch:
    patches_json = json.dumps(
        [p.model_dump() for p in patches],
        indent=2,
    )
    prompt = (
        system_prompt
        .replace("{{SKILL_CONTENT}}", skill)
        .replace("{{PATCHES}}", patches_json)
    )
    raw = _call_llm(prompt, timeout=300)
    if not raw:
        log.warning("merge LLM returned nothing, returning first patch")
        return patches[0] if patches else Patch(edits=[])
    parsed = _extract_json(raw)
    if not parsed:
        log.warning("merge LLM parse failed, returning first patch")
        return patches[0] if patches else Patch(edits=[])
    return _parse_patch(parsed)


def _hierarchical_merge(
    skill: str,
    patches: list[Patch],
    system_prompt: str,
    batch_size: int = 2,
    workers: int = 4,
) -> Patch:
    if not patches:
        return Patch(edits=[])
    if len(patches) == 1:
        return patches[0]

    current_level = list(patches)
    while len(current_level) > 1:
        batches = [
            current_level[i:i + batch_size]
            for i in range(0, len(current_level), batch_size)
        ]
        next_level: list[Patch] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_merge_batch, skill, batch, system_prompt): i
                for i, batch in enumerate(batches)
            }
            results: dict[int, Patch] = {}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    log.warning("merge batch failed", idx=idx, error=str(exc))
                    results[idx] = batches[idx][0]
            for i in range(len(batches)):
                next_level.append(results.get(i, batches[i][0]))
        current_level = next_level
        log.info("merge level done", remaining=len(current_level))

    return current_level[0]


def merge_patches(
    skill: str,
    failure_patches: list[RawPatch],
    success_patches: list[RawPatch],
    workers: int = 4,
) -> Patch:
    failure_prompt = _load_prompt("merge_failure.md")
    success_prompt = _load_prompt("merge_success.md")
    final_prompt = _load_prompt("merge_final.md")

    failure_ps = [rp.patch for rp in failure_patches]
    success_ps = [rp.patch for rp in success_patches]

    log.info(
        "merging patches",
        failure_patches=len(failure_ps),
        success_patches=len(success_ps),
    )

    merged_failure = _hierarchical_merge(skill, failure_ps, failure_prompt, workers=workers)
    merged_success = _hierarchical_merge(skill, success_ps, success_prompt, workers=workers)

    if not merged_failure.edits and not merged_success.edits:
        return Patch(edits=[])
    if not merged_failure.edits:
        return merged_success
    if not merged_success.edits:
        return merged_failure

    failure_json = json.dumps(merged_failure.model_dump(), indent=2)
    success_json = json.dumps(merged_success.model_dump(), indent=2)
    prompt = (
        final_prompt
        .replace("{{SKILL_CONTENT}}", skill)
        .replace("{{FAILURE_PATCH}}", failure_json)
        .replace("{{SUCCESS_PATCH}}", success_json)
    )
    raw = _call_llm(prompt, timeout=300)
    if not raw:
        log.warning("final merge LLM failed, returning failure patch")
        return merged_failure
    parsed = _extract_json(raw)
    if not parsed:
        return merged_failure
    return _parse_patch(parsed)
