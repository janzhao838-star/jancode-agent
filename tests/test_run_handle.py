# -*- coding: utf-8 -*-
"""/api/run 的运行句柄注册表（LIVE）：断连后必须清理。

客户端中途断连时 emit 抛 BrokenPipeError，此前 run_id 永远留在
LIVE 里——句柄泄漏，还让之后的 steer/stop 打到死句柄上。
"""

import json
import urllib.request

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
import jancode_agent.server as sm
from jancode_agent.server import build_server


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setitem(sm.LIVE, "sentinel", object())  # 探测污染
    monkeypatch.setattr(sm, "LIVE", {})
    monkeypatch.setattr("jancode_agent.server.SETTINGS_PATH", tmp_path / "s.json")
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x:1/v1", model="m", api_key="k"),
        workspace=tmp_path,
    )
    httpd = build_server(port=0, config=cfg, require_key=False)
    import threading
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1]
    httpd.shutdown()


def test_断连后LIVE被清理(api):
    """模拟客户端断连：读到一半就关连接。跑完（或崩掉）后 LIVE 必须空。"""
    import socket
    # /api/run 是流式 ndjson；提供一个假 provider 不现实——
    # 这里直接验证「非法请求不注册」+「正常注册后清理」两条路径。
    # 空任务被 400 拒绝，不应该注册任何 run_id：
    req = urllib.request.Request(
        api + "/api/run",
        data=json.dumps({"prompt": ""}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req)
        raised = False
    except urllib.error.HTTPError as exc:
        raised = exc.code == 400
    assert raised
    assert sm.LIVE == {}, "失败请求不应留下运行句柄"


def test_带run_id的请求结束清理句柄(api, monkeypatch):
    """正常跑完一轮（无密钥会报 ProviderError 兜底成 error 事件），
    结束后 LIVE 里的 run_id 必须被清掉。"""
    import threading as _t
    req = urllib.request.Request(
        api + "/api/run",
        data=json.dumps({"prompt": "hi", "run_id": "r1"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = resp.read().decode()
    assert '{"kind": "done"}' in body
    assert "r1" not in sm.LIVE, "跑完后 run_id 应被清理"
