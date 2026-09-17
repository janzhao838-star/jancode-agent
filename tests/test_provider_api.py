# -*- coding: utf-8 -*-
"""接入配置的增删改与切换接口。"""

import json
import urllib.request

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.server import build_server


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import jancode_agent.server as sm
    sp = tmp_path / "desktop-settings.json"
    monkeypatch.setattr(sm, "SETTINGS_PATH", sp)
    cfg = AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://old:1234/v1", model="m1", api_key="k-old"),
        workspace=tmp_path,
    )
    httpd = build_server(port=0, config=cfg, require_key=False)
    port = httpd.server_address[1]
    import threading
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield {"url": "http://127.0.0.1:" + str(port), "sm": sm, "path": sp}
    httpd.shutdown()


def post(url, path, body):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def test_新增并激活一套配置(env):
    sm = env["sm"]
    out = post(env["url"], "/api/providers",
               {"action": "save", "name": "客户A", "base_url": "http://a/v1",
                "api_key": "ka", "model": "ma"})
    assert out["ok"] is True and out["active"] == "客户A"
    assert sm.Handler.config.provider.api_key == "ka", "保存并激活后应立刻生效"
    assert sm.Handler.config.provider.base_url == "http://a/v1"


def test_切换配置生效(env):
    sm = env["sm"]
    post(env["url"], "/api/providers",
         {"action": "save", "name": "A", "base_url": "http://a/v1", "api_key": "ka", "model": "ma"})
    post(env["url"], "/api/providers",
         {"action": "save", "name": "B", "base_url": "http://b/v1", "api_key": "kb", "model": "mb"})
    out = post(env["url"], "/api/providers", {"action": "activate", "name": "A"})
    assert out["ok"] is True
    assert sm.Handler.config.provider.api_key == "ka", "切换后应换回A的密钥"


def test_切换到不存在的配置报错(env):
    out = post(env["url"], "/api/providers", {"action": "activate", "name": "不存在"})
    assert out["ok"] is False


def test_删除当前激活配置后回落到第一套(env):
    sm = env["sm"]
    post(env["url"], "/api/providers",
         {"action": "save", "name": "A", "base_url": "http://a/v1", "api_key": "ka", "model": "ma"})
    post(env["url"], "/api/providers",
         {"action": "save", "name": "B", "base_url": "http://b/v1", "api_key": "kb", "model": "mb"})
    out = post(env["url"], "/api/providers", {"action": "delete", "name": "B"})
    assert out["ok"] is True
    assert sm.Handler.config.provider.api_key == "ka", "删掉B后应保持A（第一套）"


def test_密钥留空表示不改(env):
    post(env["url"], "/api/providers",
         {"action": "save", "name": "A", "base_url": "http://a/v1", "api_key": "ka", "model": "ma"})
    post(env["url"], "/api/providers",
         {"action": "save", "name": "A", "base_url": "http://a/v1", "model": "ma2"})
    data = json.loads(env["path"].read_text(encoding="utf-8"))
    row = next(i for i in data["providers"] if i["name"] == "A")
    assert row["api_key"] == "ka", "密钥留空不应被清掉"
    assert row["model"] == "ma2"


def test_环境变量优先于界面切换(env, monkeypatch):
    """JANCODE_* 设着的时候，界面切换不能盖掉它（apply_saved_settings 同款原则）。"""
    sm = env["sm"]
    post(env["url"], "/api/providers",
         {"action": "save", "name": "A", "base_url": "http://a/v1", "api_key": "ka", "model": "ma"})
    monkeypatch.setenv("JANCODE_API_KEY", "env-key")
    post(env["url"], "/api/providers", {"action": "activate", "name": "A"})
    assert sm.Handler.config.provider.api_key == "env-key", "环境变量必须赢过界面配置"
    assert sm.Handler.config.provider.base_url == "http://a/v1", "没设环境变量的字段照常切换"
