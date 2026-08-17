"""Slow update — epoch-level longitudinal skill refinement.

At the end of each epoch, compares rollout performance of the same sample set
under the previous epoch's skill vs. the current epoch's skill. An optimizer
analyzes regressions, improvements, and persistent failures, then writes a
free-form guidance block into a protected section of the skill document.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import traceback
from pathlib import Path

import structlog

from factory.skillopt.skill import SLOW_UPDATE_END, SLOW_UPDATE_START
from factory.skillopt.types import RolloutResult

log = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def has_slow_update_field(skill: str) -> bool:
    return SLOW_UPDATE_START in skill and SLOW_UPDATE_END in skill


def inject_empty_slow_update_field(skill: str) -> str:
    if has_slow_update_field(skill):
        return skill
    block = f"\n\n{SLOW_UPDATE_START}\n{SLOW_UPDATE_END}\n"
    return skill.rstrip() + block


def extract_slow_update_field(skill: str) -> str:
    start = skill.find(SLOW_UPDATE_START)
    end = skill.find(SLOW_UPDATE_END)
    if start == -1 or end == -1:
        return ""
    inner_start = start + len(SLOW_UPDATE_START)
    return skill[inner_start:end].strip()


def _strip_all_slow_update_fields(skill: str) -> str:
    while True:
        start = skill.find(SLOW_UPDATE_START)
        if start == -1:
            break
        end = skill.find(SLOW_UPDATE_END, start)
        if end == -1:
            skill = skill[:start] + skill[start + len(SLOW_UPDATE_START):]
            break
        skill = skill[:start] + skill[end + len(SLOW_UPDATE_END):]
    skill = skill.replace(SLOW_UPDATE_END, "")
    while "\n\n\n" in skill:
        skill = skill.replace("\n\n\n", "\n\n")
    return skill.rstrip()


def replace_slow_update_field(skill: str, new_content: str) -> str:
    skill = _strip_all_slow_update_fields(skill)
    block = (
        f"\n\n{SLOW_UPDATE_START}\n"
        f"{new_content.strip()}\n"
        f"{SLOW_UPDATE_END}\n"
    )
    return skill + block


def build_comparison_pairs(
    results_prev: list[RolloutResult],
    results_curr: list[RolloutResult],
) -> list[dict]:
    """Build structured per-sample comparison entries from two rollout sets.

    Items are matched by id. Each entry contains the category of change and
    both results' scores/answers/fail_reasons.
    """
    prev_by_id = {r.id: r for r in results_prev}
    curr_by_id = {r.id: r for r in results_curr}

    all_ids = list(dict.fromkeys(
        [r.id for r in results_prev] + [r.id for r in results_curr]
    ))

    pairs: list[dict] = []
    for tid in all_ids:
        prev = prev_by_id.get(tid)
        curr = curr_by_id.get(tid)
        prev_ok = bool(prev and prev.hard >= 1.0)
        curr_ok = bool(curr and curr.hard >= 1.0)

        if not prev_ok and curr_ok:
            category = "improved"
        elif prev_ok and not curr_ok:
            category = "regressed"
        elif not prev_ok and not curr_ok:
            category = "persistent_fail"
        else:
            category = "stable_success"

        pairs.append({
            "id": tid,
            "category": category,
            "prev": {
                "hard": int(prev_ok),
                "soft": float(prev.soft if prev else 0.0),
                "predicted_answer": prev.extras.get("prediction", "") if prev else "",
                "fail_reason": prev.fail_reason if prev else "",
            },
            "curr": {
                "hard": int(curr_ok),
                "soft": float(curr.soft if curr else 0.0),
                "predicted_answer": curr.extras.get("prediction", "") if curr else "",
                "fail_reason": curr.fail_reason if curr else "",
            },
        })

    return pairs


def format_comparison_text(pairs: list[dict]) -> str:
    by_cat: dict[str, list[dict]] = {
        "regressed": [],
        "persistent_fail": [],
        "improved": [],
        "stable_success": [],
    }
    for p in pairs:
        by_cat.setdefault(p["category"], []).append(p)

    total = len(pairs)
    parts = [
        f"## Longitudinal Comparison Summary\n"
        f"Total samples: {total}\n"
        f"- Improved (wrong->right): {len(by_cat['improved'])}\n"
        f"- Regressed (right->wrong): {len(by_cat['regressed'])}\n"
        f"- Persistent failures (wrong->wrong): {len(by_cat['persistent_fail'])}\n"
        f"- Stable successes (right->right): {len(by_cat['stable_success'])}\n"
    ]

    categories = [
        ("regressed", "Regressions (right->wrong) — HIGHEST PRIORITY"),
        ("persistent_fail", "Persistent Failures (wrong->wrong)"),
        ("improved", "Improvements (wrong->right)"),
        ("stable_success", "Stable Successes (right->right)"),
    ]

    for cat_key, label in categories:
        entries = by_cat[cat_key]
        if not entries:
            parts.append(f"### {label}\n(none)\n")
            continue

        lines = [f"### {label}"]
        for e in entries:
            prev = e["prev"]
            curr = e["curr"]
            lines.append(
                f"\n#### Task {e['id']}\n"
                f"- Prev epoch: {'PASS' if prev['hard'] else 'FAIL'} "
                f"(soft={prev['soft']:.2f}) — answer: {prev['predicted_answer']}\n"
                f"- Curr epoch: {'PASS' if curr['hard'] else 'FAIL'} "
                f"(soft={curr['soft']:.2f}) — answer: {curr['predicted_answer']}"
            )
            if curr.get("fail_reason"):
                lines.append(f"- Curr fail reason: {curr['fail_reason']}")
            if prev.get("fail_reason") and not prev["hard"]:
                lines.append(f"- Prev fail reason: {prev['fail_reason']}")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _call_llm(prompt: str, timeout: int = 600) -> str | None:
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
        log.warning("slow update LLM call failed", error=str(exc))
    return None


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def run_slow_update(
    skill_content: str,
    prev_skill: str,
    results_prev: list[RolloutResult],
    results_curr: list[RolloutResult],
    prev_slow_update_content: str = "",
) -> dict | None:
    """Run the slow update optimizer for one epoch boundary.

    Returns {"reasoning": str, "slow_update_content": str} or None on failure.
    """
    system_prompt = (_PROMPTS_DIR / "slow_update.md").read_text()

    pairs = build_comparison_pairs(results_prev, results_curr)
    comparison_text = format_comparison_text(pairs)

    prev_guidance_section = (
        prev_slow_update_content.strip()
        if prev_slow_update_content and prev_slow_update_content.strip()
        else "(No previous guidance — this is the first slow update.)"
    )

    user_prompt = (
        f"{system_prompt}\n\n"
        f"## Previous Epoch's Skill\n{prev_skill}\n\n"
        f"## Current Epoch's Skill\n{skill_content}\n\n"
        f"## Previous Slow Update Guidance\n"
        f"The following guidance was active during the current epoch. "
        f"Reflect on its effectiveness before writing the new version.\n\n"
        f"{prev_guidance_section}\n\n"
        f"## Longitudinal Comparison (same tasks, two skill versions)\n"
        f"{comparison_text}"
    )

    try:
        response = _call_llm(user_prompt)
        if not response:
            return None
        result = _extract_json(response)
        if result and result.get("slow_update_content"):
            return {
                "reasoning": str(result.get("reasoning", "")).strip(),
                "slow_update_content": str(result["slow_update_content"]).strip(),
            }
    except Exception:  # noqa: BLE001
        traceback.print_exc()

    return None
