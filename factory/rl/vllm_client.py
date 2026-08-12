"""vLLM HTTP client for real rollout generation via OpenAI-compatible API."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0


class VLLMClient:
    """HTTP client for vLLM's OpenAI-compatible completions API."""

    def __init__(self, base_url: str, model: str = "Qwen3-32B") -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=60.0)

    def health_check(self) -> bool:
        """Check if the vLLM server is reachable via GET /v1/models."""
        try:
            resp = self._client.get(f"{self.base_url}/v1/models")
            resp.raise_for_status()
            return True
        except (httpx.HTTPError, httpx.ConnectError):
            return False

    def generate_rollouts(
        self,
        prompts: list[dict[str, Any]],
        num_per_prompt: int = 64,
        temperature: float = 0.8,
        max_tokens: int = 2048,
    ) -> list[dict[str, Any]]:
        """Generate rollouts by calling POST /v1/completions for each prompt.

        Returns a list of dicts matching the Rollout TypedDict schema.
        """
        all_rollouts: list[dict[str, Any]] = []

        for prompt_idx, prompt in enumerate(prompts):
            prompt_text = prompt["prompt_text"]
            log.info(
                "generating_rollouts",
                prompt_idx=prompt_idx,
                num_per_prompt=num_per_prompt,
            )

            resp = self._completions_request(
                prompt_text,
                n=num_per_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            choices = resp.get("choices", [])
            for rollout_idx, choice in enumerate(choices):
                text = choice.get("text", "")
                thinking = _extract_thinking(text)
                code = _extract_code(text)
                solution = _parse_solution(text)

                all_rollouts.append(
                    {
                        "prompt_idx": prompt_idx,
                        "rollout_idx": rollout_idx,
                        "global_idx": prompt_idx * num_per_prompt + rollout_idx,
                        "prompt": prompt_text,
                        "solution": solution,
                        "thinking": thinking,
                        "code": code,
                    }
                )

        log.info("rollouts_generated", total=len(all_rollouts))
        return all_rollouts

    def _completions_request(
        self,
        prompt: str,
        *,
        n: int,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Send a POST /v1/completions request with retry logic."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": n,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.post(
                    f"{self.base_url}/v1/completions",
                    json=payload,
                )
                if resp.status_code >= 500:
                    log.warning(
                        "vllm_server_error",
                        status=resp.status_code,
                        attempt=attempt + 1,
                    )
                    last_exc = httpx.HTTPStatusError(
                        f"Server error {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )
                    time.sleep(_BACKOFF_BASE * (2**attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError as exc:
                log.warning("vllm_connect_error", attempt=attempt + 1, error=str(exc))
                last_exc = exc
                time.sleep(_BACKOFF_BASE * (2**attempt))

        raise last_exc  # type: ignore[misc]

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()


def _extract_thinking(text: str) -> str:
    """Extract content between <think> tags."""
    match = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _extract_code(text: str) -> str:
    """Extract content from fenced code blocks."""
    match = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_solution(text: str) -> dict[str, Any]:
    """Parse JSON solution from the response text.

    Tries code blocks first, then raw JSON objects.
    """
    code = _extract_code(text)
    if code:
        try:
            return json.loads(code)
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {}
