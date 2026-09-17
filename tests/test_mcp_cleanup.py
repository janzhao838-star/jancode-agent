# -*- coding: utf-8 -*-
"""MCP 子进程的退出回收：退出时不能把 node/npx 进程漏在系统里。

用一个真正会说 MCP 协议的假服务验证：close/atexit 收掉进程、
注册表清空。"""

import json
import sys
import time

from jancode_agent.mcp import MCPClient, _atexit_close, manager


_FAKE = chr(10).join([
    'import json, sys',
    'for line in sys.stdin:',
    '    try:',
    '        m = json.loads(line)',
    '    except ValueError:',
    '        continue',
    '    if "id" not in m:',
    '    ' + '    continue  # 通知不回',
    '    ' + 'if m.get("method") == "initialize":',
    '        r = {"jsonrpc": "2.0", "id": m["id"], "result": {"serverInfo": {"name": "fake"}}}',
    '    else:',
    '        r = {"jsonrpc": "2.0", "id": m["id"], "result": {"tools": []}}',
    '    sys.stdout.write(json.dumps(r) + chr(10))',
    '    sys.stdout.flush()',
])


def _client(name, timeout=5.0):
    return MCPClient(name, sys.executable, ["-c", _FAKE], timeout=timeout)


def test_能起能收():
    c = _client("t")
    c.start()
    assert c.alive
    c.close()
    time.sleep(0.1)
    assert not c.alive, "terminate 后进程应当退出"


def test_atexit钩子空跑安全():
    """直接调用 _atexit_close 不抛异常（空注册表也能收尾）。"""
    _atexit_close()


def test_close_all清空注册表并收进程():
    c = _client("t2")
    c.start()
    manager._clients["t2"] = c
    try:
        manager.close_all()
        assert manager._clients == {}
        time.sleep(0.1)
        assert not c.alive
    finally:
        c.close()
