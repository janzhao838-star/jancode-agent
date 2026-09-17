# -*- coding: utf-8 -*-
"""子智能体结论收集：流式分片必须拼接，不能只留最后一片。"""

import asyncio

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.agent import Agent
from jancode_agent.providers import Reply


class StreamFakeClient:
    def __init__(self, provider):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def stream_reply(self, messages, tools=None):
        last_user = [m.content for m in messages if m.role == "user"][-1]
        if "总结一句" not in last_user:
            yield {"type": "tools", "tool_calls": [
                {"id": "c1", "name": "task",
                 "arguments": '{"description": "查", "prompt": "总结一句"}'},
            ]}
        else:
            for piece in ("第一片。", "第二片。", "第三片结论。"):
                yield {"type": "text", "text": piece}
        return

    async def complete(self, messages, tools=None):
        return Reply(content="不应走到这里")


def _cfg(tmp_path):
    return AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x/v1",
                                model="m", api_key="k"),
        workspace=tmp_path,
        allow_subagents=True,
    )


def test_子任务流式结论完整(tmp_path):
    cfg = _cfg(tmp_path)
    agent = Agent(cfg, client=StreamFakeClient(cfg.provider))

    async def run():
        results = []
        async for step in agent.run("派个子任务"):
            results.append(step)
        return results

    results = asyncio.run(run())
    tool_steps = [s for s in results if s.kind == "tool" and s.result is not None]
    assert tool_steps, "应有工具结果步"
    text = tool_steps[-1].result.output
    assert "第一片" in text and "第三片结论" in text, "结论分片被丢：" + text



def test_子任务非流式结论完整(tmp_path):
    from jancode_agent.providers import ToolCall
    class FullClient(StreamFakeClient):
        async def stream_reply(self, messages, tools=None):
            yield  # 先成为 async generator 再抛，与真客户端形态一致
            raise NotImplementedError

        async def complete(self, messages, tools=None):
            last_user = [m.content for m in messages if m.role == "user"][-1]
            if "总结一句" not in last_user:
                return Reply(content="", tool_calls=[ToolCall(
                    id="c1", name="task",
                    arguments={"description": "查", "prompt": "总结一句"})])
            return Reply(content="整段结论。")

    cfg = _cfg(tmp_path)
    agent = Agent(cfg, client=FullClient(cfg.provider))

    async def run():
        results = []
        async for step in agent.run("派个子任务"):
            results.append(step)
        return results

    results = asyncio.run(run())
    tool_steps = [s for s in results if s.kind == "tool" and s.result is not None]
    assert tool_steps
    assert "整段结论" in tool_steps[-1].result.output
