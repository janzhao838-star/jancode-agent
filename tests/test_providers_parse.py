# -*- coding: utf-8 -*-
"""providers.py 解析层：坏分片过滤、流式 tool_calls 拼接、空响应报错。"""

import asyncio
import json

import pytest

from jancode_agent.config import ProviderConfig
from jancode_agent.providers import Client, ProviderError, reply_from_payload


def _client():
    return Client(ProviderConfig(
        name="t", base_url="http://127.0.0.1:9/v1",
        model="m", api_key="k",
    ))


def test_空名工具分片被过滤():
    data = {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [
                    {"id": "a", "function": {"name": "bash", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "", "arguments": "{}"}},
                ],
            },
            "finish_reason": "tool_calls",
        }],
    }
    r = reply_from_payload(data)
    assert [c.name for c in r.tool_calls] == ["bash"], r.tool_calls


def test_参数坏JSON回退_raw():
    data = {
        "choices": [{"message": {"tool_calls": [
            {"id": "x", "function": {"name": "t", "arguments": "{bad,"}},
        ]}}],
    }
    r = reply_from_payload(data)
    assert r.tool_calls[0].arguments == {"_raw": "{bad,"}


def test_响应没有choices报错():
    c = _client()
    with pytest.raises(ProviderError):
        c._parse_chat({})



def test_流式分片拼接():
    import httpx
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "bash", "arguments": ""}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"command": '}},
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"ls"}'}},
        ]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    body = "".join(
        "data: " + json.dumps(ch) + chr(10) for ch in chunks
    ) + "data: [DONE]" + chr(10)

    def handler(request):
        return httpx.Response(200, content=body.encode(),
                              headers={"content-type": "text/event-stream"})

    c = _client()
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run():
        events = []
        async for ev in c.stream_reply([]):
            events.append(ev)
        return events

    events = asyncio.run(run())
    tools_ev = [e for e in events if e["type"] == "tools"]
    assert tools_ev and tools_ev[0]["tool_calls"][0]["name"] == "bash"
    assert tools_ev[0]["tool_calls"][0]["arguments"] == '{"command": "ls"}'  # 原始参数串，json 解析在 agent 层做
