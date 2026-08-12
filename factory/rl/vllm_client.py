"""HTTP client for vLLM's OpenAI-compatible API."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class RolloutResult:
    """Result of a single rollout generation request."""

    success: bool
    text: str = ""
    error: str | None = None
    retryable: bool = False
    raw_response: dict[str, Any] = field(default_factory=dict)


class VLLMClient:
    """Thin HTTP client for vLLM's /v1/chat/completions endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        model: str = "meta-llama/Llama-3.1-8B-Instruct",
        timeout: float = 120.0,
        connect_timeout: float = 10.0,
        max_retries: int = 3,
        retry_base_delay: float = 2.0,
        retry_max_delay: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def is_available(self) -> bool:
        """Check if the vLLM server is reachable via /health."""
        try:
            url = self.base_url.rsplit("/v1", 1)[0] + "/health"
            resp = self._client.get(url, timeout=5.0)
            return resp.status_code == 200
        except (httpx.HTTPError, Exception):
            return False

    def generate(
        self,
        prompt: str,
        *,
        n: int = 1,
        temperature: float = 0.8,
        max_tokens: int = 4096,
    ) -> list[RolloutResult]:
        """Generate completions for a single prompt.

        Returns one RolloutResult per completion (n total).
        Retries on transient HTTP errors with exponential backoff + jitter.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "n": n,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        attempt = 0
        last_error: str | None = None

        while attempt <= self.max_retries:
            try:
                resp = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                )

                if resp.status_code == 503:
                    last_error = f"Server busy (503): {resp.text[:200]}"
                    self._backoff_sleep(attempt)
                    attempt += 1
                    continue

                if resp.status_code >= 500:
                    last_error = f"Server error ({resp.status_code}): {resp.text[:200]}"
                    self._backoff_sleep(attempt)
                    attempt += 1
                    continue

                if resp.status_code >= 400:
                    return [
                        RolloutResult(
                            success=False,
                            error=f"Client error ({resp.status_code}): {resp.text[:500]}",
                            retryable=False,
                            raw_response={"status_code": resp.status_code},
                        )
                    ]

                data = resp.json()
                return self._parse_response(data, n)

            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
                last_error = f"{type(e).__name__}: {e}"
                self._backoff_sleep(attempt)
                attempt += 1

        return [
            RolloutResult(
                success=False,
                error=f"Exhausted {self.max_retries} retries. Last error: {last_error}",
                retryable=False,
            )
        ]

    def _parse_response(self, data: dict[str, Any], expected_n: int) -> list[RolloutResult]:
        choices = data.get("choices", [])
        if not choices:
            return [
                RolloutResult(
                    success=False,
                    error="No choices in response",
                    raw_response=data,
                )
            ]

        results: list[RolloutResult] = []
        for choice in choices:
            message = choice.get("message", {})
            text = message.get("content", "")
            results.append(RolloutResult(success=True, text=text, raw_response=choice))

        return results

    def _backoff_sleep(self, attempt: int) -> None:
        delay = min(self.retry_base_delay * (2 ** min(attempt, 8)), self.retry_max_delay)
        delay *= 0.5 + random.random()
        time.sleep(delay)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> VLLMClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
