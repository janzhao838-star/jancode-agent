"""子智能体测试。用假客户端驱动，不联网。

重点验证三件事：
1. **上下文隔离** —— 子智能体的中间过程不能进入主智能体的历史。
   这是派生它的唯一理由，隔离失效的话它就只是个更贵的工具调用。
2. **结论回灌** —— 主智能体必须拿到子智能体的最终答复，否则等于白跑一趟。
3. **兜底真的拦得住** —— 深度上限、开关、子智能体失败，都不能把主任务带崩。
"""

import asyncio
from pathlib import Path

from jancode_agent.agent import Agent
from jancode_agent.config import AgentConfig, ProviderConfig, load_config
from jancode_agent.providers import Reply, ToolCall
from tests.mock_server import call, chat_reply, start


class FakeClient:
    """按预设脚本依次返回回复。主智能体与子智能体共用同一个脚本队列。

    父子的调用顺序是确定的：主要 task → 子跑完 → 主收尾，
    所以一条顺序队列就够，不需要按内容路由。
    """

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


def make_agent(tmp_path: Path, replies, **overrides) -> Agent:
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="m", api_key="k"),
        workspace=tmp_path,
        max_steps=6,
        **overrides,
    )
    return Agent(cfg, client=FakeClient(replies))


def run(agent: Agent, prompt: str):
    async def _go():
        return [s async for s in agent.run(prompt)]
    return asyncio.run(_go())


def task_reply(cid: str = "t1", description: str = "查文件") -> Reply:
    return Reply(tool_calls=[ToolCall(
        id=cid, name="task",
        arguments={"description": description, "prompt": "读 note.txt，告诉我里面写了什么"},
    )])


def test_子智能体的中间过程不进入主智能体上下文(tmp_path):
    (tmp_path / "note.txt").write_text("秘密内容", encoding="utf-8")
    agent = make_agent(tmp_path, [
        task_reply(),
        # 下面是子智能体的两轮：先读文件，再给结论
        Reply(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "note.txt"})]),
        Reply(content="note.txt 里写的是「秘密内容」"),
        # 主智能体收尾
        Reply(content="查到了：秘密内容"),
    ])
    steps = run(agent, "帮我查个东西")

    tool_msgs = [m for m in agent.messages if m.role == "tool"]
    assert len(tool_msgs) == 1, f"主历史里应当只有 task 这一次工具往返，实际 {len(tool_msgs)} 次"
    assert tool_msgs[0].name == "task"
    assert "秘密内容" in tool_msgs[0].content, "子智能体的结论必须回灌给主智能体"
    # 子智能体读文件那次调用（c1）绝不能出现在主历史里——这就是隔离
    assert not [m for m in agent.messages if m.tool_call_id == "c1"]
    assert steps[-1].kind == "answer" and steps[-1].subagent == ""


def test_子智能体的每一步都带标签冒泡(tmp_path):
    (tmp_path / "note.txt").write_text("内容", encoding="utf-8")
    agent = make_agent(tmp_path, [
        task_reply(description="查文件"),
        Reply(tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "note.txt"})]),
        Reply(content="读到了"),
        Reply(content="好的"),
    ])
    steps = run(agent, "做事")

    sub_steps = [s for s in steps if s.subagent]
    assert sub_steps, "子智能体的步骤必须出现在主流程的步骤流里，否则界面上是黑盒"
    assert all(s.subagent == "查文件" for s in sub_steps), "标签应当用 description，便于界面归属"
    assert any(s.tool_name == "read_file" for s in sub_steps), "子智能体的工具调用也要冒泡"
    assert any(s.kind == "answer" for s in sub_steps)
    # 主智能体自己的步骤不带标签，否则界面无法区分
    top = [s for s in steps if not s.subagent and s.kind != "usage"]
    assert top[0].tool_name == "task" and top[-1].kind == "answer"


def test_子智能体不能再派生子智能体(tmp_path):
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="m", api_key="k"),
        workspace=tmp_path, max_subagent_depth=1,
    )
    child = Agent(cfg, client=FakeClient([]), depth=1)

    names = {t["function"]["name"] for t in child.toolbox.specs()}
    assert "task" not in names, "到了深度上限就不该再提供 task 工具，否则模型会白试一轮"
    # 就算模型硬调，也必须被拦住，而不是悄悄又派一层
    result = asyncio.run(child.toolbox.call("task", {"description": "x", "prompt": "y"}))
    assert result.ok is False
    # 直接调内部派生入口也要拦住（工具列表之外的第二道闸）
    guarded = asyncio.run(child._spawn_subagent("x", "y"))
    assert guarded.ok is False and "不能再派生" in guarded.output


def test_关掉开关后既不提供工具也不提它(tmp_path):
    agent = make_agent(tmp_path, [], allow_subagents=False)
    assert "task" not in {t["function"]["name"] for t in agent.toolbox.specs()}
    # 提示词里提了却没有这个工具，模型会反复调用一个不存在的东西
    assert "task 工具" not in agent.system_prompt()
    assert "task 工具" in make_agent(tmp_path, []).system_prompt()


def test_子智能体跑不完时主智能体仍能收尾(tmp_path):
    # 子智能体每轮换一个参数调 list_dir，绕过重复检测，直到撞上步数上限。
    # FakeClient 是严格顺序队列，所以这里必须给够、但不能多：
    # 多出来的「子智能体回复」会被主智能体当成自己的回合吃掉。
    child_replies = [
        Reply(tool_calls=[ToolCall(id=f"c{i}", name="list_dir", arguments={"path": "." * (i + 1)})])
        for i in range(3)   # 恰好等于 max_subagent_steps
    ]
    agent = make_agent(
        tmp_path,
        [task_reply(), *child_replies, Reply(content="子任务没做成，我直接回答")],
        max_subagent_steps=3,
    )
    steps = run(agent, "做事")

    finished = [s for s in steps if s.kind == "tool" and s.tool_name == "task" and s.text]
    assert finished and finished[-1].tool_ok is False, "子智能体没跑完，task 必须如实返回失败"
    assert "未能完成任务" in finished[-1].text
    assert steps[-1].kind == "answer", "子智能体失败不能拖垮主任务"


def test_子智能体可以换成另一个模型(tmp_path):
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x", model="主模型", api_key="k"),
        workspace=tmp_path, subagent_model="子模型", max_subagent_steps=7,
    )
    child_cfg = Agent(cfg, client=FakeClient([])).child_config()
    assert child_cfg.provider.model == "子模型"
    assert child_cfg.provider.base_url == cfg.provider.base_url, "只是换模型，接入点不变"
    assert child_cfg.max_steps == 7

    # 没配就沿用主模型
    plain = AgentConfig(provider=cfg.provider, workspace=tmp_path)
    assert Agent(plain, client=FakeClient([])).child_config().provider.model == "主模型"


def test_配置文件能配子智能体(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[agent]\n"
        "allow_subagents = false\n"
        "max_subagent_steps = 5\n"
        "max_subagent_depth = 2\n"
        'subagent_model = "cheap-model"\n',
        encoding="utf-8",
    )
    cfg = load_config(path=path, provider_name="deepseek", workspace=tmp_path)
    assert cfg.allow_subagents is False
    assert cfg.max_subagent_steps == 5
    assert cfg.max_subagent_depth == 2
    assert cfg.subagent_model == "cheap-model"

    # 不写时给默认值：默认开启、深度 1（子智能体不能再派生）
    default = load_config(path=tmp_path / "缺失.toml", provider_name="deepseek", workspace=tmp_path)
    assert default.allow_subagents is True
    assert default.max_subagent_depth == 1
    assert default.max_subagent_steps == 20


def test_子智能体走真实_HTTP_端到端(tmp_path):
    """单元测试里 HTTP 层整个被替换掉了。这一条让父子两级都真的发请求，
    验证共享连接、鉴权、协议构造在嵌套场景下同样成立。"""
    import tests.mock_server as ms

    (tmp_path / "note.txt").write_text("秘密内容", encoding="utf-8")
    url, httpd = start([
        chat_reply(tool_calls=[call("t1", "task", {
            "description": "查文件", "prompt": "读 note.txt，告诉我里面写了什么"})]),
        chat_reply(tool_calls=[call("c1", "read_file", {"path": "note.txt"})]),
        chat_reply("note.txt 内容是「秘密内容」"),
        chat_reply("我派子智能体查到了：秘密内容"),
    ])
    try:
        cfg = AgentConfig(
            provider=ProviderConfig(name="mock", base_url=url, model="m", api_key="sk-test"),
            workspace=tmp_path, max_steps=5,
        )
        agent = Agent(cfg)

        async def _go():
            async with agent:
                return [s async for s in agent.run("帮我查 note.txt")]
        steps = asyncio.run(_go())

        assert steps[-1].kind == "answer"
        assert steps[-1].text == "我派子智能体查到了：秘密内容"
        # 四次请求：主(派活) → 子(读文件) → 子(给结论) → 主(收尾)
        assert len(ms._Handler.seen) == 4, f"应当有 4 次真实请求，实际 {len(ms._Handler.seen)}"
        assert all(s["auth"] == "Bearer sk-test" for s in ms._Handler.seen)
        # 主智能体的第二次请求里带的是结论，而不是子智能体读到的原文
        last_user_turns = [
            m.get("content") or ""
            for m in ms._Handler.seen[-1]["body"]["messages"]
            if m.get("role") == "tool"
        ]
        assert any("子智能体「查文件」的结论" in c for c in last_user_turns)
    finally:
        httpd.shutdown()
