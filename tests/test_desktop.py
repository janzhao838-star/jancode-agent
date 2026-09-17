"""桌面版的测试。

窗口本身没法在 CI 里点，但桌面版真正会出错的地方——端口分配、等就绪、
缺密钥时的表现——都是纯逻辑，测得住。这几处也正是「用户双击了没反应」
这类问题的来源。
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jancode_agent.desktop import free_port, wait_until_ready
from jancode_agent.server import MissingApiKey, build_server


def test_分配到的端口真的能绑上():
    port = free_port()
    assert 0 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_连续分配不会给出同一个端口():
    # 多开两个实例是常见用法（一个跑项目 A 一个跑项目 B）。
    # 端口要是重复，第二个实例会直接起不来。
    assert len({free_port() for _ in range(5)}) == 5


def test_没人监听的端口不会被误判成就绪():
    # 拿到一个刚关掉的端口：这里必须返回 False。要是不等就开窗口，
    # 用户看到的是「无法连接」，还得自己刷新。
    port = free_port()
    assert wait_until_ready(port, timeout=0.4) is False


def test_服务起来之后能等到就绪():
    class _Ok(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert wait_until_ready(port, timeout=5.0) is True
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_缺密钥时抛的是专用异常而不是开一个白屏窗口(monkeypatch):
    # 桌面版靠这个异常决定要不要弹提示框。抛成别的类型的话，
    # 用户看到的就是一个连不上后端的空窗口。
    monkeypatch.setenv("JANCODE_API_KEY", "")
    monkeypatch.setattr("jancode_agent.config.Path.home", lambda: __import__("pathlib").Path("/nonexistent"))
    with pytest.raises(MissingApiKey):
        build_server(port=0, config=_no_key_config())


def _no_key_config():
    from dataclasses import replace

    from jancode_agent.config import load_config

    cfg = load_config(workspace=__import__("pathlib").Path("."))
    return replace(cfg, provider=replace(cfg.provider, api_key=""))
