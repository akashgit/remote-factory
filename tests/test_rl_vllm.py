"""Tests for vLLM client, solution parser, and rollout generator."""

import httpx
import pytest

from factory.rl.solution_parser import parse_solution
from factory.rl.vllm_client import RolloutResult, VLLMClient
from factory.rl.vllm_rollout import generate_vllm_rollouts


# ---------------------------------------------------------------------------
# solution_parser tests
# ---------------------------------------------------------------------------


class TestSolutionParser:
    def test_fenced_json_block(self) -> None:
        text = 'Here is my solution:\n```json\n{"circles": [[0.5, 0.5, 0.1]]}\n```'
        result = parse_solution(text)
        assert result == {"circles": [[0.5, 0.5, 0.1]]}

    def test_bare_json(self) -> None:
        text = 'The answer is {"circles": [[0.1, 0.2, 0.05], [0.3, 0.4, 0.06]]}'
        result = parse_solution(text)
        assert result == {"circles": [[0.1, 0.2, 0.05], [0.3, 0.4, 0.06]]}

    def test_last_fenced_block_wins(self) -> None:
        text = (
            '```json\n{"circles": [[0.0, 0.0, 0.0]]}\n```\n'
            'Actually, better:\n```json\n{"circles": [[1.0, 1.0, 0.5]]}\n```'
        )
        result = parse_solution(text)
        assert result == {"circles": [[1.0, 1.0, 0.5]]}

    def test_invalid_circle_shape(self) -> None:
        text = '```json\n{"circles": [[0.5, 0.5]]}\n```'
        result = parse_solution(text)
        assert result is None

    def test_no_json_returns_none(self) -> None:
        text = "I don't know how to solve this problem."
        result = parse_solution(text)
        assert result is None

    def test_non_circle_packing_task(self) -> None:
        text = '```json\n{"solution": {"answer": 42}}\n```'
        result = parse_solution(text, task_type="other-task")
        assert result == {"solution": {"answer": 42}}

    def test_nested_solution_key(self) -> None:
        text = '```json\n{"solution": {"circles": [[0.5, 0.5, 0.1]]}}\n```'
        result = parse_solution(text)
        assert result == {"circles": [[0.5, 0.5, 0.1]]}

    def test_non_numeric_circle_values(self) -> None:
        text = '```json\n{"circles": [["a", "b", "c"]]}\n```'
        result = parse_solution(text)
        assert result is None

    def test_empty_circles_list(self) -> None:
        text = '```json\n{"circles": []}\n```'
        result = parse_solution(text)
        assert result == {"circles": []}

    def test_fenced_plain_block_with_json(self) -> None:
        text = 'Result:\n```\n{"circles": [[0.5, 0.5, 0.1]]}\n```'
        result = parse_solution(text)
        assert result == {"circles": [[0.5, 0.5, 0.1]]}


# ---------------------------------------------------------------------------
# VLLMClient tests (using httpx mock transport)
# ---------------------------------------------------------------------------


def _make_chat_response(texts: list[str]) -> dict:
    """Build a minimal OpenAI-compatible chat completion response."""
    return {
        "id": "test",
        "object": "chat.completion",
        "choices": [
            {
                "index": i,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
            for i, text in enumerate(texts)
        ],
    }


class TestVLLMClient:
    def test_generate_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_chat_response(["hello world"]))

        transport = httpx.MockTransport(handler)
        client = VLLMClient(base_url="http://fake:8000/v1")
        client._client = httpx.Client(transport=transport)

        results = client.generate("test prompt", n=1)
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].text == "hello world"
        client.close()

    def test_generate_multiple_completions(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_chat_response(["a", "b", "c"]))

        transport = httpx.MockTransport(handler)
        client = VLLMClient(base_url="http://fake:8000/v1")
        client._client = httpx.Client(transport=transport)

        results = client.generate("test", n=3)
        assert len(results) == 3
        assert [r.text for r in results] == ["a", "b", "c"]
        client.close()

    def test_generate_client_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="Bad request")

        transport = httpx.MockTransport(handler)
        client = VLLMClient(base_url="http://fake:8000/v1")
        client._client = httpx.Client(transport=transport)

        results = client.generate("test")
        assert len(results) == 1
        assert results[0].success is False
        assert "400" in (results[0].error or "")
        client.close()

    def test_generate_retries_on_503(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(503, text="Server busy")
            return httpx.Response(200, json=_make_chat_response(["ok"]))

        transport = httpx.MockTransport(handler)
        client = VLLMClient(
            base_url="http://fake:8000/v1",
            max_retries=5,
            retry_base_delay=0.01,
            retry_max_delay=0.05,
        )
        client._client = httpx.Client(transport=transport)

        results = client.generate("test")
        assert results[0].success is True
        assert call_count == 3
        client.close()

    def test_is_available_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "/health" in str(request.url):
                return httpx.Response(200, text="ok")
            return httpx.Response(200, json=_make_chat_response(["x"]))

        transport = httpx.MockTransport(handler)
        client = VLLMClient(base_url="http://fake:8000/v1")
        client._client = httpx.Client(transport=transport)

        assert client.is_available() is True
        client.close()

    def test_is_available_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        client = VLLMClient(base_url="http://fake:8000/v1")
        client._client = httpx.Client(transport=transport)

        assert client.is_available() is False
        client.close()

    def test_context_manager(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_chat_response(["hi"]))

        transport = httpx.MockTransport(handler)
        with VLLMClient(base_url="http://fake:8000/v1") as client:
            client._client = httpx.Client(transport=transport)
            results = client.generate("test")
            assert results[0].success is True

    def test_exhausted_retries(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal server error")

        transport = httpx.MockTransport(handler)
        client = VLLMClient(
            base_url="http://fake:8000/v1",
            max_retries=2,
            retry_base_delay=0.01,
            retry_max_delay=0.02,
        )
        client._client = httpx.Client(transport=transport)

        results = client.generate("test")
        assert len(results) == 1
        assert results[0].success is False
        assert "retries" in (results[0].error or "").lower()
        client.close()


# ---------------------------------------------------------------------------
# vllm_rollout tests
# ---------------------------------------------------------------------------


class TestVLLMRollout:
    def test_generate_rollouts_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end: prompts → rollouts with parsed solutions."""
        call_count = 0

        def fake_generate(
            self: VLLMClient,
            prompt: str,
            *,
            n: int = 1,
            temperature: float = 0.8,
            max_tokens: int = 4096,
        ) -> list[RolloutResult]:
            nonlocal call_count
            call_count += 1
            return [
                RolloutResult(
                    success=True,
                    text=f'{{"circles": [[0.{i}, 0.{i}, 0.0{i}]]}}',
                )
                for i in range(n)
            ]

        monkeypatch.setattr(VLLMClient, "generate", fake_generate)
        monkeypatch.setattr(VLLMClient, "__init__", lambda self, **kw: None)
        monkeypatch.setattr(VLLMClient, "close", lambda self: None)
        monkeypatch.setattr(VLLMClient, "__enter__", lambda self: self)
        monkeypatch.setattr(VLLMClient, "__exit__", lambda self, *a: None)

        prompts = [
            {"prompt_text": "Pack 26 circles", "strategy": "greedy"},
            {"prompt_text": "Pack 26 circles v2", "strategy": "physics"},
        ]
        rollouts = generate_vllm_rollouts(
            prompts,
            num_per_prompt=3,
            vllm_url="http://fake:8000/v1",
            vllm_model="test-model",
        )

        assert len(rollouts) == 6
        assert rollouts[0]["prompt_idx"] == 0
        assert rollouts[0]["rollout_idx"] == 0
        assert rollouts[3]["prompt_idx"] == 1
        assert "circles" in rollouts[0]["solution"]
        assert call_count == 2

    def test_generate_rollouts_graceful_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Failed rollouts produce empty solutions instead of aborting."""

        def fake_generate(self: VLLMClient, prompt: str, **kw: object) -> list[RolloutResult]:
            return [RolloutResult(success=False, error="timeout")]

        monkeypatch.setattr(VLLMClient, "generate", fake_generate)
        monkeypatch.setattr(VLLMClient, "__init__", lambda self, **kw: None)
        monkeypatch.setattr(VLLMClient, "close", lambda self: None)
        monkeypatch.setattr(VLLMClient, "__enter__", lambda self: self)
        monkeypatch.setattr(VLLMClient, "__exit__", lambda self, *a: None)

        prompts = [{"prompt_text": "test", "strategy": "basic"}]
        rollouts = generate_vllm_rollouts(
            prompts, num_per_prompt=2, vllm_url="http://fake:8000/v1", vllm_model="test",
        )

        assert len(rollouts) == 2
        assert rollouts[0]["solution"] == {}
        assert rollouts[1]["solution"] == {}
