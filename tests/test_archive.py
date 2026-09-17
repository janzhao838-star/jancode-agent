# -*- coding: utf-8 -*-
"""上下文自动归档：长任务上下文超预算时压桩，不撑爆窗口。"""

import asyncio

from jancode_agent.agent import (
    ARCHIVE_BUDGET,
    ARCHIVE_TOOL_MAX,
    Agent,
)
from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.providers import Message, Reply

from tests.test_agent_loop import FakeClient


def make_agent(tmp_path, replies=()):
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="m", api_key="k"),
        workspace=tmp_path,
        max_steps=6,
    )
    return Agent(cfg, client=FakeClient(list(replies)))


def _big_tool_msgs(agent, n, size=4000):
    """塞 n 组「模型调工具 + 超长工具输出」。"""
    for i in range(n):
        agent.messages.append(Message(role="assistant", content=f"第{i}步",
                                      tool_calls=[]))
        agent.messages.append(Message(role="tool", content="x" * 4000,
                                      tool_call_id=f"call-{i}", name="read_file"))


def test_不超预算不归档(tmp_path):
    agent = make_agent(tmp_path)
    _big_tool_msgs(agent, 3)
    assert agent._maybe_archive(budget=10**9) == 0
    # 原文一字不动
    assert agent.messages[2].content == "x" * 4000


def test_超预算压桩且保留窗口不动(tmp_path):
    agent = make_agent(tmp_path)
    _big_tool_msgs(agent, 10)          # messages 共 21 条（含 system）
    n = agent._maybe_archive(budget=100, keep_recent=4)
    assert n == 8                      # 前 17 条里 8 组工具输出被压
    # 最近 4 条不动（最后两组「assistant+tool」）
    assert agent.messages[-1].content == "x" * 4000
    # 老条目变短桩：有开头、有归档标记、有找回指引
    stub = agent.messages[2].content
    assert stub.startswith("xxx")
    assert "已自动归档" in stub and "read_file" in stub
    assert len(stub) < ARCHIVE_TOOL_MAX + 200


def test_归档幂等_第二轮不再增长(tmp_path):
    agent = make_agent(tmp_path)
    _big_tool_msgs(agent, 10)
    first = agent._maybe_archive(budget=100, keep_recent=4)
    second = agent._maybe_archive(budget=100, keep_recent=4)
    assert first == 8 and second == 0


def test_配对完整_tool_call_id不动(tmp_path):
    agent = make_agent(tmp_path)
    _big_tool_msgs(agent, 6)
    agent._maybe_archive(budget=100, keep_recent=2)
    pairs = [m.tool_call_id for m in agent.messages if m.role == "tool"]
    assert pairs == [f"call-{i}" for i in range(6)]


def test_系统提示词永不归档(tmp_path):
    agent = make_agent(tmp_path)
    _big_tool_msgs(agent, 10)
    agent.messages[0].content = "sys" * 500
    agent._maybe_archive(budget=100, keep_recent=4)
    assert agent.messages[0].content == "sys" * 500


def test_超长助手回复也压(tmp_path):
    agent = make_agent(tmp_path)
    agent.messages.append(Message(role="assistant", content="计" * 5000))
    agent.messages.append(Message(role="user", content="继续"))
    n = agent._maybe_archive(budget=100, keep_recent=1)
    assert n == 1
    assert agent.messages[1].content.startswith("计")
    assert "已自动归档" in agent.messages[1].content
    assert agent.messages[-1].content == "继续"   # 用户的话不动


def test_主循环自动触发归档(tmp_path):
    """跑真实循环：第一步产出超长工具输出，第二步前应被压桩。"""
    (tmp_path / "big.txt").write_text("y" * 90000, encoding="utf-8")
    replies = [
        Reply(tool_calls=[]) ,
    ]
    # 直接构造消息流验证循环内钩子存在且生效：塞满历史后跑一步
    agent = make_agent(tmp_path, [Reply(content="done")])
    _big_tool_msgs(agent, 40)
    steps = []
    async def _go():
        steps.extend([s async for s in agent.run("收尾")])
    asyncio.run(_go())
    # run 追加了用户消息后触发归档：40 组老输出应被压掉
    stubs = [m for m in agent.messages if m.role == "tool" and "已自动归档" in m.content]
    assert len(stubs) >= 20, f"主循环没触发归档，桩只有 {len(stubs)} 条"
    assert steps[-1].kind == "answer"

def test_用户贴的超长日志也压(tmp_path):
    """用户贴 150KB 日志后，归档要把这条也压掉。

    原先只压 tool/assistant——用户消息永不压缩，导致这条日志
    永久留在上下文里，每轮请求都白白多发送十几万字符。
    """
    agent = make_agent(tmp_path)
    big = "x" * 150_000
    agent.messages = [Message(role="system", content="sys")]
    for i in range(30):
        if i == 5:
            agent.messages.append(Message(role="user", content=big))
        else:
            agent.messages.append(Message(role="user", content=f"问{i}"))
        agent.messages.append(Message(role="assistant", content=f"答{i}"))

    n = agent._maybe_archive()
    assert n == 1, f"应当恰好压掉那条日志，实际压了 {n} 条"
    assert len(agent.messages[11].content) < 150_000, "超长用户消息没被压"
    assert agent._context_chars() <= ARCHIVE_BUDGET, "归档后仍超预算"
    assert "已自动归档" in agent.messages[11].content


def test_最近窗口内的用户长消息不压(tmp_path):
    """keep_recent 保护窗内的消息不动：模型正在处理的不能压掉。"""
    agent = make_agent(tmp_path)
    big = "x" * 150_000
    agent.messages = [Message(role="system", content="sys")]
    for i in range(6):
        if i == 3:
            agent.messages.append(Message(role="user", content=big))
        else:
            agent.messages.append(Message(role="user", content=f"问{i}"))
        agent.messages.append(Message(role="assistant", content=f"答{i}"))
    n = agent._maybe_archive()
    assert n == 0, "对话太短全在保护窗内，不该压任何东西"
