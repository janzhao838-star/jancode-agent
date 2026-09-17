# -*- coding: utf-8 -*-
"""429 退避重试：网关按机器限并发，等几秒通常就好。

这里不真等 3/6/9 秒——把 sleep 打桩成 0，只验证：
重试确实发生、成功后正常返回、三次都 429 才报用户能看懂的错。
"""

import asyncio
import json

import httpx

from jancode_agent.config import ProviderConfig
from jancode_agent.providers import Message
from jancode_agent.providers import Client, ProviderError


def make_client(responder):
    provider = ProviderConfig(name="t", base_url="https://x/v1", model="m", api_key="k")
    return Client(provider, httpx.AsyncClient(transport=httpx.MockTransport(responder)))


def complete_with(client, monkeypatch):
    async def _nosleep(_s):
        return
    monkeypatch.setattr("jancode_agent.providers.asyncio.sleep", _nosleep)
    return asyncio.run(client.complete([Message(role="user", content="问")]))


def chat_body():
    return {"choices": [{"message": {"role": "assistant", "content": "好"},
            "finish_reason": "stop"}], "usage": {"total_tokens": 1}}


def test_429重试后成功(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, text='{"error":"并发满"}')
        return httpx.Response(200, json=chat_body())

    reply = complete_with(make_client(handler), monkeypatch)
    assert reply.content == "好"
    assert len(calls) == 3, f"应当重试到第 3 次成功，实际请求 {len(calls)} 次"


def test_429重试三次仍失败报人话(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, text='{"error":"这台电脑已有两个进行中的请求"}')

    try:
        complete_with(make_client(handler), monkeypatch)
        raise AssertionError("应当抛 ProviderError")
    except ProviderError as exc:
        msg = str(exc)
        assert "429" in msg and "并发" in msg, msg
        assert "这台电脑已有两个进行中的请求" in msg, "原始说明要透出，排查全靠它"
    # 首发 + 3 次重试 = 4 次
    assert len(calls) == 4, f"实际请求 {len(calls)} 次"


def test_非429错误不重试(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text='{"error":{"message":"Invalid token"}}')

    try:
        complete_with(make_client(handler), monkeypatch)
        raise AssertionError("应当抛 ProviderError")
    except ProviderError as exc:
        assert "401" in str(exc)
    assert len(calls) == 1, "401 不是限流，重试没有意义"
