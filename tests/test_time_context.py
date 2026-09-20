# -*- coding: utf-8 -*-
"""时间上下文：系统提示词必须带当前时间。

模型自己没有钟表。用户说「今天」「这周」「明天九点」时，没有时间
锚点的模型要么瞎猜日期，要么写死一个训练截止附近的年份。
把真实时间拼进系统提示词，一行成本，换来所有相对时间说法可靠。
"""

from jancode_agent.agent import Agent
from jancode_agent.config import AgentConfig, ProviderConfig


def _agent():
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x:1/v1", model="m", api_key="k"),
    )
    return Agent(cfg)


def test_系统提示词带当前日期和星期():
    text = _agent().system_prompt()
    assert "当前时间：" in text
    assert any(w in text for w in ("周一", "周二", "周三", "周四", "周五", "周六", "周日"))


def test_时间在角色提示词之前不被覆盖():
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x:1/v1", model="m", api_key="k"),
        system_extra="你是数字专家。",
    )
    a = Agent(cfg)
    text = a.system_prompt()
    assert text.index("当前时间：") < text.index("你是数字专家。")

