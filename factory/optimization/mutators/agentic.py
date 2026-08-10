"""AgenticMutator — LLM-powered prompt optimization via invoke_agent('strategist').

Reflects on benchmark failures and proposes targeted prompt slot edits.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import structlog

from factory.optimization.surface import Surface
from factory.optimization.types import ExecutionResult, Patch, SlotEdit, StepRecord

log = structlog.get_logger()


class AgenticMutator:
    """Proposes prompt-slot edits by reflecting on benchmark failures via the strategist agent."""

    def __init__(
        self,
        project_path: Path,
        num_failures_to_show: int = 5,
        model: str | None = None,
    ) -> None:
        self.project_path = Path(project_path)
        self.num_failures_to_show = num_failures_to_show
        self.model = model

    def propose(
        self,
        surface: Surface,
        execution_result: ExecutionResult,
        history: list[StepRecord],
    ) -> Patch:
        failed = [t for t in execution_result.task_results if t.reward == 0]
        if not failed:
            log.info("mutator.agentic.no_failures", total_tasks=len(execution_result.task_results))
            return Patch(reasoning="no failures to reflect on")

        prompt = self._build_prompt(surface, failed[:self.num_failures_to_show], history)
        log.info(
            "mutator.agentic.invoke",
            num_failures=len(failed),
            num_shown=min(len(failed), self.num_failures_to_show),
        )

        try:
            from factory.agents.runner import invoke_agent

            stdout, returncode = asyncio.run(
                invoke_agent(
                    "strategist",
                    task=prompt,
                    project_path=self.project_path,
                    model=self.model or "sonnet",
                    timeout=120,
                )
            )
        except Exception as exc:
            log.error("mutator.agentic.invoke_failed", error=str(exc))
            return Patch(reasoning=f"agent invocation failed: {exc}")

        if returncode != 0:
            log.warning("mutator.agentic.nonzero_exit", returncode=returncode)
            return Patch(reasoning=f"agent invocation failed: exit code {returncode}")

        current_skill = surface.prompt_slots.get("skill", "")
        return self._parse_response(stdout, current_skill)

    def _build_prompt(
        self,
        surface: Surface,
        failed: list,
        history: list[StepRecord],
    ) -> str:
        parts: list[str] = []
        parts.append("You are optimizing a question-answering prompt by adding rules. Analyze the failures below and propose NEW RULES to add.")
        parts.append("")
        parts.append("CRITICAL: You are ONLY adding rules. The base prompt (instructions to read the question, write the answer file, use <answer> tags) is FIXED and will NOT be changed. Your rules will be APPENDED to the existing prompt.")
        parts.append("")

        parts.append("## Current Prompt")
        for name, value in surface.prompt_slots.items():
            parts.append(f"### {name}")
            parts.append(value)
            parts.append("")

        parts.append("## Failed Tasks")
        for t in failed:
            parts.append(f"### Task: {t.task_id[:12]}")
            if t.question:
                parts.append(f"**Question:** {t.question}")
            parts.append(f"**Predicted:** {t.predicted}")
            parts.append(f"**Gold:** {t.gold}")
            parts.append("")

        recent = history[-3:] if len(history) >= 3 else history
        if recent:
            parts.append("## Recent History")
            for rec in recent:
                delta = f"{rec.score_delta:+.4f}" if rec.score_delta is not None else "n/a"
                parts.append(
                    f"- Step {rec.step_number}: {rec.score_start:.4f} → {rec.score_end:.4f} (delta {delta}, {rec.verdict})"
                    if rec.score_start is not None and rec.score_end is not None
                    else f"- Step {rec.step_number}: delta {delta}, {rec.verdict}"
                )
            parts.append("")

        parts.append("## Response Format")
        parts.append("Respond with a JSON object. The 'rules' field is a list of short, specific rules to APPEND to the prompt. Each rule should address a specific failure pattern seen above.")
        parts.append('```json')
        parts.append('{"rules": ["rule 1 text", "rule 2 text", ...], "reasoning": "<why these rules will fix the failures>"}')
        parts.append('```')

        return "\n".join(parts)

    def _parse_response(self, stdout: str, current_skill: str = "") -> Patch:
        parsed = _parse_json(stdout)
        if parsed is None:
            log.warning("mutator.agentic.parse_failed", stdout_len=len(stdout))
            return Patch(reasoning="agent invocation failed: could not parse JSON from response")

        reasoning = parsed.get("reasoning", "")

        # New format: {"rules": ["rule1", "rule2"], "reasoning": "..."}
        rules = parsed.get("rules", [])
        if rules and isinstance(rules, list):
            rules_block = "\n## Learned Rules\n" + "\n".join(
                f"- {r}" for r in rules if isinstance(r, str) and r.strip()
            ) + "\n"
            new_skill = current_skill.rstrip() + "\n" + rules_block
            slot_edits = [SlotEdit(slot_name="skill", old_value=current_skill, new_value=new_skill)]
            log.info("mutator.agentic.parsed", num_rules=len(rules), reasoning=reasoning[:80])
            return Patch(prompt_edits=slot_edits, reasoning=reasoning)

        # Legacy format: {"edits": [{"slot": ..., "old": ..., "new": ...}]}
        edits_raw = parsed.get("edits", [])
        slot_edits: list[SlotEdit] = []
        for e in edits_raw:
            if isinstance(e, dict) and "slot" in e:
                slot_edits.append(
                    SlotEdit(
                        slot_name=e["slot"],
                        old_value=e.get("old", ""),
                        new_value=e.get("new", ""),
                    )
                )

        log.info("mutator.agentic.parsed", num_edits=len(slot_edits), reasoning=reasoning[:80])
        return Patch(prompt_edits=slot_edits, reasoning=reasoning)


_VALID_KEYS = {"edits", "reasoning", "rules"}


def _parse_json(text: str) -> dict | None:
    """Three-tier JSON extraction: direct parse → markdown code blocks → regex search."""
    def _looks_valid(d: object) -> bool:
        return isinstance(d, dict) and bool(_VALID_KEYS & d.keys())

    try:
        result = json.loads(text)
        if _looks_valid(result):
            return result
    except (json.JSONDecodeError, ValueError):
        pass

    code_block = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if code_block:
        try:
            result = json.loads(code_block.group(1))
            if _looks_valid(result):
                return result
        except (json.JSONDecodeError, ValueError):
            pass

    for match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text):
        try:
            result = json.loads(match.group())
            if _looks_valid(result):
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    return None
