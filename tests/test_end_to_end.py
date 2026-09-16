"""端到端测试：智能体通过真实 HTTP 与一个假的 OpenAI 兼容服务对话。

这补齐了单元测试的盲区——那些测试里整个 HTTP 层被假客户端替换掉了，
协议构造、响应解析、鉴权头这些真实路径并没有被验证。
"""

import asyncio
from pathlib import Path

import pytest

from jancode_agent.agent import Agent
from jancode_agent.config import AgentConfig, ProviderConfig
from tests.mock_server import call, chat_reply, start


def build(tmp_path: Path, url: str) -> Agent:
    cfg = AgentConfig(
        provider=ProviderConfig(name="mock", base_url=url, model="m", api_key="sk-test"),
        workspace=tmp_path,
        max_steps=5,
    )
    return Agent(cfg)


def run(agent: Agent, prompt: str):
    async def _go():
        return [s async for s in agent.run(prompt)]
    return asyncio.run(_go())


def test_完整问答走真实_HTTP(tmp_path):
    """模型答一句话，验证请求发出、响应解析、答复产出这条完整链路。"""
    url, httpd = start([chat_reply("你好，我是模型")])
    try:
        agent = build(tmp_path, url)
        async def _go():
            async with agent:
                return [s async for s in agent.run("打个招呼")]
        steps = asyncio.run(_go())
        assert steps[-1].kind == "answer"
        assert steps[-1].text == "你好，我是模型"
    finally:
        httpd.shutdown()


def test_工具调用闭环走真实_HTTP(tmp_path):
    """模型先要求读文件，拿到结果后再给出答复——完整的两轮来回。"""
    (tmp_path / "note.txt").write_text("秘密内容", encoding="utf-8")
    url, httpd = start([
        chat_reply(tool_calls=[call("c1", "read_file", {"path": "note.txt"})]),
        chat_reply("文件里写的是「秘密内容」"),
    ])
    try:
        agent = build(tmp_path, url)
        async def _go():
            async with agent:
                return [s async for s in agent.run("读一下 note.txt")]
        steps = asyncio.run(_go())

        assert steps[0].kind == "tool" and steps[0].tool_name == "read_file"
        assert "秘密内容" in steps[1].text          # 工具真的读到了
        assert steps[-1].kind == "answer"
        assert "秘密内容" in steps[-1].text
    finally:
        httpd.shutdown()


def test_无密钥时收到_401_并如实报告(tmp_path):
    """没有密钥时必须明确失败，而不是悄悄返回空答案。"""
    import tests.mock_server as ms
    url, httpd = start([chat_reply("不该走到这里")])
    try:
        cfg = AgentConfig(
            provider=ProviderConfig(name="mock", base_url=url, model="m", api_key=""),
            workspace=tmp_path, max_steps=3,
        )
        agent = Agent(cfg)
        async def _go():
            async with agent:
                return [s async for s in agent.run("你好")]
        steps = asyncio.run(_go())
        assert steps[-1].kind == "error"
        assert "401" in steps[-1].text
        assert "Invalid token" in steps[-1].text   # 中转站原始报错要透出
    finally:
        httpd.shutdown()


def test_请求确实带上了鉴权头(tmp_path):
    """这条守着一个容易静默失效的点：密钥没被带上时，
    如果服务端恰好不校验，整个会话会「看起来正常」但实际是匿名的。"""
    import tests.mock_server as ms
    url, httpd = start([chat_reply("ok")])
    try:
        agent = build(tmp_path, url)
        async def _go():
            async with agent:
                return [s async for s in agent.run("hi")]
        asyncio.run(_go())
        assert ms._Handler.seen, "应该至少发出过一次请求"
        assert ms._Handler.seen[0]["auth"] == "Bearer sk-test"
        assert ms._Handler.seen[0]["path"].endswith("/chat/completions")
    finally:
        httpd.shutdown()
