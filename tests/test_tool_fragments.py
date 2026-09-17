# -*- coding: utf-8 -*-
"""流式输出：工具调用分片归并的测试。

流式下 tool_calls 是按 index 分片到达的，最容易写错的地方是
arguments 跨多片拼接、以及 id/name 只在第一片出现。这里把这两种
真实形状都造出来验证。

报文一律用 json.dumps 构造，不手写 JSON 转义——
手写转义在这个项目里已经坑过好几次（引号被吃掉、拼出坏 JSON）。
"""

import asyncio
import json

import httpx

from jancode_agent.config import ProviderConfig
from jancode_agent.providers import Client

SSE_HEAD = {"Content-Type": "text/event-stream"}


def sse(*chunks):
    """把若干 chunk 拼成 SSE 报文，最后补 [DONE]。"""
    body = "".join("data: " + json.dumps(c) + chr(10) + chr(10) for c in chunks)
    return body + "data: [DONE]" + chr(10)


def delta(**kw):
    return {"choices": [{"delta": kw}]}


def make_client(sse_text):
    def handler(request):
        return httpx.Response(200, text=sse_text, headers=SSE_HEAD)

    provider = ProviderConfig(name="t", base_url="https://x/v1", model="m", api_key="k")
    return Client(provider, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def events(client):
    async def go():
        out = []
        async for ev in client.stream_reply([]):
            out.append(ev)
        return out

    return asyncio.run(go())


def test_工具分片跨多条拼起来():
    # 真实形状：第一片给 id 和函数名，后续片只给 arguments 的片段
    sse_text = sse(
        delta(tool_calls=[{"index": 0, "id": "call_1",
                           "function": {"name": "list_dir", "arguments": '{"pa'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": 'th": "."}'}}]),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    )
    ev = events(make_client(sse_text))
    tools = [e for e in ev if e["type"] == "tools"]
    assert len(tools) == 1
    call = tools[0]["tool_calls"][0]
    assert call["name"] == "list_dir"
    assert call["id"] == "call_1"
    assert call["arguments"] == '{"path": "."}'


def test_两个工具并行分片():
    sse_text = sse(
        delta(tool_calls=[{"index": 0, "id": "a", "function": {"name": "f1", "arguments": "{}"}}]),
        delta(tool_calls=[{"index": 1, "id": "b", "function": {"name": "f2", "arguments": "{}"}}]),
    )
    ev = events(make_client(sse_text))
    calls = [e for e in ev if e["type"] == "tools"][0]["tool_calls"]
    assert [c["name"] for c in calls] == ["f1", "f2"]
    assert [c["id"] for c in calls] == ["a", "b"]


def test_文本和工具可以同时出现():
    # 模型先说了半句话再决定调工具，两种情况都要能收全
    sse_text = sse(
        delta(content="我先看"),
        delta(tool_calls=[{"index": 0, "id": "x", "function": {"name": "f", "arguments": "{}"}}]),
    )
    ev = events(make_client(sse_text))
    assert "".join(e["text"] for e in ev if e["type"] == "text") == "我先看"
    assert [e for e in ev if e["type"] == "tools"][0]["tool_calls"][0]["name"] == "f"
