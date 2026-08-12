"""Tests for factory.rl.vllm_client — all HTTP is mocked."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from factory.rl.vllm_client import VLLMClient, _extract_code, _extract_thinking, _parse_solution


class TestHealthCheck:
    def test_healthy_server(self) -> None:
        client = VLLMClient("http://localhost:8000")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "get", return_value=mock_resp):
            assert client.health_check() is True

    def test_unreachable_server(self) -> None:
        client = VLLMClient("http://localhost:8000")
        with patch.object(client._client, "get", side_effect=httpx.ConnectError("refused")):
            assert client.health_check() is False

    def test_server_error(self) -> None:
        client = VLLMClient("http://localhost:8000")
        with patch.object(
            client._client,
            "get",
            side_effect=httpx.HTTPStatusError(
                "500",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            ),
        ):
            assert client.health_check() is False


class TestGenerateRollouts:
    def _mock_completion_response(self, texts: list[str]) -> dict:
        return {
            "choices": [{"text": t, "index": i} for i, t in enumerate(texts)],
        }

    def test_successful_generation(self) -> None:
        client = VLLMClient("http://localhost:8000", model="test-model")
        prompts = [{"prompt_text": "Solve circle packing", "strategy": "greedy"}]
        text = '<think>reasoning here</think>\n```json\n{"circles": [[0.5, 0.5, 0.1]]}\n```'
        resp_data = self._mock_completion_response([text, text])

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = resp_data

        with patch.object(client._client, "post", return_value=mock_resp):
            rollouts = client.generate_rollouts(prompts, num_per_prompt=2)

        assert len(rollouts) == 2
        assert rollouts[0]["prompt_idx"] == 0
        assert rollouts[0]["rollout_idx"] == 0
        assert rollouts[0]["global_idx"] == 0
        assert rollouts[1]["rollout_idx"] == 1
        assert rollouts[1]["global_idx"] == 1
        assert rollouts[0]["prompt"] == "Solve circle packing"
        assert rollouts[0]["thinking"] == "reasoning here"
        assert rollouts[0]["solution"] == {"circles": [[0.5, 0.5, 0.1]]}

    def test_multiple_prompts(self) -> None:
        client = VLLMClient("http://localhost:8000")
        prompts = [
            {"prompt_text": "prompt A", "strategy": "s1"},
            {"prompt_text": "prompt B", "strategy": "s2"},
        ]
        text = '```json\n{"circles": []}\n```'
        resp_data = self._mock_completion_response([text])

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = resp_data

        with patch.object(client._client, "post", return_value=mock_resp) as mock_post:
            rollouts = client.generate_rollouts(prompts, num_per_prompt=1)

        assert len(rollouts) == 2
        assert rollouts[0]["prompt_idx"] == 0
        assert rollouts[1]["prompt_idx"] == 1
        assert rollouts[0]["global_idx"] == 0
        assert rollouts[1]["global_idx"] == 1
        assert mock_post.call_count == 2


class TestRetryLogic:
    def test_retry_on_500(self) -> None:
        client = VLLMClient("http://localhost:8000")

        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.request = MagicMock()

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"text": "ok", "index": 0}]}

        with patch.object(client._client, "post", side_effect=[fail_resp, ok_resp]):
            with patch("factory.rl.vllm_client.time.sleep"):
                result = client._completions_request("test", n=1, temperature=0.8, max_tokens=100)

        assert result == {"choices": [{"text": "ok", "index": 0}]}

    def test_exhausted_retries_raises(self) -> None:
        client = VLLMClient("http://localhost:8000")

        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.request = MagicMock()

        with patch.object(client._client, "post", return_value=fail_resp):
            with patch("factory.rl.vllm_client.time.sleep"):
                with pytest.raises(httpx.HTTPStatusError):
                    client._completions_request("test", n=1, temperature=0.8, max_tokens=100)

    def test_retry_on_connect_error(self) -> None:
        client = VLLMClient("http://localhost:8000")

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": []}

        with patch.object(
            client._client,
            "post",
            side_effect=[httpx.ConnectError("refused"), ok_resp],
        ):
            with patch("factory.rl.vllm_client.time.sleep"):
                result = client._completions_request("test", n=1, temperature=0.8, max_tokens=100)

        assert result == {"choices": []}

    def test_connect_error_exhausted_raises(self) -> None:
        client = VLLMClient("http://localhost:8000")

        with patch.object(
            client._client,
            "post",
            side_effect=httpx.ConnectError("refused"),
        ):
            with patch("factory.rl.vllm_client.time.sleep"):
                with pytest.raises(httpx.ConnectError):
                    client._completions_request("test", n=1, temperature=0.8, max_tokens=100)


class TestTrainFailFast:
    def test_runtime_error_on_unreachable_vllm(self, tmp_path) -> None:
        """train.py raises RuntimeError when vLLM is unreachable (no silent fallback)."""
        import sys

        iteration_dir = tmp_path / ".factory/rl/iteration_0"
        iteration_dir.mkdir(parents=True)
        (iteration_dir / "prompts.json").write_text(
            '{"prompts": [{"prompt_text": "test", "strategy": "s"}], "scoring_direction": "maximize"}'
        )

        with patch.object(
            sys,
            "argv",
            [
                "train",
                "--task", "test",
                "--task-dir", str(tmp_path),
                "--project-path", str(tmp_path),
                "--iteration", "0",
                "--no-mock",
                "--vllm-url", "http://localhost:9999",
            ],
        ):
            with patch("factory.rl.vllm_client.VLLMClient.health_check", return_value=False):
                from factory.rl.train import main

                with pytest.raises(RuntimeError, match="vLLM server unreachable"):
                    main()


class TestParsing:
    def test_extract_thinking(self) -> None:
        text = "prefix <think>my reasoning\nhere</think> suffix"
        assert _extract_thinking(text) == "my reasoning\nhere"

    def test_extract_thinking_empty(self) -> None:
        assert _extract_thinking("no tags here") == ""

    def test_extract_code(self) -> None:
        text = "some text\n```python\nprint('hello')\n```\nmore text"
        assert _extract_code(text) == "print('hello')"

    def test_extract_code_json(self) -> None:
        text = '```json\n{"key": "val"}\n```'
        assert _extract_code(text) == '{"key": "val"}'

    def test_extract_code_empty(self) -> None:
        assert _extract_code("no code blocks") == ""

    def test_parse_solution_from_code_block(self) -> None:
        text = '```json\n{"circles": [[0.5, 0.5, 0.1]]}\n```'
        assert _parse_solution(text) == {"circles": [[0.5, 0.5, 0.1]]}

    def test_parse_solution_from_raw_json(self) -> None:
        text = 'Some text {"answer": 42} more text'
        assert _parse_solution(text) == {"answer": 42}

    def test_parse_solution_no_json(self) -> None:
        assert _parse_solution("no json at all") == {}

    def test_parse_solution_invalid_json_in_code_block(self) -> None:
        text = "```\nnot json\n```"
        assert _parse_solution(text) == {}
