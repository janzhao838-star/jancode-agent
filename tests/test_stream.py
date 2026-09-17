# -*- coding: utf-8 -*-
"""流式输出的解析测试。

重点覆盖三条容易出错的路径：被切断的半行、多个 data 行、
以及接口不支持 stream 时能不能干净地抛错（由上层退回一次性请求）。
不依赖真实网络：用 httpx 的 MockTransport 造响应。

注意：SSE 报文用三引号写成「真实换行」，不写反斜杠 n 转义——
之前就是因为转义层数太多把字符串写坏过。
"""

import asyncio

import httpx
import pytest

from jancode_agent.config import ProviderConfig
from jancode_agent.providers import Client, ProviderError

SSE_HEAD = {"Content-Type": "text/event-stream"}


def make_client(sse_text, status=200):
    def handler(request):
        return httpx.Response(status, text=sse_text, headers=SSE_HEAD)

    provider = ProviderConfig(name="t", base_url="https://x/v1", model="m", api_key="k")
    return Client(provider, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def collect(client):
    async def go():
        out = []
        async for piece in client.stream_chat([]):
            out.append(piece)
        return "".join(out)

    return asyncio.run(go())


def test_解析基本增量():
    sse = """data: {"choices":[{"delta":{"content":"你"}}]}

data: {"choices":[{"delta":{"content":"好"}}]}

data: [DONE]

"""
    assert collect(make_client(sse)) == "你好"


def test_跳过心跳和空行():
    sse = """: keep-alive

data: {"choices":[{"delta":{"content":"甲"}}]}

data: [DONE]
"""
    assert collect(make_client(sse)) == "甲"


def test_坏行不影响后续():
    # 中间夹一条被切断的 JSON：应该跳过它继续读后面的内容，
    # 而不是因为一行坏了就让整轮回答失败。
    sse = """data: {"choices":[{"delta":{"content":"前"}}]}

data: {"choices":[{"delta":{"con

data: {"choices":[{"delta":{"content":"后"}}]}

data: [DONE]
"""
    assert collect(make_client(sse)) == "前后"


def test_多个data行():
    sse = """data: {"choices":[{"delta":{"content":"a"}}]}
data: {"choices":[{"delta":{"content":"b"}}]}
data: [DONE]
"""
    assert collect(make_client(sse)) == "ab"


def test_接口不支持stream时用完整响应兜底():
    # 网关无视 stream、返回普通 JSON 时，不该白跑一趟再重发请求——
    # 网关按机器限并发时，多打一发会实打实多占一次配额。
    # 直接把这份完整响应当成回答，行为更正确。
    client = make_client('{"choices":[{"message":{"content":"完整回答"}}]}')
    assert collect(client) == "完整回答"


def test_错误状态码抛错():
    with pytest.raises(ProviderError):
        collect(make_client("nope", status=400))


def test_responses协议直接退回():
    provider = ProviderConfig(
        name="t", base_url="https://x/v1", model="m", api_key="k", wire_api="responses"
    )
    client = Client(
        provider,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    with pytest.raises(ProviderError):
        collect(client)
