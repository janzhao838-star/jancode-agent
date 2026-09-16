"""智能体循环。

核心是一个「模型说要做什么 → 执行 → 把结果喂回去」的循环。
三个关键取舍：

1. 循环有硬上限（max_steps）。模型陷入反复尝试时，必须有外力叫停，
   否则会一直消耗额度。
2. 工具失败不中断会话，而是把失败原因作为工具结果回灌给模型。
   大多数情况下模型能自己纠正（换个路径、先读文件再改）。
3. 连续多次调用完全相同的工具和参数时提前停：这是循环卡死的典型信号，
   继续跑下去只是在烧钱。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable

from .config import AgentConfig
from .providers import Client, Message, ProviderError, ToolCall
from .tools import Toolbox

SYSTEM_PROMPT = """你是一个终端里的编程助手，可以直接读写文件、执行命令。

工作方式：
- 动手改代码前，先用 read_file 看清楚原文。不要凭记忆改文件。
- 修改已有文件用 edit_file，新建文件用 write_file。
- 改完代码后，尽量用 bash 跑一次测试或构建，用实际结果确认改动生效。
- 需要了解项目结构时用 list_dir，找代码位置用 grep。

准则：
- 用中文回复。
- 不要臆测文件内容或命令输出。不确定就去看。
- 完成任务后简要说明改了什么，不要罗列工具调用过程。
- 遇到明确的失败要如实报告，不要假装成功。"""


@dataclass
class Step:
    """循环中的一步，用于向调用方汇报进度。"""

    kind: str  # thinking | tool | answer | error
    text: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_ok: bool = True


class Agent:
    """把模型、工具、循环控制拼在一起。"""

    def __init__(self, config: AgentConfig, client: Client | None = None):
        self.config = config
        self.toolbox = Toolbox(
            workspace=config.workspace,
            allow_bash=config.allow_bash,
            bash_timeout=config.bash_timeout,
            max_output=config.max_tool_output,
        )
        self._client = client
        self._owns_client = client is None
        self.messages: list[Message] = [Message(role="system", content=SYSTEM_PROMPT)]

    async def __aenter__(self) -> "Agent":
        if self._client is None:
            self._client = Client(self.config.provider)
            await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    async def run(self, prompt: str) -> AsyncIterator[Step]:
        """跑一轮完整任务。以异步生成器形式产出每一步，便于实时显示。"""
        if self._client is None:
            self._client = Client(self.config.provider)
            await self._client.__aenter__()

        self.messages.append(Message(role="user", content=prompt))
        specs = self.toolbox.specs()
        seen: list[tuple[str, str]] = []  # 重复调用检测

        for step_no in range(1, self.config.max_steps + 1):
            try:
                reply = await self._client.complete(self.messages, specs)
            except ProviderError as exc:
                yield Step("error", tool_ok=False, text=str(exc))
                return

            if not reply.wants_tools:
                text = reply.content.strip() or "（模型没有返回内容）"
                self.messages.append(Message(role="assistant", content=text))
                yield Step("answer", text=text)
                return

            self.messages.append(Message(
                role="assistant",
                content=reply.content,
                tool_calls=reply.tool_calls,
            ))

            for tc in reply.tool_calls:
                yield Step("tool", tool_name=tc.name, tool_args=tc.arguments, text="")

                # 重复调用检测：同工具同参数连续出现，说明模型在打转
                signature = (tc.name, json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False))
                if seen.count(signature) >= 2:
                    note = (
                        f"检测到重复调用 {tc.name} 且参数完全相同，已中止。"
                        f"可能是任务无法按当前方式完成，请检查工作目录或换一种思路。"
                    )
                    self.messages.append(Message(
                        role="tool", content=note, tool_call_id=tc.id, name=tc.name,
                    ))
                    yield Step("error", tool_ok=False, text=note)
                    return
                seen.append(signature)

                result = await self.toolbox.call(tc.name, tc.arguments)
                yield Step(
                    "tool",
                    tool_name=tc.name,
                    tool_args=tc.arguments,
                    tool_ok=result.ok,
                    text=result.render(),
                )
                self.messages.append(Message(
                    role="tool",
                    content=result.render(),
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

        yield Step("error", tool_ok=False, text=f"已达最大步数 {self.config.max_steps}，任务未完成。可用 --max-steps 放宽上限。")
