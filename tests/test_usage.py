# -*- coding: utf-8 -*-
"""token 用量统计：流式 usage 捕获 → 智能体累计 → 事件送到界面。

中转站默认在流式里不回 usage，必须显式 stream_options.include_usage。
这条链路任何一处断了，界面下方的统计行就是死的。
"""

import asyncio
import json

import httpx

from jancode_agent.agent import Agent
from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.providers import Client, Reply

from jancode_agent.providers import ToolCall
from tests.mock_server import chat_reply, start

SSE_HEAD = {"Content-Type": "text/event-stream"}


def make_client(sse_text):
    def handler(request):
        return httpx.Response(200, text=sse_text, headers=SSE_HEAD)

    provider = ProviderConfig(name="t", base_url="https://x/v1", model="m", api_key="k")
    return Client(provider, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def collect_events(client):
    async def go():
        out = []
        async for event in client.stream_reply([]):
            out.append(event)
        return out

    return asyncio.run(go())


def make_agent(tmp_path, replies):
    class FakeClient:
        def __init__(self, replies):
            self.replies = list(replies)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def complete(self, messages, tools=None):
            return self.replies.pop(0)

    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="m", api_key="k"),
        workspace=tmp_path,
        max_steps=6,
    )
    return Agent(cfg, client=FakeClient(replies))


def run(agent, prompt):
    async def _go():
        async with agent:
            return [s async for s in agent.run(prompt)]
    return asyncio.run(_go())


def test_流式末尾的usage被捕获为事件():
    sse = (
        'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":100,"completion_tokens":20,'
        '"prompt_tokens_details":{"cached_tokens":60}}}\n\n'
        "data: [DONE]\n\n"
    )
    events = collect_events(make_client(sse))
    kinds = [e["type"] for e in events]
    assert "usage" in kinds, f"流式没吐 usage 事件：{kinds}"
    u = [e for e in events if e["type"] == "usage"][0]["usage"]
    assert u["prompt_tokens"] == 100 and u["completion_tokens"] == 20


def test_请求带上include_usage选项():
    """不加这个字段多数网关不回 usage，统计就断了。"""
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        sse = (
            'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse, headers=SSE_HEAD)

    provider = ProviderConfig(name="t", base_url="https://x/v1", model="m", api_key="k")
    client = Client(provider, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    collect_events(client)
    assert seen["body"].get("stream_options", {}).get("include_usage") is True


def test_无usage的流式静默跳过():
    sse = (
        'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    events = collect_events(make_client(sse))
    assert all(e["type"] != "usage" for e in events)


def test_智能体累计turns与token(tmp_path):
    replies = [
        Reply(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "f.txt"})],
              usage={"prompt_tokens": 10, "completion_tokens": 5,
                     "prompt_tokens_details": {"cached_tokens": 4}}),
        Reply(content="第二句", usage={"prompt_tokens": 30, "completion_tokens": 15}),
    ]
    agent = make_agent(tmp_path, replies)
    steps = run(agent, "问一句")
    usage_steps = [s for s in steps if s.kind == "usage"]
    assert len(usage_steps) == 2, "每轮回复后都该吐一个 usage 步"
    final = usage_steps[-1].usage
    assert final["turns"] == 2
    assert final["prompt_tokens"] == 40 and final["completion_tokens"] == 20
    assert final["cached_tokens"] == 4


def test_工具调用计入steps(tmp_path):
    (tmp_path / "f.txt").write_text("内容", encoding="utf-8")
    replies = [
        Reply(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "f.txt"})],
              usage={"prompt_tokens": 10, "completion_tokens": 5}),
        Reply(content="读完", usage={"prompt_tokens": 20, "completion_tokens": 8}),
    ]
    agent = make_agent(tmp_path, replies)
    steps = run(agent, "读文件")
    final = [s for s in steps if s.kind == "usage"][-1].usage
    assert final["steps"] == 1
    assert final["turns"] == 2


def test_用量走真实HTTP全链路(tmp_path):
    """mock 服务回完整 JSON（智能体走 full 兜底路径），usage 要能并进累计器。"""
    url, httpd = start([chat_reply("你好")])
    try:
        cfg = AgentConfig(
            provider=ProviderConfig(name="mock", base_url=url, model="m", api_key="sk-test"),
            workspace=tmp_path, max_steps=5,
        )
        agent = Agent(cfg)
        async def _go():
            async with agent:
                return [s async for s in agent.run("打个招呼")]
        steps = asyncio.run(_go())
        final = [s for s in steps if s.kind == "usage"]
        assert final, "真实 HTTP 链路上没吐 usage 步"
    finally:
        httpd.shutdown()


def test_usage帧在finish_reason之后也能收到():
    sse = (
        'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3}}\n\n'
        "data: [DONE]\n\n"
    )
    events = collect_events(make_client(sse))
    u = [e for e in events if e["type"] == "usage"]
    kinds = [e["type"] for e in events]
    assert u and u[0]["usage"]["prompt_tokens"] == 7, kinds

def test_cli一次性运行结束打印用量摘要(tmp_path, capsys):
    """CLI 用户也该看到 token 消耗——界面有统计行，终端不能没有。"""
    import asyncio
    from jancode_agent.cli import _run_once
    from jancode_agent.config import AgentConfig, ProviderConfig

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def complete(self, messages, tools=None):
            return Reply(content="答案", usage={"prompt_tokens": 100,
                "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 50}})

    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="m", api_key="k"),
        workspace=tmp_path, max_steps=4,
    )
    agent = Agent(cfg, client=FakeClient())

    async def go():
        async with agent:
            return await _run_once(agent, "问", verbose=False)

    code = asyncio.run(go())
    out = capsys.readouterr().out
    assert code == 0
    assert "1 轮 0 步" in out and "120 tok" in out, out
    assert "缓存命中 50%" in out, out


def test_cli无usage时安静收场(tmp_path, capsys):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def complete(self, messages, tools=None):
            return Reply(content="答案")

    from jancode_agent.cli import _run_once
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="m", api_key="k"),
        workspace=tmp_path, max_steps=4,
    )
    agent = Agent(cfg, client=FakeClient())

    async def go():
        async with agent:
            return await _run_once(agent, "问", verbose=False)

    code = asyncio.run(go())
    out = capsys.readouterr().out
    assert code == 0 and "——" not in out, out
