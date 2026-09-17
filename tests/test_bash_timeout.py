# -*- coding: utf-8 -*-
"""bash 工具的超时行为：整组杀进程 + 保留部分输出。"""

import asyncio
import time

import pytest

from jancode_agent.tools import Toolbox


def _box(tmp_path, timeout=1.0):
    return Toolbox(tmp_path, bash_timeout=timeout)


def test_超时后孙子进程也被杀(tmp_path):
    """sh -c 里再起后台进程：只杀 shell 的话孙子会留在系统里。"""
    box = _box(tmp_path, timeout=1.0)
    r = asyncio.run(box.bash("sleep 30 & echo started; sleep 30"))
    assert r.ok is False
    assert "超时" in r.output
    # 给系统一点时间收进程
    time.sleep(0.3)
    import subprocess
    out = subprocess.run(["pgrep", "-f", "sleep 30"], capture_output=True, text=True)
    assert out.stdout.strip() == "", "sleep 30 不应还活着: " + out.stdout


def test_超时保留部分输出(tmp_path):
    """先输出再挂住：超时报错里应带上已产出的内容。"""
    box = _box(tmp_path, timeout=2.0)
    r = asyncio.run(box.bash("echo 关键进展一; echo 关键进展二; sleep 30"))
    assert r.ok is False
    assert "部分输出" in r.output
    assert "关键进展二" in r.output


def test_正常命令不受影响(tmp_path):
    box = _box(tmp_path)
    r = asyncio.run(box.bash("echo fine"))
    assert r.ok is True
    assert r.output.strip() == "fine"
