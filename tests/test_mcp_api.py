# -*- coding: utf-8 -*-
"""MCP 服务管理接口：增删改、重载、探活。"""

import json
import urllib.request

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.server import build_server


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import jancode_agent.server as sm
    import jancode_agent.mcp as mm
    mcp_file = tmp_path / "mcp.json"
    monkeypatch.setattr(mm, "CONFIG_PATH", mcp_file)
    monkeypatch.setattr("jancode_agent.server.SETTINGS_PATH", tmp_path / "s.json")
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x/v1", model="m", api_key="k"),
        workspace=tmp_path,
    )
    httpd = build_server(port=0, config=cfg, require_key=False)
    port = httpd.server_address[1]
    import threading
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield {"url": "http://127.0.0.1:" + str(port), "mm": mm, "path": mcp_file}
    httpd.shutdown()


def post(env, body):
    req = urllib.request.Request(
        env["url"] + "/api/mcp",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def test_新增MCP服务落盘(env):
    out = post(env, {"action": "add", "name": "fs", "command": "npx",
                     "args": ["-y", "@modelcontextprotocol/server-fs"]})
    assert out["ok"] is True
    data = json.loads(env["path"].read_text(encoding="utf-8"))["servers"]
    row = next(s for s in data if s["name"] == "fs")
    assert row["command"] == "npx"
    assert row["enabled"] is True


def test_删除MCP服务(env):
    post(env, {"action": "add", "name": "a", "command": "x"})
    post(env, {"action": "add", "name": "b", "command": "y"})
    out = post(env, {"action": "remove", "name": "a"})
    assert out["ok"] is True
    data = json.loads(env["path"].read_text(encoding="utf-8"))["servers"]
    assert [s["name"] for s in data] == ["b"]


def test_更新已有服务不改坏其他字段(env):
    post(env, {"action": "add", "name": "a", "command": "x", "args": ["1"], "env": {"K": "V"}})
    post(env, {"action": "add", "name": "a", "command": "x2"})
    data = json.loads(env["path"].read_text(encoding="utf-8"))["servers"]
    row = next(s for s in data if s["name"] == "a")
    assert row["command"] == "x2"
    assert row["args"] == [], "没传 args 应重置为空"
    assert row["env"] == {"K": "V"}, "没传 env 不应清掉"


def test_缺名字或缺命令被拒(env):
    out = post(env, {"action": "add", "name": "", "command": "x"})
    assert out["ok"] is False
    out2 = post(env, {"action": "add", "name": "a", "command": ""})
    assert out2["ok"] is False


def test_探活缺命令被拒(env):
    out = post(env, {"action": "probe", "name": "p", "command": ""})
    assert out["ok"] is False and "命令" in out["error"]



def test_不说话的服务超时报错不挂死(env, monkeypatch):
    """进程活着但从不输出：必须在 timeout 后报错，而不是永久挂住 HTTP 请求。"""
    import jancode_agent.mcp as mm
    import time
    c = mm.MCPClient("hang", "sleep", ["999"], timeout=2.0)
    t0 = time.time()
    try:
        c.start()
        raise AssertionError("不该成功启动")
    except mm.MCPError as exc:
        elapsed = time.time() - t0
        assert "超时" in str(exc), "应当报超时错误，实际：" + str(exc)
        assert elapsed < 10, "超时应当在 deadline 附近触发，实际等了 %.0fs" % elapsed
    finally:
        c.close()
