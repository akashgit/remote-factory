"""Custom VERL AgentLoop for Lumen.

Ported from Discover's DiscoverAgentLoopManagerTQ with PUCT removed.
Implements two-phase token completion (think + answer) with fine-grained
response masking and Einstein Arena evaluation.

Activate via VERL config:
  actor_rollout_ref.rollout.agent.agent_loop_manager_class: \
    "factory.lumen.verl_integration.agent_loop:LumenAgentLoopManagerTQ"
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import numpy as np
import ray
import torch
import transfer_queue as tq
from tensordict import NonTensorData, NonTensorStack, TensorDict

from verl.experimental.agent_loop import (
    AgentLoopManager,
    AgentLoopOutput,
    AgentLoopWorker,
    get_trajectory_info,
)
from verl.experimental.agent_loop.agent_loop import AgentLoopMetrics
from verl.utils.tensordict_utils import list_of_dict_to_tensordict

logger = logging.getLogger(__name__)


@ray.remote
class LumenAgentLoopWorkerTQ(AgentLoopWorker):
    """Agent loop worker with two-phase completion for Lumen."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tq.init()
        self.background_tasks = set()
        self._tokenizer = None
        self._stop_token_ids = []
        self._lumen_config = {}

    def set_lumen_config(self, lumen_config: dict):
        """Inject Lumen config (task_dir, eval_timeout, phase1_max_tokens)."""
        self._lumen_config = lumen_config

        from transformers import AutoTokenizer
        model_path = self.config.actor_rollout_ref.model.path
        self._tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

        # Stop tokens: EOS token + any model-specific stop tokens
        self._stop_token_ids = [self._tokenizer.eos_token_id]
        # Add common stop tokens if they exist in the tokenizer vocabulary
        for stop_str in ["<|endoftext|>", "<|im_end|>", "</s>"]:
            stop_id = self._tokenizer.convert_tokens_to_ids(stop_str)
            if stop_id != self._tokenizer.unk_token_id and stop_id not in self._stop_token_ids:
                self._stop_token_ids.append(stop_id)

        self._phase1_max_tokens = lumen_config.get("phase1_max_tokens", 26000)
        self._context_window = lumen_config.get("max_model_len", 32768)
        self._context_buffer = 50
        self._phase2_prefill = "\n\n... I need to give my final answer now.\n</think>\n"
        self._phase2_prefill_ids = self._tokenizer.encode(
            self._phase2_prefill, add_special_tokens=False
        )

    async def generate_sequences(self, batch: TensorDict) -> None:
        """Override: generate completions for each prompt in the batch."""
        validate = batch.get("validate", False)
        if isinstance(validate, torch.Tensor):
            validate = bool(validate.item())
        batch.pop("validate", None)

        trajectory_info = await get_trajectory_info(
            batch["global_steps"], batch["index"], validate
        )

        for i in range(len(batch)):
            prompt = {}
            for k, v in batch.items():
                if isinstance(v, torch.Tensor):
                    prompt[k] = v[i]
                elif isinstance(v, NonTensorStack):
                    prompt[k] = v[i].data
                elif isinstance(v, NonTensorData):
                    prompt[k] = v.data

            task = asyncio.create_task(
                self._run_prompt(prompt, trajectory=trajectory_info[i], validate=validate)
            )
            self.background_tasks.add(task)
            task.add_done_callback(self.background_tasks.discard)

    async def _run_prompt(self, prompt: dict, trajectory: dict, validate: bool) -> None:
        """Generate N completions + evaluate for one prompt."""
        uid = prompt["uid"]
        partition_id = "train" if not validate else "val"
        await tq.async_kv_put(key=uid, partition_id=partition_id, tag={"status": "running"})

        try:
            config = self.config.actor_rollout_ref.rollout
            n = prompt.pop("__rollout_n__", config.n if not validate else config.val_kwargs.n)

            # Build prompt from raw_prompt (set by manager from parquet data)
            messages = prompt.get("raw_prompt", [])
            if not messages:
                logger.error(f"No raw_prompt for uid={uid}")
                await tq.async_kv_put(
                    key=uid, partition_id=partition_id, tag={"status": "failure"}
                )
                return

            # Use tokenizer.apply_chat_template to build prompt_ids directly
            prompt_ids = self._tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True
            )
            if isinstance(prompt_ids, torch.Tensor):
                prompt_ids = prompt_ids.tolist()

            prompt["_prompt_ids"] = prompt_ids

            sampling_params = {
                "temperature": float(config.temperature),
                "top_p": float(config.top_p),
                "top_k": int(config.top_k),
                "repetition_penalty": 1.0,
                "logprobs": config.calculate_log_probs,
                "stop_token_ids": self._stop_token_ids,
            }

            tasks = []
            for session_id in range(n):
                task = asyncio.create_task(
                    self._generate_two_phase(
                        prompt_ids=prompt_ids,
                        sampling_params=sampling_params,
                        prompt=prompt,
                        trajectory=trajectory,
                        validate=validate,
                        session_id=session_id,
                    )
                )
                tasks.append(task)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            valid_results = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    logger.error(f"Session {i} failed: {type(r).__name__}: {r}")
                else:
                    valid_results.append(r)

            await tq.async_kv_put(
                key=uid, partition_id=partition_id, tag={"status": "finished"}
            )

        except Exception as e:
            logger.exception(f"Error in _run_prompt: {e}")
            await tq.async_kv_put(
                key=uid, partition_id=partition_id, tag={"status": "failure"}
            )

    async def _generate_two_phase(
        self, prompt_ids, sampling_params, prompt, trajectory, validate, session_id,
    ) -> tuple[AgentLoopOutput, str, float]:
        """Two-phase generation: thinking + forced answer + eval.

        Identical to Discover's three-case logic:
        - Case A: natural stop
        - Case B: budget exhausted, </think> present → continue without prefill
        - Case C: budget exhausted, no </think> → inject prefill (mask=0)
        """
        t0 = time.time()

        prompt_len = len(prompt_ids)
        phase1_budget = self._phase1_max_tokens - prompt_len
        if phase1_budget <= 0:
            phase1_budget = 100

        request_id = uuid.uuid4().hex
        phase1_output = await self.llm_client.generate(
            request_id=request_id,
            prompt_ids=prompt_ids,
            sampling_params={**sampling_params, "max_tokens": phase1_budget},
        )

        p1_tokens = phase1_output.token_ids
        p1_logprobs = phase1_output.log_probs or [0.0] * len(p1_tokens)

        hit_stop = (
            phase1_output.stop_reason == "stop" or self._hit_stop_token(p1_tokens)
        )
        budget_exhausted = not hit_stop and len(p1_tokens) >= phase1_budget

        gen_case = "A"
        p2_len = 0

        if not budget_exhausted:
            response_ids = p1_tokens
            response_logprobs = p1_logprobs
            response_mask = [1] * len(p1_tokens)
        elif self._contains_pattern(p1_tokens, "</think>"):
            gen_case = "B"
            phase2_prompt = prompt_ids + p1_tokens
            phase2_budget = self._context_window - len(phase2_prompt) - self._context_buffer
            if phase2_budget <= 0:
                response_ids = p1_tokens
                response_logprobs = p1_logprobs
                response_mask = [1] * len(p1_tokens)
            else:
                request_id_p2 = uuid.uuid4().hex
                phase2_output = await self.llm_client.generate(
                    request_id=request_id_p2,
                    prompt_ids=phase2_prompt,
                    sampling_params={**sampling_params, "max_tokens": phase2_budget},
                )
                p2_tokens = phase2_output.token_ids
                p2_logprobs = phase2_output.log_probs or [0.0] * len(p2_tokens)
                p2_len = len(p2_tokens)
                response_ids = p1_tokens + p2_tokens
                response_logprobs = p1_logprobs + p2_logprobs
                response_mask = [1] * len(p1_tokens) + [1] * len(p2_tokens)
        else:
            gen_case = "C"
            phase2_prompt = prompt_ids + p1_tokens + self._phase2_prefill_ids
            phase2_budget = self._context_window - len(phase2_prompt) - self._context_buffer
            if phase2_budget <= 0:
                response_ids = p1_tokens + self._phase2_prefill_ids
                response_logprobs = p1_logprobs + [0.0] * len(self._phase2_prefill_ids)
                response_mask = [1] * len(p1_tokens) + [0] * len(self._phase2_prefill_ids)
            else:
                request_id_p2 = uuid.uuid4().hex
                phase2_output = await self.llm_client.generate(
                    request_id=request_id_p2,
                    prompt_ids=phase2_prompt,
                    sampling_params={**sampling_params, "max_tokens": phase2_budget},
                )
                p2_tokens = phase2_output.token_ids
                p2_logprobs = phase2_output.log_probs or [0.0] * len(p2_tokens)
                p2_len = len(p2_tokens)
                response_ids = p1_tokens + self._phase2_prefill_ids + p2_tokens
                response_logprobs = (
                    p1_logprobs
                    + [0.0] * len(self._phase2_prefill_ids)
                    + p2_logprobs
                )
                response_mask = (
                    [1] * len(p1_tokens)
                    + [0] * len(self._phase2_prefill_ids)
                    + [1] * len(p2_tokens)
                )

        gen_time = time.time() - t0

        output = AgentLoopOutput(
            prompt_ids=prompt_ids,
            response_ids=response_ids,
            response_mask=response_mask,
            response_logprobs=response_logprobs,
            metrics=AgentLoopMetrics(generate_sequences=gen_time),
            extra_fields={
                "min_global_steps": prompt.get("global_steps", 0),
                "max_global_steps": prompt.get("global_steps", 0),
            },
        )

        # Evaluate via Einstein Arena verifier
        response_text = self._tokenizer.decode(response_ids, skip_special_tokens=True)
        code = ""
        score = 0.0
        eval_msg = ""

        if not validate:
            from factory.lumen.verl_integration.reward import compute_score
            extra_info = prompt.get("extra_info", {})
            if not extra_info.get("task_dir"):
                extra_info["task_dir"] = self._lumen_config.get("task_dir", "")
            extra_info["eval_timeout"] = self._lumen_config.get("eval_timeout", 60)

            result = await asyncio.to_thread(
                compute_score,
                data_source=self._lumen_config.get("data_source", "lumen"),
                solution_str=response_text,
                ground_truth=None,
                extra_info=extra_info,
            )
            score = float(result.get("score", 0.0))
            code = result.get("code", "")
            eval_msg = result.get("eval_msg", "")

        output.reward_score = score
        reward_extra = {
            "acc": float(score > 0),
            "code": code,
            "eval_msg": eval_msg,
            "gen_case": gen_case,
            "p1_len": len(p1_tokens),
            "p2_len": p2_len,
            "gen_time_s": round(gen_time, 3),
        }
        output.extra_fields["reward_extra_info"] = reward_extra

        await self._write_to_tq(output, prompt, session_id, validate)
        return output, code, score

    async def _write_to_tq(
        self, output: AgentLoopOutput, prompt: dict, session_id: int, validate: bool,
    ) -> None:
        """Write output to TransferQueue in VERL's expected format."""
        uid = prompt["uid"]
        partition_id = "train" if not validate else "val"
        key = f"{uid}_{session_id}_0"

        prompts = torch.tensor(output.prompt_ids, dtype=torch.int64)
        responses = torch.tensor(output.response_ids, dtype=torch.int64)
        input_ids = torch.cat([prompts, responses], dim=0)
        attention_mask = torch.ones_like(input_ids, dtype=torch.int64)
        position_ids = torch.arange(len(input_ids), dtype=torch.int64)

        field = output.as_dict()
        field["uid"] = uid
        field["session_id"] = session_id
        field["global_steps"] = prompt.get("global_steps", 0)
        field["raw_prompt"] = prompt.get("raw_prompt", [])
        field["data_source"] = self._lumen_config.get("data_source", "lumen")
        field["num_turns"] = 1
        field.pop("multi_modal_data", None)
        field["loss_mask"] = field["response_mask"]
        field["input_ids"] = input_ids
        field["position_ids"] = position_ids
        field["attention_mask"] = attention_mask
        field["multi_modal_inputs"] = {}

        prompt_len = prompts.size(0)
        response_len = responses.size(0)
        tag = {
            "status": "success",
            "prompt_len": prompt_len,
            "response_len": response_len,
            "seq_len": prompt_len + response_len,
            "global_steps": prompt.get("global_steps", 0),
            "min_global_steps": output.extra_fields.get("min_global_steps", 0),
            "max_global_steps": output.extra_fields.get("max_global_steps", 0),
        }

        await tq.async_kv_batch_put(
            keys=[key],
            fields=list_of_dict_to_tensordict([field]),
            tags=[tag],
            partition_id=partition_id,
        )

    def _hit_stop_token(self, tokens: list[int]) -> bool:
        if not tokens or not self._stop_token_ids:
            return False
        return tokens[-1] in self._stop_token_ids

    def _contains_pattern(self, tokens: list[int], pattern: str) -> bool:
        pattern_ids = self._tokenizer.encode(pattern, add_special_tokens=False)
        if len(pattern_ids) > len(tokens):
            return False
        for i in range(len(tokens) - len(pattern_ids) + 1):
            if tokens[i : i + len(pattern_ids)] == pattern_ids:
                return True
        return False


class LumenAgentLoopManagerTQ(AgentLoopManager):
    """Lumen agent loop manager — distributes prompts to workers."""

    def __init__(self, config, workers, **kwargs):
        super().__init__(config, workers, **kwargs)
        self._lumen_config = {}

    def set_lumen_config(self, lumen_config: dict):
        self._lumen_config = lumen_config
        for worker in self.workers:
            ray.get(worker.set_lumen_config.remote(lumen_config))

    async def generate_sequences(self, prompts: TensorDict) -> list[TensorDict]:
        """Assign prompts directly to workers (no PUCT sampling)."""
        # Prompts come from parquet data source — just pass through to workers
        # The raw_prompt field is already set from the parquet's prompt column
        return await super().generate_sequences(prompts)
