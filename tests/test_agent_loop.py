"""智能体循环测试。用假模型驱动，不联网。"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from jancode_agent.agent import Agent
from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.providers import Reply, ToolCall


class FakeClient:
    """按预设脚本依次返回回复。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def complete(self, messages, tools=None):
        self.calls.append(list(messages))
        if not self.replies:
            return Reply(content="（脚本用尽）")
        return self.replies.pop(0)


def make_agent(tmp_path: Path, replies) -> Agent:
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="m", api_key="k"),
        workspace=tmp_path,
        max_steps=6,
    )
    return Agent(cfg, client=FakeClient(replies))


def steps_of(agent, prompt):
    async def _go():
        return [s async for s in agent.run(prompt)]
    return asyncio.run(_go())


def test_无工具调用时直接给答案(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="你好")])
    steps = steps_of(agent, "打招呼")
    data = [s for s in steps if s.kind != "usage"]  # usage 步另测
    assert [s.kind for s in data] == ["answer"]
    assert data[0].text == "你好"


def test_工具调用后把结果回灌并继续(tmp_path):
    (tmp_path / "f.txt").write_text("内容", encoding="utf-8")
    agent = make_agent(tmp_path, [
        Reply(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "f.txt"})]),
        Reply(content="文件里是「内容」"),
    ])
    steps = steps_of(agent, "读文件")
    data = [s for s in steps if s.kind != "usage"]
    assert [s.kind for s in data] == ["tool", "tool", "answer"]
    assert data[0].tool_name == "read_file"
    assert "内容" in data[1].text
    # 工具结果必须以 tool 角色进入历史，否则下一轮模型看不到
    roles = [m.role for m in agent.messages]
    assert "tool" in roles


def test_工具失败不中断且告知模型(tmp_path):
    agent = make_agent(tmp_path, [
        Reply(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "缺失.txt"})]),
        Reply(content="文件不存在"),
    ])
    steps = steps_of(agent, "读一个不存在的文件")
    data = [s for s in steps if s.kind != "usage"]
    assert data[1].tool_ok is False
    # 仍然走到了最终答复
    assert steps[-1].kind == "answer"


def test_重复调用被叫停(tmp_path):
    same = lambda: Reply(tool_calls=[ToolCall(id="x", name="list_dir", arguments={"path": "."})])
    agent = make_agent(tmp_path, [same(), same(), same(), same(), same(), same()])
    steps = steps_of(agent, "打转")
    kinds = [s.kind for s in steps]
    assert "error" in kinds
    assert any("重复调用" in s.text for s in steps if s.kind == "error")


def test_达到步数上限会停下(tmp_path):
    # 每轮都调不同参数的工具，绕过重复检测，验证步数上限生效
    replies = [
        Reply(tool_calls=[ToolCall(id=str(i), name="list_dir", arguments={"path": f"{'.' * (i + 1)}"})])
        for i in range(20)
    ]
    agent = make_agent(tmp_path, replies)
    steps = steps_of(agent, "无限循环")
    assert steps[-1].kind == "error"
    assert "最大步数" in steps[-1].text


def test_上下文在多轮之间保留(tmp_path):
    agent = make_agent(tmp_path, [Reply(content="第一次"), Reply(content="第二次")])
    steps_of(agent, "问题一")
    steps_of(agent, "问题二")
    users = [m.content for m in agent.messages if m.role == "user"]
    assert users == ["问题一", "问题二"]
