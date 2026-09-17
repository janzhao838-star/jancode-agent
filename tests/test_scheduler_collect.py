# -*- coding: utf-8 -*-
"""定时任务结论收集：流式分片必须拼接，空收尾标记不能清空结论。

和子智能体收集是同源 bug：覆盖式赋值碰上 delta 分片 + 空收尾标记，
任务跑成了记录却是「（没有结论）」。"""


import asyncio

import tempfile

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.agent import Agent
from jancode_agent.scheduler import start as start_scheduler


class DeltaClient:
    def __init__(self, provider): pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def stream_reply(self, messages, tools=None):
        for piece in ("结论甲。", "结论乙。", "最终结论。"):
            yield {"type": "text", "text": piece}


def test_定时任务流式结论完整():
    with tempfile.TemporaryDirectory() as d:
        cfg = AgentConfig(
            provider=ProviderConfig(name="t", base_url="http://x/v1",
                                    model="m", api_key="k"),
            workspace=d,
        )
        agent = Agent(cfg, client=DeltaClient(cfg.provider))

        async def drive():
            parts = []
            async for step in agent.run("你好"):
                if step.kind == "answer" and not step.subagent:
                    if step.delta:
                        parts.append(step.text)
                    elif step.text:
                        parts = [step.text]
            return "".join(parts)

        assert asyncio.run(drive()) == "结论甲。结论乙。最终结论。"


def test_调度线程幂等():
    """重复 start 不起第二个线程（额度双倍消耗的老坑）。"""
    class FakeConfig:
        workspace = "."

        provider = ProviderConfig(name="t", base_url="http://x/v1",
                                  model="m", api_key="k")

    t1 = start_scheduler(FakeConfig(), interval=9999.0)
    t2 = start_scheduler(FakeConfig(), interval=9999.0)
    assert t1 is t2