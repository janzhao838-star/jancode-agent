# -*- coding: utf-8 -*-
"""插话：运行中立刻介入，不是等这轮跑完再排队。

这是用户明确要的语义——插话就是「引导修正问题」，按下发送的那一刻，
当前正在生成的内容立刻作废，模型带着修正重新作答。
"""

import asyncio
import json
import queue
from pathlib import Path

from jancode_agent.agent import Agent
from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.providers import Reply


class FakeClient:
    """第一轮先说一半，然后用户插话；第二轮按修正重答。"""

    def __init__(self, inbox):
        self.calls = 0
        self.inbox = inbox

    async def stream_reply(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "text", "text": "原来的答案"}
            # 模拟用户在生成过程中按了发送
            self.inbox.put("不对，改成讲杭州")
            yield {"type": "text", "text": "这段应该被丢掉"}
        else:
            yield {"type": "text", "text": "按你的修正重答"}

    async def complete(self, messages, tools=None):
        raise AssertionError("不该退回一次性请求")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _cfg(tmp_path):
    return AgentConfig(
        provider=ProviderConfig(name="t", base_url="https://x/v1",
                                model="m", api_key="k"),
        workspace=Path(tmp_path), allow_bash=False)


def test_插话立刻中断并带着修正重答(tmp_path):
    inbox = queue.Queue()
    client = FakeClient(inbox)

    async def go():
        steps = []
        async with Agent(_cfg(tmp_path), client=client) as agent:
            async for step in agent.run("讲个故事", inbox=inbox):
                steps.append(step)
        return steps

    steps = asyncio.run(go())

    # 关键：确实又发了一次请求，而不是把插话排到队尾
    assert client.calls == 2, "插话没有触发重新作答，只调用了 %d 次" % client.calls
    # 界面要能收到「用户插话了」这个信号
    steers = [s for s in steps if s.kind == "steer"]
    assert steers and steers[0].text == "不对，改成讲杭州", steers
    # 第二轮的内容出来了
    deltas = [s for s in steps if s.kind == "answer" and s.delta]
    assert "".join(s.text for s in deltas).endswith("按你的修正重答")


def test_停止置位后立刻收工(tmp_path):
    import threading

    class Slow:
        def __init__(self):
            self.calls = 0

        async def stream_reply(self, messages, tools=None):
            self.calls += 1
            yield {"type": "text", "text": "开头"}
            stop.set()          # 用户点了停止
            yield {"type": "text", "text": "不该再问下一轮"}

        async def complete(self, messages, tools=None):
            raise AssertionError("不该退回一次性请求")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    stop = threading.Event()
    client = Slow()

    async def go():
        steps = []
        async with Agent(_cfg(tmp_path), client=client) as agent:
            async for step in agent.run("随便", stop=stop):
                steps.append(step)
        return steps

    asyncio.run(go())
    assert client.calls == 1, "停止后不该再发请求，实际发了 %d 次" % client.calls


class ToolThenStopClient(FakeClient):
    """第一轮调工具，之后才回文本。stop 在工具期间由测试置位。"""

    def __init__(self, inbox, stop):
        super().__init__(inbox)
        self.stop = stop
        self.second_call = False

    async def complete(self, messages, tools=None):
        # 退回一次性请求也算一轮 LLM 调用——测试断言的是 calls 数量
        self.calls += 1
        self.second_call = True
        return Reply(content="不该走到这里")

    async def stream_reply(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "tools", "tool_calls": [
                {"id": "c1", "name": "read_file", "arguments": json.dumps({"path": "a"})}]}
        else:
            self.second_call = True
            yield {"type": "text", "text": "不该走到这里"}


def test_工具执行后stop立刻兑现不再请求(tmp_path):
    """用户在长工具（bash/MCP 冷启动）运行期间点停止：

    工具结果落库后，下一轮 LLM 请求前必须被拦截——
    原先只在流式循环里查 stop，工具跑完还是会白白请求一轮。
    """
    inbox = queue.Queue()
    stop = asyncio.Event()
    client = ToolThenStopClient(inbox, stop)
    agent = Agent(_cfg(tmp_path), client=client)
    agent.toolbox.allow_bash = False  # 用桩工具走通流程

    # 让 run() 能真的执行 read_file：临时放行真实工具调用
    async def main():
        steps = []
        async for s in agent.run("读一下", inbox=inbox, stop=stop):
            steps.append(s.kind)
            if s.kind == "tool":
                stop.set()  # 模拟用户在工具运行期间点了停止
        return steps

    steps = asyncio.run(main())
    assert client.calls == 1, (
        f"工具后 stop 已置位，不该再发第 {client.calls} 轮 LLM 请求"
    )
    assert "answer" not in steps, "被停止的任务不该有最终回答"
