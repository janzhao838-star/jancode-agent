"""工具系统测试。重点是安全边界和失败信号。"""

import asyncio
from pathlib import Path

import pytest

from jancode_agent.tools import Toolbox


@pytest.fixture
def box(tmp_path: Path) -> Toolbox:
    return Toolbox(workspace=tmp_path)


def run(coro):
    return asyncio.run(coro)


def test_写入后能读回(box, tmp_path):
    r = run(box.write_file("a.txt", "你好\n世界\n"))
    assert r.ok
    r = run(box.read_file("a.txt"))
    assert r.ok and "你好" in r.output and "世界" in r.output
    # 带行号返回，模型才能定位
    assert "1\t" in r.output


def test_读不存在的文件给出可操作提示(box):
    r = run(box.read_file("nope.txt"))
    assert not r.ok
    # 失败信息要能指导模型下一步，而不是只说「失败」
    assert "list_dir" in r.output


def test_越界读取被拒绝(box):
    r = run(box.read_file("../../etc/passwd"))
    assert not r.ok
    assert "超出工作目录" in r.output


def test_越界写入被拒绝(box, tmp_path):
    outside = tmp_path.parent / "evil.txt"
    r = run(box.write_file("../evil.txt", "x"))
    assert not r.ok
    assert not outside.exists()


def test_edit_要求唯一匹配(box):
    run(box.write_file("b.txt", "foo\nfoo\n"))
    r = run(box.edit_file("b.txt", "foo", "bar"))
    assert not r.ok, "内容出现多次时不应擅自替换"
    assert "2 次" in r.output
    # 原文必须没被改动
    assert run(box.read_file("b.txt")).output.count("foo") == 2


def test_edit_允许显式全量替换(box):
    run(box.write_file("c.txt", "foo\nfoo\n"))
    r = run(box.edit_file("c.txt", "foo", "bar", replace_all=True))
    assert r.ok
    assert run(box.read_file("c.txt")).output.count("bar") == 2


def test_edit_找不到内容时提示先读文件(box):
    run(box.write_file("d.txt", "hello"))
    r = run(box.edit_file("d.txt", "zzz", "yyy"))
    assert not r.ok and "read_file" in r.output


def test_危险命令被拒绝(box):
    r = run(box.bash("rm -rf /"))
    assert not r.ok
    assert "危险" in r.output


def test_禁用_bash_时不提供该工具(tmp_path):
    box = Toolbox(workspace=tmp_path, allow_bash=False)
    names = {t["function"]["name"] for t in box.specs()}
    assert "bash" not in names
    assert not run(box.bash("echo hi")).ok


def test_grep_能找到并跳过依赖目录(box):
    run(box.write_file("src/x.py", "target_line = 1\n"))
    run(box.write_file("node_modules/y.js", "target_line = 2\n"))
    r = run(box.grep("target_line"))
    assert r.ok
    assert "src/x.py" in r.output
    assert "node_modules" not in r.output


def test_未知工具返回可用列表(box):
    r = run(box.call("nonexistent", {}))
    assert not r.ok and "read_file" in r.output


def test_参数错误不抛异常(box):
    # 模型给错参数是常态，必须转成可读提示
    r = run(box.call("read_file", {"wrong_param": 1}))
    assert not r.ok
