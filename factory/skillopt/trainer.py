"""DL-style training loop for SKILL.md optimization."""
from __future__ import annotations

import json
from pathlib import Path

import structlog

from factory.skillopt.adapter import EnvAdapter
from factory.skillopt.aggregate import merge_patches
from factory.skillopt.clip import rank_and_select
from factory.skillopt.gate import evaluate_gate, select_gate_score
from factory.skillopt.skill import apply_patch
from factory.skillopt.types import GateResult, Patch, RolloutResult
from factory.skillopt.yaml_surface import (
    compute_prompt_change_magnitude,
    extract_prompt_slots,
    format_prompt_slots_for_llm,
    load_yaml,
    render_skill_from_slots,
)

log = structlog.get_logger()


class SkillOptTrainer:

    def __init__(
        self,
        adapter: EnvAdapter,
        skill_path: str,
        epochs: int = 3,
        steps_per_epoch: int = 5,
        batch_size: int = 8,
        learning_rate: int = 3,
        eval_split_seed: int = 42,
        metric: str = "hard",
        out_dir: str = ".skillopt",
        overfit: bool = False,
        results_from: str = "",
        annotations_path: str = "",
        workflow_name: str = "",
    ) -> None:
        self.adapter = adapter
        self.skill_path = Path(skill_path)
        self.epochs = epochs
        self.steps_per_epoch = steps_per_epoch
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.eval_split_seed = eval_split_seed
        self.metric = metric
        self.out_dir = Path(out_dir)
        self.overfit = overfit
        self.results_from = Path(results_from) if results_from else None

        self.rejected_edits: list[Patch] = []
        self.best_skill: str = ""
        self.best_score: float = -1.0
        self.best_step: int = 0
        self.current_skill: str = ""
        self.current_score: float = -1.0
        self.global_step: int = 0

        self._workflow_name = workflow_name
        self.yaml_surface: dict | None = None
        self.prompt_slots: dict[str, str] = {}
        self.prompt_slots_text: str = ""
        self._resolve_annotations(annotations_path)

    def _resolve_annotations(self, annotations_path: str) -> None:
        if annotations_path:
            path = Path(annotations_path)
        else:
            path = self.skill_path.parent / (self.skill_path.stem + ".annotations.yaml")
        if path.exists():
            self.yaml_surface = load_yaml(path)
            self.prompt_slots = extract_prompt_slots(self.yaml_surface)
            self.prompt_slots_text = format_prompt_slots_for_llm(self.yaml_surface)
            log.info(
                "loaded YAML annotations",
                path=str(path),
                prompt_slots=len(self.prompt_slots),
            )
        else:
            log.info("no YAML annotations found, using legacy SKILL.md surface", path=str(path))

    def _load_skill(self) -> str:
        return self.skill_path.read_text()

    def _save_skill(self, content: str) -> None:
        self.skill_path.write_text(content)

    def _checkpoint(self, label: str) -> None:
        ckpt_dir = self.out_dir / "checkpoints"
        ckpt_dir.mkdir(parents=True, exist_ok=True)
        (ckpt_dir / f"{label}_skill.md").write_text(self.current_skill)
        if self.best_skill:
            (ckpt_dir / f"{label}_best_skill.md").write_text(self.best_skill)
        state = {
            "global_step": self.global_step,
            "current_score": self.current_score,
            "best_score": self.best_score,
            "best_step": self.best_step,
            "rejected_count": len(self.rejected_edits),
        }
        (ckpt_dir / f"{label}_state.json").write_text(json.dumps(state, indent=2))
        log.info("checkpoint saved", label=label)

    def _compute_score(self, results: list[RolloutResult]) -> tuple[float, float]:
        if not results:
            return 0.0, 0.0
        hard = sum(r.hard for r in results) / len(results)
        soft = sum(r.soft for r in results) / len(results)
        return hard, soft

    def _build_step_buffer_context(self) -> str:
        if not self.rejected_edits:
            return ""
        lines = ["Previously rejected edits (DO NOT re-propose these):"]
        for i, patch in enumerate(self.rejected_edits):
            for edit in patch.edits:
                target = edit.target[:60] if edit.target else edit.content[:60]
                reasoning = patch.reasoning[:100] if patch.reasoning else ""
                lines.append(f"  Rejected: {edit.op} at {target} — {reasoning}")
        result = "\n".join(lines)
        if len(result) > 2000:
            result = result[:1997] + "..."
        return result

    def _load_results(self, path: Path) -> list[RolloutResult]:
        raw = json.loads(path.read_text())
        items = raw if isinstance(raw, list) else raw.get("results", raw.get("items", []))
        return [RolloutResult(**r) for r in items]

    def _validate_edits_target_prompts_only(self, patch: Patch) -> list[str]:
        """Validate that all edits in the patch target known prompt slot values."""
        if not self.prompt_slots:
            return []
        known_values = list(self.prompt_slots.values())
        violations: list[str] = []
        for edit in patch.edits:
            if edit.op == "replace" and edit.target:
                is_prompt = any(
                    edit.target.strip() == kv.strip()
                    or kv.strip().startswith(edit.target.strip()[:200])
                    for kv in known_values
                )
                if not is_prompt:
                    violations.append(f"Edit targets non-prompt content: {edit.target[:80]}...")
        return violations

    def _update_prompt_slots_after_accept(
        self,
        accepted_patch: Patch,
        candidate_slots: dict[str, str] | None = None,
    ) -> None:
        """After accepting edits, update prompt_slots to reflect the new prompt values."""
        if not self.prompt_slots:
            return
        if candidate_slots is not None:
            self.prompt_slots = candidate_slots
        else:
            for edit in accepted_patch.edits:
                if edit.op != "replace" or not edit.target:
                    continue
                for slot_name, slot_value in list(self.prompt_slots.items()):
                    if slot_value == edit.target:
                        self.prompt_slots[slot_name] = edit.content
                        break
        self.prompt_slots_text = format_prompt_slots_for_llm(self._build_updated_yaml_surface())

    def _build_updated_yaml_surface(self) -> dict:
        """Build a YAML surface dict with current prompt slot values for formatting."""
        if not self.yaml_surface:
            return {}
        import copy
        surface = copy.deepcopy(self.yaml_surface)
        for node_id, node in surface.items():
            if not isinstance(node, dict):
                continue
            slots = node.get("slots", {})
            for k in slots:
                if k.startswith("task_prompt_") and k in self.prompt_slots:
                    slots[k] = self.prompt_slots[k]
        return surface

    def train(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.current_skill = self._load_skill()
        self.best_skill = self.current_skill

        log.info(
            "training started",
            epochs=self.epochs,
            steps_per_epoch=self.steps_per_epoch,
            batch_size=self.batch_size,
            learning_rate=self.learning_rate,
            skill_path=str(self.skill_path),
            overfit=self.overfit,
            results_from=str(self.results_from) if self.results_from else "",
            yaml_surface="yes" if self.yaml_surface else "no",
        )

        for epoch in range(self.epochs):
            log.info("epoch started", epoch=epoch + 1, total=self.epochs)

            for step in range(self.steps_per_epoch):
                self.global_step += 1
                log.info(
                    "step started",
                    epoch=epoch + 1,
                    step=step + 1,
                    global_step=self.global_step,
                )

                gate_result = self._run_step(epoch, step)

                if gate_result.action == "reject":
                    log.info("step rejected", global_step=self.global_step)
                else:
                    action = gate_result.action
                    log.info(
                        "step accepted",
                        action=action,
                        score=round(gate_result.current_score, 4),
                        global_step=self.global_step,
                    )

                self._checkpoint(f"epoch{epoch + 1}_step{step + 1}")

            log.info("epoch completed", epoch=epoch + 1)

        self._save_skill(self.best_skill)
        self._checkpoint("final")
        log.info(
            "training complete",
            best_score=round(self.best_score, 4),
            best_step=self.best_step,
            total_steps=self.global_step,
        )

    def _run_step(self, epoch: int, step: int) -> GateResult:
        step_dir = str(self.out_dir / f"epoch{epoch + 1}" / f"step{step + 1}")
        Path(step_dir).mkdir(parents=True, exist_ok=True)

        use_preloaded = (
            self.results_from
            and self.global_step == 1
            and self.results_from.exists()
        )

        if use_preloaded and self.results_from:
            results = self._load_results(self.results_from)
            env = None
            log.info("loaded results from file", path=str(self.results_from), count=len(results))
        else:
            env = self.adapter.build_train_env(self.batch_size, seed=self.global_step)
            self._save_skill(self.current_skill)
            results = self.adapter.rollout(env, self.current_skill, step_dir)
            log.info("rollout complete", results=len(results))

        hard_before, soft_before = self._compute_score(results)
        if self.current_score < 0:
            self.current_score = select_gate_score(hard_before, soft_before, self.metric)
            self.best_score = self.current_score

        step_buffer_context = self._build_step_buffer_context() if self.overfit else ""

        reflect_kwargs: dict = {
            "minibatch_size": max(1, self.batch_size // 2),
            "edit_budget": self.learning_rate + 2,
            "step_buffer_context": step_buffer_context,
        }
        if self.yaml_surface and self.prompt_slots:
            reflect_kwargs["prompt_slots"] = self.prompt_slots
            reflect_kwargs["prompt_slots_text"] = self.prompt_slots_text
            reflect_kwargs["learning_rate"] = self.learning_rate

        raw_patches = self.adapter.reflect(
            results, self.current_skill, step_dir,
            **reflect_kwargs,
        )

        if not raw_patches:
            log.warning("no patches from reflect")
            return GateResult(
                action="reject",
                current_skill=self.current_skill,
                current_score=self.current_score,
                best_skill=self.best_skill,
                best_score=self.best_score,
                best_step=self.best_step,
            )

        failure_patches = [rp for rp in raw_patches if rp.source_type == "failure"]
        success_patches = [rp for rp in raw_patches if rp.source_type == "success"]

        merged = merge_patches(self.current_skill, failure_patches, success_patches)

        if not merged.edits:
            log.warning("merged patch has no edits")
            return GateResult(
                action="reject",
                current_skill=self.current_skill,
                current_score=self.current_score,
                best_skill=self.best_skill,
                best_score=self.best_score,
                best_step=self.best_step,
            )

        clipped = rank_and_select(self.current_skill, merged, max_edits=self.learning_rate)

        if self.yaml_surface:
            violations = self._validate_edits_target_prompts_only(clipped)
            if violations:
                log.warning("edits target non-prompt content, rejecting", violations=violations)
                self.rejected_edits.append(clipped)
                return GateResult(
                    action="reject",
                    current_skill=self.current_skill,
                    current_score=self.current_score,
                    best_skill=self.best_skill,
                    best_score=self.best_score,
                    best_step=self.best_step,
                )

        candidate_slots: dict[str, str] | None = None
        if self.yaml_surface and self._workflow_name:
            candidate_slots = dict(self.prompt_slots)
            for edit in clipped.edits:
                if edit.op == "replace":
                    for slot_name, slot_value in self.prompt_slots.items():
                        if slot_value == edit.target:
                            candidate_slots[slot_name] = edit.content
                            break

            total_magnitude = 0
            for slot_name, new_value in candidate_slots.items():
                old_value = self.prompt_slots.get(slot_name, "")
                if new_value == old_value:
                    continue
                magnitude = compute_prompt_change_magnitude(old_value, new_value)
                total_magnitude += magnitude
                log.info(
                    "slot edit magnitude",
                    slot=slot_name,
                    magnitude=magnitude,
                )
            if total_magnitude > self.learning_rate:
                log.warning(
                    "total edit magnitude exceeds learning rate",
                    total_magnitude=total_magnitude,
                    limit=self.learning_rate,
                )
                self.rejected_edits.append(clipped)
                return GateResult(
                    action="reject",
                    current_skill=self.current_skill,
                    current_score=self.current_score,
                    best_skill=self.best_skill,
                    best_score=self.best_score,
                    best_step=self.best_step,
                )
            log.info(
                "edit magnitude ok",
                total_magnitude=total_magnitude,
                limit=self.learning_rate,
            )

            candidate_skill = render_skill_from_slots(
                workflow_name=self._workflow_name,
                prompt_slots=candidate_slots,
                skill_path=self.skill_path,
            )
        else:
            candidate_skill = apply_patch(self.current_skill, clipped)

        if self.overfit:
            self._save_skill(candidate_skill)
            if use_preloaded:
                env = self.adapter.build_train_env(self.batch_size, seed=self.global_step)
            eval_results = self.adapter.rollout(env, candidate_skill, step_dir + "/eval")
            cand_hard, cand_soft = self._compute_score(eval_results)
            log.info(
                "overfit eval",
                step=self.global_step,
                baseline=round(self.current_score, 4),
                candidate=round(
                    select_gate_score(cand_hard, cand_soft, self.metric), 4,
                ),
            )
        else:
            self._save_skill(candidate_skill)
            eval_env = self.adapter.build_eval_env(
                env_num=self.batch_size, split="eval", seed=self.eval_split_seed,
            )
            eval_results = self.adapter.rollout(eval_env, candidate_skill, step_dir + "/eval")
            cand_hard, cand_soft = self._compute_score(eval_results)

        gate = evaluate_gate(
            candidate_skill=candidate_skill,
            cand_hard=cand_hard,
            cand_soft=cand_soft,
            current_skill=self.current_skill,
            current_score=self.current_score,
            best_skill=self.best_skill,
            best_score=self.best_score,
            best_step=self.best_step,
            global_step=self.global_step,
            metric=self.metric,
            accept_ties=self.overfit,
        )

        if gate.action == "reject":
            self.rejected_edits.append(clipped)
            self._save_skill(self.current_skill)
            log.info(
                "step result",
                step=self.global_step,
                action="reject",
                accepted_total=self.global_step - len(self.rejected_edits),
                rejected_total=len(self.rejected_edits),
            )
        else:
            self.current_skill = gate.current_skill
            self.current_score = gate.current_score
            if self.yaml_surface:
                self._update_prompt_slots_after_accept(clipped, candidate_slots)
            if gate.action == "accept_new_best":
                self.best_skill = gate.best_skill
                self.best_score = gate.best_score
                self.best_step = gate.best_step
            log.info(
                "step result",
                step=self.global_step,
                action=gate.action,
                score=round(gate.current_score, 4),
                accepted_total=self.global_step - len(self.rejected_edits),
                rejected_total=len(self.rejected_edits),
            )

        (Path(step_dir) / "patch.json").write_text(
            json.dumps(clipped.model_dump(), indent=2)
        )
        (Path(step_dir) / "gate.json").write_text(
            json.dumps(gate.model_dump(), indent=2)
        )

        return gate
