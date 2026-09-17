"""智能体循环。

核心是一个「模型说要做什么 → 执行 → 把结果喂回去」的循环。
三个关键取舍：

1. 循环有硬上限（max_steps）。模型陷入反复尝试时，必须有外力叫停，
   否则会一直消耗额度。
2. 工具失败不中断会话，而是把失败原因作为工具结果回灌给模型。
   大多数情况下模型能自己纠正（换个路径、先读文件再改）。
3. 连续多次调用完全相同的工具和参数时提前停：这是循环卡死的典型信号，
   继续跑下去只是在烧钱。

关于子智能体（子智能体 = 由主智能体派生、上下文独立的另一个智能体）：
它的价值全在「上下文隔离」——中间翻了多少文件、跑了多少命令，
都不会进入主智能体的历史，只有最后一条答复交回来。
所以派生逻辑写在这里，而不是 Toolbox 里：需要新建一个循环、还要管派生深度。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import AsyncIterator, Callable

from .config import AgentConfig
from .providers import Client, Message, ProviderError, ToolCall, Reply
from .mcp import manager
from .tools import ToolResult, Toolbox

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

# 只有在 task 工具真的可用时才追加。提示词里说了有、工具列表里却没有，
# 模型会反复调用一个不存在的工具，把步数耗光。
SUBAGENT_SECTION = """

关于 task 工具（子智能体）：
- 子智能体有自己的上下文，**看不到我们这段对话**，只能看到你在 prompt 里写的内容。
  所以 prompt 必须自包含：写清楚目标、已知条件、要交付什么。
- 它跑完只把最终结论交回来，中间翻了哪些文件、跑了哪些命令都不会进入我们的上下文。
  因此「派它去查，拿结论回来」比你自己逐个文件读一遍更省上下文。
- 适合：要在很多文件里翻找才能回答的调查类问题、边界清晰且可独立完成的改造。
- 不适合：答案依赖我们刚才对话里的小事——那种直接做更快。"""

# 子智能体自己的系统提示词。要点是「最后一条答复会被原样交回去」，
# 所以它必须自包含；否则主智能体只拿到一句「已完成」，等于什么也没得到。
SUBAGENT_SYSTEM_PROMPT = """你是子智能体，被主智能体派来执行一个边界清晰的子任务。

工作方式与准则和主智能体一致：先 read_file 看清原文再改，改完尽量跑一次测试确认，
用中文回复，不确定就去看，失败要如实报告。

环境细节：这台机器上 Python 的命令名是 python3，没有 python。
跑脚本或查版本一律用 python3，别用 python（会报 command not found）。

你的处境和主智能体不同，有三点必须记住：
- 你看不到主智能体和用户的对话，只能依据任务说明办事。任务说明没写的信息就是没有。
- 你的中间过程不会进入主智能体的上下文，**只有你最后一条答复会被原样交回去**。
  所以最后一条答复必须自包含：结论是什么、依据是什么、改动了哪些文件。
- 信息不足以完成任务时不要猜，在最后一条答复里明确指出缺什么。"""


@dataclass
class Step:
    """循环中的一步，用于向调用方汇报进度。"""

    kind: str  # thinking | tool | answer | error
    text: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_ok: bool = True
    # 非空表示这一步来自子智能体，值是子智能体的标签（界面据此缩进/标注）。
    subagent: str = ""
    # True 表示这只是一个文字片段，界面要追加而不是替换。
    # 流式输出的关键：整段回答会拆成很多个 delta 事件发出去。
    delta: bool = False
    # 内部使用：工具结果本身。只有「工具执行完」那一步会带上它，
    # 供 run() 把结果回灌进历史。不参与发给界面的序列化。
    result: ToolResult | None = None



def drain_inbox(inbox) -> str:
    """取走累积的插话并拼成一条。没有就跑空。

    inbox 用 queue.Queue：插话是从另一个 HTTP 线程塞进来的。
    """
    if inbox is None:
        return ""
    got = []
    while True:
        try:
            item = inbox.get_nowait()
        except Exception:
            break
        if item:
            got.append(str(item))
    return "\n".join(got)

class Agent:
    """把模型、工具、循环控制拼在一起。"""

    def __init__(self, config: AgentConfig, client: Client | None = None, depth: int = 0):
        self.config = config
        # 派生深度：0 是主智能体，1 是第一层子智能体。用来兜住无限套娃。
        self.depth = depth
        self.toolbox = Toolbox(
            workspace=config.workspace,
            allow_bash=config.allow_bash,
            bash_timeout=config.bash_timeout,
            max_output=config.max_tool_output,
            # 到了深度上限就不再提供 task 工具——从工具列表里消失，
            # 比「留着但调用时报错」更省步数。
            allow_subagents=config.allow_subagents and depth < config.max_subagent_depth,
            spawn_subagent=self._spawn_subagent,
            mode=config.mode,
        )
        self._client = client
        self._owns_client = client is None
        # 子智能体的步骤要实时冒泡给「正在等它的那次工具调用」。
        # 没有人在等时（比如测试里直接跑子智能体）就是 None。
        self._sub_step_sink: Callable[[Step], None] | None = None
        self.messages: list[Message] = [Message(role="system", content=self.system_prompt())]

    # ---------- 配置派生 ----------

    def system_prompt(self) -> str:
        """按当前身份和配置拼系统提示词。"""
        if self.depth > 0 or not self.toolbox.allow_subagents:
            base = SUBAGENT_SYSTEM_PROMPT if self.depth > 0 else SYSTEM_PROMPT
        else:
            base = SYSTEM_PROMPT + SUBAGENT_SECTION
        extra = (self.config.system_extra or "").strip()
        return base + ("\n\n" + extra if extra else "")

    def child_config(self) -> AgentConfig:
        """子智能体用的配置。

        换模型是可选项：把「翻文件找代码」这类活交给便宜快的模型，
        把贵的留给主循环，是子智能体最实际的省钱用法。
        """
        config = replace(self.config, max_steps=self.config.max_subagent_steps)
        if not self.config.subagent_model:
            return config
        return replace(config, provider=replace(self.config.provider, model=self.config.subagent_model))

    # ---------- 生命周期 ----------

    async def __aenter__(self) -> "Agent":
        if self._client is None:
            self._client = Client(self.config.provider)
            await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.__aexit__(*exc)
            self._client = None

    # ---------- 子智能体 ----------

    async def _spawn_subagent(self, description: str, prompt: str) -> ToolResult:
        """派生一个子智能体跑完整个子任务，只把最终结论交回主智能体。

        上下文隔离是重点：子智能体的 messages 是新建的，它的工具调用过程
        不会进入主智能体的历史。共享的是 HTTP 连接（同一个 Client），
        不是对话——Connection 复用省握手，对话复用会让隔离失效。
        """
        label = (description or prompt).strip().splitlines()[0][:40]
        if self.depth >= self.config.max_subagent_depth:
            return ToolResult(False, "已经是子智能体，不能再派生下一层。请自己完成这个任务。")

        child = Agent(self.child_config(), client=self._client, depth=self.depth + 1)
        answer, failure = "", ""
        async for step in child.run(prompt):
            if step.kind == "answer":
                answer = step.text
            elif step.kind == "error":
                failure = step.text
            self._emit_sub_step(step, label)

        if not answer:
            reason = failure or "子智能体没有给出结论"
            return ToolResult(False, f"子智能体「{label}」未能完成任务：{reason}")
        return ToolResult(True, f"子智能体「{label}」的结论：\n{self.toolbox.clip(answer)}")

    def _emit_sub_step(self, step: Step, label: str) -> None:
        """把子智能体的一步冒泡出去。

        没人在等就丢弃——直接跑子智能体（测试、单用）时不应该报错。
        """
        sink = self._sub_step_sink
        if sink is None:
            return
        sink(Step(
            kind=step.kind,
            text=step.text,
            tool_name=step.tool_name,
            tool_args=step.tool_args,
            tool_ok=step.tool_ok,
            subagent=label,
        ))

    # ---------- 工具驱动 ----------

    async def _drive_tool(self, tc: ToolCall) -> AsyncIterator[Step]:
        """执行一次工具调用，产出「开始」「结束」两步。

        task 执行期间，子智能体的每一步也在这里实时冒泡出去。
        不这么做的话，一个跑几分钟的子任务在界面上完全静默——
        用户只看到光标在转，判断不出它是在干活还是卡住了。
        """
        yield Step("tool", tool_name=tc.name, tool_args=tc.arguments)

        queue: asyncio.Queue[Step] = asyncio.Queue()
        if tc.name == "task":
            self._sub_step_sink = queue.put_nowait
        try:
            # MCP 工具是阻塞式子进程调用，丢到线程里，别卡住事件循环
            if str(tc.name).startswith("mcp__"):
                call = asyncio.ensure_future(
                    asyncio.to_thread(manager.call, tc.name, tc.arguments))
            else:
                call = asyncio.ensure_future(self.toolbox.call(tc.name, tc.arguments))
            while True:
                waiter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({call, waiter}, return_when=asyncio.FIRST_COMPLETED)
                if waiter in done:
                    yield waiter.result()
                    continue
                # 工具已结束：队列里剩下的步骤排空后收工。
                # 这一步不会漏——子智能体的步骤是同步 put 的，call 结束前已全部入队。
                waiter.cancel()
                while not queue.empty():
                    yield queue.get_nowait()
                break
            result = await call
        finally:
            self._sub_step_sink = None

        yield Step(
            "tool",
            tool_name=tc.name,
            tool_args=tc.arguments,
            tool_ok=result.ok,
            text=result.render(),
            result=result,
        )

    # ---------- 主循环 ----------

    async def run(self, prompt: str, inbox=None, stop=None) -> AsyncIterator[Step]:
        """inbox 收到用户插话就中断当前生成、带修正重新作答；stop 置位就收工。"""
        """跑一轮完整任务。以异步生成器形式产出每一步，便于实时显示。"""
        if self._client is None:
            self._client = Client(self.config.provider)
            await self._client.__aenter__()

        self.messages.append(Message(role="user", content=prompt))
        # MCP 服务是按需启动的子进程，npx 冷启动要几秒到几十秒。
        # 直接在事件循环里调用会把整个任务堵住（对外表现就是「一直执行中」），
        # 所以丢到线程里去等。
        mcp_specs = await asyncio.to_thread(manager.specs)
        specs = self.toolbox.specs() + mcp_specs
        seen: list[tuple[str, str]] = []  # 重复调用检测

        for step_no in range(1, self.config.max_steps + 1):
            # 先试流式：纯文本回答能边生成边显示，这就是用户要的
            # 「不要一直等最后答案」。流式里也能收全分片的 tool_calls，
            # 所以两种回合都走得通；接口不支持流式时会抛 ProviderError，
            # 退回下面的一次性请求。
            reply: Reply | None = None
            streamed = ""
            stopped = interrupted = False
            try:
                async for event in self._client.stream_reply(self.messages, specs):
                    # 插话与停止：每个事件都看一眼。
                    # 停止 → 立刻收工；插话 → 中断这轮生成，把修正插进
                    # 历史后重新问一次，而不是等整轮跑完再排队。
                    if stop is not None and stop.is_set():
                        stopped = True
                        break
                    steer = drain_inbox(inbox)
                    if steer:
                        if streamed.strip():
                            self.messages.append(Message(role="assistant", content=streamed))
                        self.messages.append(Message(role="user", content=steer))
                        yield Step("steer", text=steer)
                        interrupted = True
                        break
                    if event.get("type") == "text":
                        piece = event["text"]
                        streamed += piece
                        yield Step("answer", text=piece, delta=True)
                    elif event.get("type") == "full":
                        # 网关不认 stream，直接把完整回复给过来了：用它，
                        # 不再多发一次请求。
                        reply = event["reply"]
                    elif event.get("type") == "tools":
                        calls = []
                        for item in event.get("tool_calls") or []:
                            raw = (item.get("arguments") or "").strip() or "{}"
                            try:
                                args = json.loads(raw)
                            except ValueError:
                                args = {"_raw": raw}
                            calls.append(ToolCall(
                                id=item.get("id") or "call",
                                name=item.get("name") or "",
                                arguments=args,
                            ))
                        reply = Reply(content=streamed, tool_calls=calls)
            except (ProviderError, AttributeError, NotImplementedError):
                # 静默退回一次性请求。AttributeError 是为了兼容测试里
                # 的假客户端和任何只实现了 complete() 的客户端——
                # 没有流式能力应当退回，而不是让整轮任务崩掉。
                # 已经吐出去的文字收不回来，
                # 所以只有在什么都没吐的时候才真的退回；
                # 否则就以已经收到的为准（否则用户会看到重复内容）。
                reply = Reply(content=streamed) if streamed else None

            if stopped:
                break
            if interrupted:
                # 用户已经给了修正，这一轮作废，带上新指令重新问
                continue
            if reply is None and streamed:
                # 纯文本回答：流式里已经收全了，直接当成这一轮的回复。
                # 少了这一步，下面会再发一次完整请求去拿同样的内容——
                # 用户明明已经看到答案了，却还要白等一次往返，
                # 顺带白占一次网关配额（按机器限并发时尤其明显）。
                reply = Reply(content=streamed)

            if reply is None:
                try:
                    reply = await self._client.complete(self.messages, specs)
                except ProviderError as exc:
                    yield Step("error", tool_ok=False, text=str(exc))
                    return

            if not reply.wants_tools:
                text = (reply.content or "").strip() or "（模型没有返回内容）"
                self.messages.append(Message(role="assistant", content=text))
                if streamed:
                    # 内容已经逐块发过了，这里只发一个结束标记，
                    # 界面据此收掉「正在输入」状态。
                    yield Step("answer", text="", delta=False)
                else:
                    # 退回了一次性请求，这里补发完整回答
                    yield Step("answer", text=text)
                return

            self.messages.append(Message(
                role="assistant",
                content=reply.content,
                tool_calls=reply.tool_calls,
            ))

            for tc in reply.tool_calls:
                # 重复调用检测：同工具同参数连续出现，说明模型在打转
                signature = (tc.name, json.dumps(tc.arguments, sort_keys=True, ensure_ascii=False))
                repeats = seen.count(signature)
                seen.append(signature)
                if repeats >= 4:
                    # 同一条命令连打四次以上才算打转，才真的中止。
                    note = (
                        f"检测到重复调用 {tc.name} 且参数完全相同（第 {repeats + 1} 次），已中止。"
                        f"换个思路，或者先说明为什么这条命令必须反复重试。"
                    )
                    self.messages.append(Message(
                        role="tool", content=note, tool_call_id=tc.id, name=tc.name,
                    ))
                    yield Step("error", tool_ok=False, text=note)
                    return
                if repeats >= 1:
                    # 第二次出现同样的调用：只提醒，不掐掉整个任务。
                    # 控制设备时这太常见了——第一次 ssh 超时、命令没回显、
                    # 服务刚起来还没就绪，都会让模型用同样的参数再试一次。
                    # 直接中止会表现为「任务突然停止不工作」。
                    self.messages.append(Message(
                        role="tool",
                        content=(
                            "注意：你刚才已经用过完全相同的工具和参数（内容相同），"
                            "再执行一次结果不会变。请先看上一次的输出："
                            "如果是超时或没有回显，先检查连接和认证；"
                            "如果是命令报错，改参数或换命令。不要原样重试。"
                        ),
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))
                    continue

                # 工具结果由「结束」那一步带回来（见 Step.result 的说明）
                result: ToolResult | None = None
                async for step in self._drive_tool(tc):
                    if step.result is not None:
                        result = step.result
                    yield step
                assert result is not None, "工具步骤流必须以带结果的一步结束"

                self.messages.append(Message(
                    role="tool",
                    content=result.render(),
                    tool_call_id=tc.id,
                    name=tc.name,
                ))

        yield Step("error", tool_ok=False, text=f"已达最大步数 {self.config.max_steps}，任务未完成。可用 --max-steps 放宽上限。")
