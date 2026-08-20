import unittest
from unittest.mock import MagicMock, patch

import httpx

from medgate.deepseek import ChatResult, DeepSeekClient


class UsagePersistenceTest(unittest.TestCase):
    def test_chat_result_holds_usage(self) -> None:
        r = ChatResult(content="hi", response_id="1", model="m", finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        self.assertEqual(r.usage["total_tokens"], 30)

    def test_deepseek_client_parses_usage(self) -> None:
        # Mock httpx response with usage
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "model": "deepseek-v4-flash",
            "choices": [{"message": {"content": "hello", "role": "assistant"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }
        mock_response.headers = {}
        with patch("httpx.Client.post", return_value=mock_response):
            client = DeepSeekClient(api_key="test-key", base_url="https://api.deepseek.com")
            result = client.complete(messages=[{"role": "user", "content": "hi"}])
            self.assertIsNotNone(result.usage)
            self.assertEqual(result.usage["prompt_tokens"], 100)
            self.assertEqual(result.usage["total_tokens"], 150)
            client.close()

    def test_trace_holds_usage(self) -> None:
        from medgate.agent import Trace

        t = Trace(
            trace_id="t1",
            repeat_no=1,
            role="candidate",
            case_id="case-001",
            turn_no=1,
            request_hash="h",
            system_prompt_hash="s",
            input_hash="i",
            started_at="2026-08-20T00:00:00Z",
            duration_ms=100,
            response_id="r1",
            model="m",
            finish_reason="stop",
            output="hi",
            output_hash="h",
            estimated_input_tokens=10,
            max_output_tokens=512,
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        self.assertEqual(t.usage["total_tokens"], 30)
