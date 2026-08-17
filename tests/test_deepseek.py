from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from medgate.deepseek import DeepSeekClient, DeepSeekError


class FakeHttpxClient:
    response = httpx.Response(200, json={
        "id": "response-001",
        "model": "deepseek-v4-flash",
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "测试回答"},
        }],
    })
    last_headers: dict[str, str] | None = None
    last_request: dict | None = None

    def __init__(self, *, base_url: str, headers: dict[str, str], timeout: httpx.Timeout) -> None:
        type(self).last_headers = headers

    def post(self, path: str, *, json: dict) -> httpx.Response:
        type(self).last_request = {"path": path, "json": json}
        return type(self).response

    def close(self) -> None:
        pass


class FakeStreamingResponse:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def __enter__(self) -> "FakeStreamingResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        pass

    def iter_lines(self):
        return iter(self.lines)


class RetrySequenceHttpxClient:
    """按序列返回预置状态码（429 前导 + 最终 200），记录调用次数，模拟 429 重试。"""

    sequence: list[int] = []
    calls: list[dict] = []

    def __init__(self, *, base_url: str, headers: dict[str, str], timeout: httpx.Timeout) -> None:
        pass

    def post(self, path: str, *, json: dict) -> httpx.Response:
        type(self).calls.append({"path": path, "json": json})
        index = len(type(self).calls) - 1
        status = type(self).sequence[min(index, len(type(self).sequence) - 1)]
        if status == 429:
            return httpx.Response(429, headers={"retry-after": "0.01"})
        return httpx.Response(200, json={
            "id": f"response-{index}",
            "model": "deepseek-v4-flash",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "重试后成功"}}],
        })

    def close(self) -> None:
        pass


class DeepSeekRetryTest(unittest.TestCase):
    def setUp(self) -> None:
        RetrySequenceHttpxClient.sequence = []
        RetrySequenceHttpxClient.calls = []

    def test_429_retries_then_succeeds_with_on_retry_callback(self) -> None:
        RetrySequenceHttpxClient.sequence = [429, 429, 200]
        retries: list[int] = []
        with patch("medgate.deepseek.httpx.Client", RetrySequenceHttpxClient):
            client = DeepSeekClient("test-key", max_retries=4, retry_base_delay=0.01, on_retry=lambda attempt: retries.append(attempt))
            try:
                result = client.complete(messages=[{"role": "user", "content": "你好"}])
            finally:
                client.close()
        self.assertEqual(result.content, "重试后成功")
        self.assertEqual(len(RetrySequenceHttpxClient.calls), 3)
        self.assertEqual(retries, [1, 2])

    def test_429_exhausts_retries_and_fails_closed(self) -> None:
        RetrySequenceHttpxClient.sequence = [429, 429, 429, 429, 429]
        with patch("medgate.deepseek.httpx.Client", RetrySequenceHttpxClient):
            client = DeepSeekClient("test-key", max_retries=3, retry_base_delay=0.01)
            try:
                with self.assertRaises(DeepSeekError) as ctx:
                    client.complete(messages=[{"role": "user", "content": "你好"}])
            finally:
                client.close()
        self.assertEqual(ctx.exception.code, "DEEPSEEK_RATE_LIMITED")
        self.assertEqual(len(RetrySequenceHttpxClient.calls), 4)

    def test_max_retries_zero_disables_retry(self) -> None:
        RetrySequenceHttpxClient.sequence = [429, 200]
        with patch("medgate.deepseek.httpx.Client", RetrySequenceHttpxClient):
            client = DeepSeekClient("test-key", max_retries=0, retry_base_delay=0.01)
            try:
                with self.assertRaises(DeepSeekError) as ctx:
                    client.complete(messages=[{"role": "user", "content": "你好"}])
            finally:
                client.close()
        self.assertEqual(ctx.exception.code, "DEEPSEEK_RATE_LIMITED")
        self.assertEqual(len(RetrySequenceHttpxClient.calls), 1)


class FakeStreamingHttpxClient(FakeHttpxClient):
    stream_lines = [
        'data: {"id":"stream-001","model":"deepseek-v4-flash","choices":[{"delta":{"content":"流式"},"finish_reason":null}]}',
        'data: {"id":"stream-001","model":"deepseek-v4-flash","choices":[{"delta":{"content":"输出"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]

    def stream(self, method: str, path: str, *, json: dict):
        type(self).last_request = {"method": method, "path": path, "json": json}
        return FakeStreamingResponse(type(self).stream_lines)


class DeepSeekClientTest(unittest.TestCase):
    def test_stream_request_emits_incremental_content_and_done_metadata(self) -> None:
        with patch("medgate.deepseek.httpx.Client", FakeStreamingHttpxClient):
            client = DeepSeekClient("secret-for-test")
            chunks = list(client.stream(messages=[{"role": "user", "content": "你好"}]))
            client.close()
        self.assertEqual("".join(chunk.content for chunk in chunks), "流式输出")
        self.assertEqual(chunks[-1].finish_reason, "stop")
        self.assertEqual(FakeStreamingHttpxClient.last_request["method"], "POST")
        self.assertEqual(FakeStreamingHttpxClient.last_request["json"]["stream"], True)

    def test_request_uses_confirmed_model_and_openai_compatible_contract(self) -> None:
        FakeHttpxClient.response = httpx.Response(200, json={
            "id": "response-001",
            "model": "deepseek-v4-flash",
            "choices": [{
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "测试回答"},
            }],
        })
        with patch("medgate.deepseek.httpx.Client", FakeHttpxClient):
            client = DeepSeekClient("secret-for-test")
            result = client.complete(
                messages=[{"role": "user", "content": "你好"}],
                response_format={"type": "json_object"},
            )
            client.close()
        self.assertEqual(result.content, "测试回答")
        self.assertEqual(result.response_id, "response-001")
        request = FakeHttpxClient.last_request
        self.assertEqual(request["path"], "/chat/completions")
        self.assertEqual(request["json"]["model"], "deepseek-v4-flash")
        self.assertEqual(request["json"]["temperature"], 0)
        self.assertEqual(request["json"]["thinking"], {"type": "disabled"})
        self.assertEqual(request["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(FakeHttpxClient.last_headers["Authorization"], "Bearer secret-for-test")

    def test_auth_error_does_not_include_upstream_body_or_key(self) -> None:
        FakeHttpxClient.response = httpx.Response(401, text="upstream body contains sensitive diagnostics")
        with patch("medgate.deepseek.httpx.Client", FakeHttpxClient):
            client = DeepSeekClient("secret-for-test")
            with self.assertRaises(DeepSeekError) as raised:
                client.complete(messages=[{"role": "user", "content": "你好"}])
            client.close()
        self.assertEqual(raised.exception.code, "DEEPSEEK_AUTH_FAILED")
        self.assertNotIn("upstream body", raised.exception.message)
        self.assertNotIn("secret-for-test", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
