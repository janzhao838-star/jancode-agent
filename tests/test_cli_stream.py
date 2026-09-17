# -*- coding: utf-8 -*-
"""CLI 流式输出：分片必须连排，版式与非流式一致。

此前每片都按整段打印，夹着空行碎成一列，收尾空标记还会多打空行。
"""

import asyncio

import contextlib
import io
import tempfile

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.agent import Agent
from jancode_agent.cli import _run_once


class DeltaClient:
    def __init__(self, provider): pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def stream_reply(self, messages, tools=None):
        for piece in ("第一句。", "第二句。", "第三句。"):
            yield {"type": "text", "text": piece}


def _capture(cfg, prompt):
    agent = Agent(cfg, client=DeltaClient(cfg.provider))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        asyncio.run(_run_once(agent, prompt, verbose=True))
    return buf.getvalue()


def test_cli流式版式连排():
    with tempfile.TemporaryDirectory() as d:
        cfg = AgentConfig(
            provider=ProviderConfig(name="t", base_url="http://x/v1",
                                    model="m", api_key="k"),
            workspace=d,
        )
        out = _capture(cfg, "你好")
        assert "第一句。第二句。第三句。" in out, repr(out)
        assert out == "\n第一句。第二句。第三句。\n", repr(out)


def test_cli非流式版式不变():
    class FullClient(DeltaClient):
        async def stream_reply(self, messages, tools=None):
            yield  # async generator 形态
            raise NotImplementedError

        async def complete(self, messages, tools=None):
            from jancode_agent.providers import Reply
            return Reply(content="整段回答。")

    with tempfile.TemporaryDirectory() as d:
        cfg = AgentConfig(
            provider=ProviderConfig(name="t", base_url="http://x/v1",
                                    model="m", api_key="k"),
            workspace=d,
        )
        agent = Agent(cfg, client=FullClient(cfg.provider))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            asyncio.run(_run_once(agent, "你好", verbose=True))
        assert buf.getvalue() == "\n整段回答。\n", repr(buf.getvalue())
