# -*- coding: utf-8 -*-
"""流式与工具调用共存的行为测试。

这是改流式时最大的回归风险：工具回合必须先收全 tool_calls 再执行，
而文字回答要边走边显示。两种事件混在同一个流里，收错顺序就会出现
「参数没拼完就去执行」或「工具结果丢了」。真实模型实测走通过了，
这里用假客户端把它固定下来，免得以后改坏。
"""

import asyncio
from pathlib import Path

from jancode_agent.agent import Agent
from jancode_agent.config import AgentConfig, ProviderConfig


class FakeClient:
    """第一次回复要求调工具，第二次给出文字答案。"""

    def __init__(self):
        self.rounds = 0

    async def stream_reply(self, messages, tools=None):
        self.rounds += 1
        if self.rounds == 1:
            yield {"type": "tools", "tool_calls": [
                {"id": "c1", "name": "read_file", "arguments": '{"path": "a.txt"}'}
            ]}
        else:
            for ch in "内容是 hi":
                yield {"type": "text", "text": ch}

    async def complete(self, messages, tools=None):
        raise AssertionError("有流式可用时不该退回一次性请求")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_工具回合与流式共存(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="https://x/v1",
                               model="m", api_key="k"),
        workspace=Path(tmp_path),
        allow_bash=False,
    )
    client = FakeClient()

    async def go():
        steps = []
        async with Agent(cfg, client=client) as agent:
            async for step in agent.run("读一下 a.txt"):
                steps.append(step)
        return steps

    steps = asyncio.run(go())
    kinds = [s.kind for s in steps]

    # 工具被真的调用了，而且拿到了文件内容
    tool_steps = [s for s in steps if s.kind == "tool"]
    assert tool_steps, "没有产生工具步骤：%s" % kinds
    assert tool_steps[0].tool_name == "read_file"
    # 工具结果回灌进了历史（否则模型下一轮无从作答）
    joined = " ".join(s.text for s in tool_steps)
    assert "hi" in joined, "工具结果没进历史：%r" % joined

    # 文字是逐块流出来的，不是最后一次性给的
    deltas = [s for s in steps if s.kind == "answer" and s.delta]
    assert len(deltas) >= 3, "文字没有分块流出：%d 块" % len(deltas)
    assert "".join(s.text for s in deltas).strip() == "内容是 hi"
    # 最后要有一个结束标记，界面据此收掉「正在输入」
    assert steps[-1].kind == "answer" and not steps[-1].delta
