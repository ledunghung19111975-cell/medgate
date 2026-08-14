from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEEPSEEK_TEMPERATURE = 0
DEEPSEEK_THINKING = {"type": "disabled"}
DEEPSEEK_STREAM = True


class DeepSeekError(Exception):
    """A sanitized DeepSeek failure safe to return from the local API."""

    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ChatResult:
    content: str
    response_id: str | None
    model: str
    finish_reason: str | None


@dataclass(frozen=True)
class ChatDelta:
    content: str
    response_id: str | None
    model: str
    finish_reason: str | None


class ChatClient(Protocol):
    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        max_tokens: int = 512,
    ) -> ChatResult: ...


class DeepSeekClient:
    """Minimal OpenAI-compatible client with no credential logging."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEEPSEEK_MODEL,
        base_url: str = DEEPSEEK_BASE_URL,
        timeout_seconds: float = 45.0,
    ) -> None:
        if not api_key.strip():
            raise DeepSeekError(
                "DEEPSEEK_API_KEY_MISSING",
                "本机未配置 DEEPSEEK_API_KEY，无法执行真实模型评测",
                503,
            )
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout_seconds),
        )

    def close(self) -> None:
        self._client.close()

    def complete(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        max_tokens: int = 512,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": DEEPSEEK_TEMPERATURE,
            "thinking": DEEPSEEK_THINKING,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise DeepSeekError("DEEPSEEK_TIMEOUT", "DeepSeek 请求超时，请稍后重试", 504) from exc
        except httpx.RequestError as exc:
            raise DeepSeekError("DEEPSEEK_UNREACHABLE", "无法连接 DeepSeek，请检查本机网络", 502) from exc

        if response.status_code in {401, 403}:
            raise DeepSeekError("DEEPSEEK_AUTH_FAILED", "DeepSeek 鉴权失败，请检查本机 API Key", 502)
        if response.status_code == 429:
            raise DeepSeekError("DEEPSEEK_RATE_LIMITED", "DeepSeek 请求频率受限，请稍后重试", 503)
        if response.status_code >= 500:
            raise DeepSeekError("DEEPSEEK_UNAVAILABLE", "DeepSeek 服务暂时不可用，请稍后重试", 502)
        if response.status_code >= 400:
            raise DeepSeekError("DEEPSEEK_REQUEST_REJECTED", "DeepSeek 拒绝了本次请求，请检查模型配置", 502)

        try:
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise TypeError("message.content must be a string")
            return ChatResult(
                content=content,
                response_id=str(body["id"]) if body.get("id") else None,
                model=str(body.get("model") or self.model),
                finish_reason=str(choice["finish_reason"]) if choice.get("finish_reason") else None,
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekError("DEEPSEEK_INVALID_RESPONSE", "DeepSeek 返回结构无法解析，本次运行未生成门禁结论", 502) from exc

    def stream(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        max_tokens: int = 512,
    ) -> Iterator[ChatDelta]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": DEEPSEEK_TEMPERATURE,
            "thinking": DEEPSEEK_THINKING,
            "max_tokens": max_tokens,
            "stream": DEEPSEEK_STREAM,
        }
        if response_format:
            payload["response_format"] = response_format
        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code in {401, 403}:
                    raise DeepSeekError("DEEPSEEK_AUTH_FAILED", "DeepSeek 鉴权失败，请检查本机 API Key", 502)
                if response.status_code == 429:
                    raise DeepSeekError("DEEPSEEK_RATE_LIMITED", "DeepSeek 请求频率受限，请稍后重试", 503)
                if response.status_code >= 500:
                    raise DeepSeekError("DEEPSEEK_UNAVAILABLE", "DeepSeek 服务暂时不可用，请稍后重试", 502)
                if response.status_code >= 400:
                    raise DeepSeekError("DEEPSEEK_REQUEST_REJECTED", "DeepSeek 拒绝了本次请求，请检查模型配置", 502)

                for raw_line in response.iter_lines():
                    line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        body = json.loads(data)
                        choice = body["choices"][0]
                        delta = choice.get("delta") or {}
                        content = delta.get("content") or ""
                        if isinstance(content, list):
                            content = "".join(
                                str(item.get("text", ""))
                                for item in content
                                if isinstance(item, dict)
                            )
                        if not isinstance(content, str):
                            raise TypeError("delta.content must be a string")
                        yield ChatDelta(
                            content=content,
                            response_id=str(body["id"]) if body.get("id") else None,
                            model=str(body.get("model") or self.model),
                            finish_reason=str(choice["finish_reason"]) if choice.get("finish_reason") else None,
                        )
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise DeepSeekError(
                            "DEEPSEEK_INVALID_RESPONSE",
                            "DeepSeek 流式返回结构无法解析，本次运行未生成门禁结论",
                            502,
                        ) from exc
        except DeepSeekError:
            raise
        except httpx.TimeoutException as exc:
            raise DeepSeekError("DEEPSEEK_TIMEOUT", "DeepSeek 请求超时，请稍后重试", 504) from exc
        except httpx.RequestError as exc:
            raise DeepSeekError("DEEPSEEK_UNREACHABLE", "无法连接 DeepSeek，请检查本机网络", 502) from exc
