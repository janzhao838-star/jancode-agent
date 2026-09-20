# -*- coding: utf-8 -*-
"""会话持久化：对话历史落盘，重启不丢，坏档不崩。"""

import json
import os
import stat
import threading

import pytest

from jancode_agent.config import AgentConfig, ProviderConfig
from jancode_agent.server import build_server
from jancode_agent.sessions import (
    MAX_SESSIONS,
    load_store,
    save_store,
)


def _cfg():
    return AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x:1/v1", model="m", api_key="k"),
    )


@pytest.fixture()
def api(tmp_path):
    httpd = build_server(port=0, config=_cfg(), require_key=False,
                         settings_path=tmp_path / "settings.json",
                         sessions_path=tmp_path / "sessions.json")
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(base, path):
    import urllib.request
    with urllib.request.urlopen(base + path) as r:
        return json.loads(r.read())


def _post(base, path, payload):
    import urllib.request
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _sample_store():
    return {
        "list": [{
            "id": "s1",
            "title": "周报助手",
            "ts": 1700000000000,
            "turns": [
                {"prompt": "帮我写周报", "answer": "好的，内容如下……",
                 "tools": [{"name": "read_file", "text": "……", "ok": True}],
                 "error": "", "status": "ok", "ts": 1700000001000},
            ],
        }],
        "current": "s1",
    }


def test_存档往返内容一字不差(tmp_path):
    p = tmp_path / "sessions.json"
    store = _sample_store()
    save_store(p, store)
    back = load_store(p)
    assert back["current"] == "s1"
    assert back["list"][0]["title"] == "周报助手"
    assert back["list"][0]["turns"][0]["answer"] == "好的，内容如下……"
    assert back["list"][0]["turns"][0]["tools"][0]["name"] == "read_file"


def test_存档文件权限收紧到600(tmp_path):
    if os.name == "nt":
        # Windows 的 FAT/NTFS 不吃 POSIX chmod（os.chmod 只动只读位），
        # 438=0o666 是预期值。隐私保护在 Windows 上由用户目录 ACL 承担。
        import pytest
        pytest.skip("Windows 无 POSIX 文件模式")
    p = tmp_path / "sessions.json"
    save_store(p, _sample_store())
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_超上限旧会话被裁掉(tmp_path):
    p = tmp_path / "sessions.json"
    sessions = [
        {"id": f"s{i}", "title": f"对话{i}", "ts": i, "turns": []}
        for i in range(MAX_SESSIONS + 50)
    ]
    save_store(p, {"list": sessions, "current": "s0"})
    back = load_store(p)
    assert len(back["list"]) == MAX_SESSIONS
    # 裁的是最旧的：新的留、旧的走
    ids = {s["id"] for s in back["list"]}
    assert "s0" not in ids and f"s{MAX_SESSIONS + 49}" in ids


def test_坏档按空档处理不抛异常(tmp_path):
    p = tmp_path / "sessions.json"
    p.write_text("{这不是JSON", encoding="utf-8")
    assert load_store(p) == {"list": [], "current": None}

    missing = tmp_path / "nope.json"
    assert load_store(missing) == {"list": [], "current": None}


def test_没有id的会话被丢弃(tmp_path):
    p = tmp_path / "sessions.json"
    save_store(p, {"list": [
        {"id": "", "title": "垃圾", "ts": 1, "turns": []},
        {"title": "也没id", "ts": 2, "turns": []},
        {"id": "ok", "title": "好会话", "ts": 3, "turns": []},
    ], "current": None})
    back = load_store(p)
    assert [s["id"] for s in back["list"]] == ["ok"]


def test_接口保存后再读回一致(api, tmp_path):
    d = _post(api, "/api/sessions", {"store": _sample_store()})
    assert d["ok"] is True and d["count"] == 1
    back = _get(api, "/api/sessions")
    assert back["ok"] is True
    assert back["store"]["list"][0]["id"] == "s1"
    assert back["store"]["list"][0]["turns"][0]["prompt"] == "帮我写周报"


def test_接口拒绝没有list的负载(api):
    d = _post(api, "/api/sessions", {"store": {"current": "s1"}})
    assert d["ok"] is False


def test_重启新服务实例还能读到存档(tmp_path):
    """持久化的意义就在这一条：换一个进程（重启应用）历史还在。"""
    p = tmp_path / "sessions.json"
    save_store(p, _sample_store())
    httpd2 = build_server(port=0, config=_cfg(), require_key=False,
                          settings_path=tmp_path / "settings.json",
                          sessions_path=p)
    port = httpd2.server_address[1]
    th = threading.Thread(target=httpd2.serve_forever, daemon=True)
    th.start()
    try:
        back = _get(f"http://127.0.0.1:{port}", "/api/sessions")
        assert back["store"]["list"][0]["title"] == "周报助手"
    finally:
        httpd2.shutdown()


def test_单会话轮数封顶(tmp_path):
    p = tmp_path / "sessions.json"
    turns = [{"prompt": f"问{i}", "answer": "答", "ts": i} for i in range(1500)]
    save_store(p, {"list": [{"id": "s", "title": "长对话", "ts": 1, "turns": turns}],
                   "current": "s"})
    back = load_store(p)
    assert len(back["list"][0]["turns"]) <= 1000
    # 留的是最新的轮次
    assert back["list"][0]["turns"][-1]["prompt"] == "问1499"

