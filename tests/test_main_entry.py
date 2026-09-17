# -*- coding: utf-8 -*-
"""python -m jancode_agent 入口可用。"""

import os
import subprocess
import sys
import pathlib


def test_模块入口能跑():
    """python -m jancode_agent --version 必须正常退出。"""
    root = pathlib.Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    r = subprocess.run(
        [sys.executable, "-m", "jancode_agent", "--version"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert r.returncode == 0, r.stderr[:200]
    assert "jancode-agent" in r.stdout
