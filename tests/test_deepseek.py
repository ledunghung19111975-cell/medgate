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
