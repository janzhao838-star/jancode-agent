"""命令行层面的端到端测试：真进程、真 HTTP、真协议。

与 test_subagent.py 的分工：
  · test_subagent.py 用假客户端驱动 Agent，验证循环与上下文隔离的逻辑；
  · 这里把整个 CLI 当黑盒跑一遍——命令行解析、配置装配、真实 HTTP 请求、
    输出渲染全都在里面。子智能体的命令行输出（缩进、结论提示）只有这里覆盖得到，
    而「--no-subagents 到底有没有把工具摘掉」也只有看真实请求体才算数。
"""

import subprocess
import sys

from tests import mock_server
from tests.mock_server import call, chat_reply, start


def run_cli(workspace, script, *extra):
    """起一个假服务，用真实命令行入口跑一次任务。"""
    base, httpd = start(script)
    try:
        return subprocess.run(
            [sys.executable, "-m", "jancode_agent.cli",
             "--base-url", base, "--api-key", "test-key",
             "--workspace", str(workspace), *extra, "帮我查个东西"],
            capture_output=True, text=True, timeout=120,
        )
    finally:
        httpd.shutdown()


def test_子智能体在真实命令行里跑通(tmp_path):
    (tmp_path / "note.txt").write_text("秘密内容", encoding="utf-8")
    proc = run_cli(tmp_path, [
        # 主智能体把活派出去
        chat_reply(tool_calls=[call("t1", "task", {
            "description": "查文件", "prompt": "读 note.txt，告诉我里面写了什么"})]),
        # 子智能体自己的一轮：读文件
        chat_reply(tool_calls=[call("c1", "read_file", {"path": "note.txt"})]),
        # 子智能体给结论
        chat_reply(content="note.txt 里写的是「秘密内容」"),
        # 主智能体收尾
        chat_reply(content="查到了：秘密内容"),
    ])

    assert proc.returncode == 0, f"退出码非 0；stderr:\n{proc.stderr}"
    # 子智能体的过程要看得见，不能是个黑盒
    assert "子智能体「查文件」已给出结论" in proc.stdout, proc.stdout
    # 结论要回灌给主智能体，否则等于白跑
    assert "秘密内容" in proc.stdout
    assert "查到了：秘密内容" in proc.stdout


def test_子智能体的中间过程没有进入主智能体的上下文(tmp_path):
    (tmp_path / "note.txt").write_text("秘密内容", encoding="utf-8")
    run_cli(tmp_path, [
        chat_reply(tool_calls=[call("t1", "task", {
            "description": "查文件", "prompt": "读 note.txt"})]),
        chat_reply(tool_calls=[call("c1", "read_file", {"path": "note.txt"})]),
        chat_reply(content="note.txt 里写的是「秘密内容」"),
        chat_reply(content="查到了：秘密内容"),
    ])

    # 从**服务端**看最后一次请求：主智能体的历史里应当只有 task 这一次工具往返。
    # 这比在进程内断言更有说服力——它才是模型真正看到的东西。
    seen = mock_server._Handler.seen
    assert len(seen) == 4, f"应当正好 4 次请求，实际 {len(seen)}"
    last = seen[-1]["body"]
    tool_messages = [m for m in last["messages"] if m.get("role") == "tool"]
    assert [m.get("name") for m in tool_messages] == ["task"], tool_messages
    # 子智能体读文件这件事，不该出现在主智能体看到的历史里。
    # 注意不能直接在整段 JSON 里搜 "read_file"——系统提示词里本来就会提到这个工具名，
    # 那样搜到的是提示词，不是泄漏。要查的是真实的工具调用记录。
    called = [
        tc.get("function", {}).get("name")
        for m in last["messages"]
        for tc in (m.get("tool_calls") or [])
    ]
    assert called == ["task"], f"主上下文里应当只有一次 task 调用，实际：{called}"


def test_关闭子智能体后模型根本拿不到这个工具(tmp_path):
    proc = run_cli(tmp_path, [
        chat_reply(content="直接回答，没有派活"),
    ], "--no-subagents")

    assert proc.returncode == 0, proc.stderr
    assert "直接回答，没有派活" in proc.stdout
    # 关键断言：不是「留着工具但调用时报错」，而是压根不提供给模型。
    # 提示词里说了有、工具列表里却没有，模型会反复空试把步数耗光。
    tools = mock_server._Handler.seen[0]["body"].get("tools") or []
    names = [t.get("function", {}).get("name") for t in tools]
    assert "task" not in names, names
    assert "read_file" in names, "其它工具不该被一起摘掉"
