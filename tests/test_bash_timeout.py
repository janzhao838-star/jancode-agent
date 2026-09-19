# -*- coding: utf-8 -*-
"""bash 工具的超时行为：整组杀进程 + 保留部分输出。"""

import asyncio
import time

import pytest

from jancode_agent.tools import Toolbox


def _box(tmp_path, timeout=1.0):
    return Toolbox(tmp_path, bash_timeout=timeout)


def test_超时后孙子进程也被杀(tmp_path):
    """shell 里再起后台进程：只杀 shell 的话孙子会留在系统里。"""
    import os
    import subprocess
    import sys

    box = _box(tmp_path, timeout=1.0)
    # 不用 shell 语法（; & sleep）：Windows 的 cmd.exe 不认，语义全变。
    # 用 python 写挂住的父/孙进程，跨平台行为一致；孙进程在 cwd（即
    # tmp_path）里写下自己的 pid，方便平台无关地验证它确实被收掉。
    (tmp_path / "grandchild.py").write_text(
        "import os, time\n"
        "open('grandchild.pid', 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n", encoding="utf-8")
    (tmp_path / "hang.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'grandchild.py'])\n"
        "time.sleep(30)\n", encoding="utf-8")
    r = asyncio.run(box.bash(f'"{sys.executable}" "{tmp_path / "hang.py"}"'))
    assert r.ok is False
    assert "超时" in r.output
    # 给系统一点时间收进程
    time.sleep(0.5)
    pidfile = tmp_path / "grandchild.pid"
    assert pidfile.exists(), "孙进程应当来得及写下自己的 pid"
    pid = pidfile.read_text().strip()
    if os.name == "posix":
        out = subprocess.run(["pgrep", "-f", "grandchild.py"],
                             capture_output=True, text=True)
    else:
        out = subprocess.run(["tasklist"], capture_output=True, text=True)
    assert pid not in out.stdout, f"孙进程 {pid} 不应还活着: {out.stdout}"


def test_超时保留部分输出(tmp_path):
    """先输出再挂住：超时报错里应带上已产出的内容。"""
    import sys

    box = _box(tmp_path, timeout=2.0)
    # python 一行式跨平台：cmd.exe 不认 `;` 串联。
    code = "print('关键进展一'); print('关键进展二'); import time; time.sleep(30)"
    r = asyncio.run(box.bash(f'"{sys.executable}" -c "{code}"'))
    assert r.ok is False
    assert "部分输出" in r.output
    assert "关键进展二" in r.output


def test_正常命令不受影响(tmp_path):
    box = _box(tmp_path)
    r = asyncio.run(box.bash("echo fine"))
    assert r.ok is True
    assert r.output.strip() == "fine"
