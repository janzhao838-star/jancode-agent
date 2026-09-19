# -*- coding: utf-8 -*-
"""设置保存接口：用户在界面里填的接入配置，保存后立刻生效。

这是用户数据的写入路径，而且文件里有 API 密钥——保存要落盘、
权限要 600、马上能用，任何一环断了都表现为「填了没反应」。
"""

import json
import urllib.request

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.server import SETTINGS_PATH, build_server


@pytest.fixture()
def server(tmp_path, monkeypatch):
    """临时目录里建服务（settings 文件指到 tmp）。"""
    monkeypatch.setattr("jancode_agent.server.SETTINGS_PATH", tmp_path / "desktop-settings.json")
    # SETTINGS_PATH 是模块级常量，_save_settings 里直接引用；
    # monkeypatch 替换后 Handler 里读到的也是新值（同一模块属性）。
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://old:1234/v1", model="m1", api_key="k-old"),
        workspace=tmp_path,
    )
    httpd = build_server(port=0, config=cfg, require_key=False)
    port = httpd.server_address[1]
    import threading
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def post(url, body):
    req = urllib.request.Request(
        url + "/api/settings",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def test_保存设置落盘且权限600(server, monkeypatch):
    import stat
    from jancode_agent import server as sm
    path = sm.SETTINGS_PATH
    out = post(server, {"api_key": "sk-new", "base_url": "http://new:9999/v1", "model": "m2"})
    assert out["ok"] is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["api_key"] == "sk-new"
    mode = stat.S_IMODE(path.stat().st_mode)
    import os
    if os.name == "posix":
        # Windows 不支持 POSIX 权限位，chmod 600 后 stat 仍报 0o666。
        assert mode == 0o600, f"设置文件里有密钥，权限必须 600，实际 {oct(mode)}"


def test_保存后当前进程立刻生效(server, monkeypatch):
    from jancode_agent import server as sm
    post(server, {"api_key": "sk-new", "base_url": "http://new:9999/v1", "model": "m2"})
    assert sm.Handler.config.provider.api_key == "sk-new", "保存后应当立刻生效，不用重启"
    assert sm.Handler.config.provider.base_url == "http://new:9999/v1"


def test_空值不覆盖已有设置(server):
    from jancode_agent import server as sm
    post(server, {"api_key": "sk-a", "base_url": "http://a/v1", "model": "ma"})
    post(server, {"api_key": "sk-b"})  # 只改 key
    data = json.loads(sm.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert data["api_key"] == "sk-b"
    assert data["base_url"] == "http://a/v1", "没填的字段不该被清掉"


def test_全空提交被拒(server):
    out = post(server, {})
    assert out["ok"] is False and "什么都没填" in out["error"]
