# -*- coding: utf-8 -*-
"""工作模式的代码级拦截测试。

这四个模式原先只写进系统提示词，模型不听话就绕过去了。
这里验证的是「提示词之外还有一道代码闸门」：
readonly/plan 模式下写文件、改文件、执行命令一律被拒，
而且拒绝发生在方法内部——不管从 call() 还是直接调用进来都生效。
"""

import asyncio

from jancode_agent.tools import Toolbox


def run(coro):
    return asyncio.run(coro)


def test_只读模式拒绝写文件(tmp_path):
    tb = Toolbox(tmp_path, mode="readonly")
    r = run(tb.write_file("a.txt", "x"))
    assert not r.ok
    assert "只读" in r.output
    assert not (tmp_path / "a.txt").exists()  # 真的没落盘


def test_只读模式放行读文件(tmp_path):
    (tmp_path / "a.txt").write_text("内容", encoding="utf-8")
    tb = Toolbox(tmp_path, mode="readonly")
    assert run(tb.read_file("a.txt")).ok


def test_只读模式拒绝bash(tmp_path):
    tb = Toolbox(tmp_path, mode="readonly")
    r = run(tb.bash("echo hi"))
    assert not r.ok and "只读" in r.output


def test_计划模式同样硬拦(tmp_path):
    (tmp_path / "a.txt").write_text("原内容", encoding="utf-8")
    tb = Toolbox(tmp_path, mode="plan")
    assert not run(tb.edit_file("a.txt", "原", "新")).ok
    assert not run(tb.save_skill("x", "y")).ok
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "原内容"


def test_call入口也拦(tmp_path):
    # 光拦直接调用不够：agent 走的是 call()，两个入口都必须生效
    tb = Toolbox(tmp_path, mode="readonly")
    assert not run(tb.call("write_file", {"path": "a.txt", "content": "x"})).ok


def test_全自动模式放行(tmp_path):
    tb = Toolbox(tmp_path, mode="auto")
    assert run(tb.write_file("a.txt", "x")).ok


def test_sandbox拒绝往工作目录外写(tmp_path):
    tb = Toolbox(tmp_path, mode="sandbox")
    r = run(tb.bash("echo x > /tmp/jancode-perm-outside.txt"))
    assert not r.ok and "限定工作目录" in r.output


def test_sandbox放行工作目录内的写(tmp_path):
    tb = Toolbox(tmp_path, mode="sandbox")
    assert run(tb.bash("echo hi > inside.txt")).ok


def test_sandbox放行只读命令(tmp_path):
    # 只看不写的外部路径不该拦，否则连 /etc/hosts 都不能看了
    tb = Toolbox(tmp_path, mode="sandbox")
    assert run(tb.bash("cat /etc/hosts")).ok


def test_模式名不认识时按最严格处理(tmp_path):
    # 拼错模式名绝不能变成放行：用户以为自己在受限模式里，
    # 实际全放开是最糟的结果。所以不认识的模式按只读处理。
    tb = Toolbox(tmp_path, mode="readonyl")
    r = run(tb.write_file("a.txt", "x"))
    assert not r.ok
    assert "无法识别" in r.output
    assert not (tmp_path / "a.txt").exists()
    # 只读类工具仍然放行，否则连看都看不了
    (tmp_path / "b.txt").write_text("内容", encoding="utf-8")
    assert run(tb.read_file("b.txt")).ok
