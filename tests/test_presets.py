# -*- coding: utf-8 -*-
"""内置模式预设：清单、切换、复制成自定义智能体。

预设是「拿到软件就能用」的第一屏，切不过去或复制不出来都是白做。
全部走 HTTP 真实验证，不 mock。
"""
import json

import pytest

from jancode_agent.presets import BUILTIN_PRESETS, find_preset


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """起真实服务：隔离 HOME（不碰真实配置），不设密钥也能起（配假密钥）。"""
    import os
    import threading
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    import jancode_agent.server as server_mod

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Windows 的 Path.home() 走 USERPROFILE，不认 HOME——两个都指向临时目录，
    # 否则测试里读 Path.home() 会读到 runner 的真实家目录。
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("JANCODE_AGENT_CONFIG", str(home / "config.toml"))
    # 库模块的路径是 import 时算好的，要跟着 HOME 走
    from jancode_agent import library
    lib_dir = home / ".jancode-agent"
    monkeypatch.setattr(library, "LIB_DIR", lib_dir, raising=False)
    monkeypatch.setattr(library, "SKILLS_PATH", lib_dir / "skills.json", raising=False)
    monkeypatch.setattr(library, "AGENTS_PATH", lib_dir / "agents.json", raising=False)
    monkeypatch.setattr(server_mod, "SETTINGS_PATH", lib_dir / "desktop-settings.json", raising=False)
    for k in [k for k in os.environ if k.upper().startswith(("OPENAI_", "JANCODE_"))]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("JANCODE_API_KEY", "sk-dummy-local-test")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    httpd.daemon_threads = True
    server_mod.Handler.config = server_mod.load_config()
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base
    httpd.shutdown()


def get(base, path):
    import urllib.request
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read())


def post(base, path, body):
    import urllib.request
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def test_presets_ship_with_server(api):
    d = get(api, "/api/agents")
    assert d["ok"]
    names = [p["name"] for p in d["presets"]]
    assert names == ["标准模式", "计划模式", "极简模式", "创造模式"]


def test_use_preset_persists_mode(api):
    d = post(api, "/api/agents", {"use_preset": "计划模式"})
    assert d["ok"] and d["mode"] == "plan"
    import pathlib
    settings = json.loads(
        (pathlib.Path.home() / ".jancode-agent/desktop-settings.json").read_text())
    assert settings["active_mode"] == "plan"
    assert settings["preset_prompt"]


def test_use_standard_clears_prompt(api):
    post(api, "/api/agents", {"use_preset": "极简模式"})
    d = post(api, "/api/agents", {"use_preset": "标准模式"})
    assert d["ok"]
    import pathlib
    settings = json.loads(
        (pathlib.Path.home() / ".jancode-agent/desktop-settings.json").read_text())
    assert settings["active_mode"] == "auto"
    assert "preset_prompt" not in settings  # 标准模式不加戏


def test_install_preset_copies_to_my_agents(api):
    d = post(api, "/api/agents", {"install_preset": "创造模式"})
    assert d["ok"]
    agents = get(api, "/api/agents")["agents"]
    mine = [a for a in agents if a["name"] == "创造模式"]
    assert mine, "复制出来的智能体必须出现在列表里"
    assert mine[0]["system_prompt"] == find_preset("创造模式")["system_prompt"]


def test_unknown_preset_rejected(api):
    d = post(api, "/api/agents", {"use_preset": "不存在的预设"})
    assert not d["ok"]


def test_preset_modes_all_valid():
    # 模式名必须是 server MODES 认的四个，写错会被强制 readonly
    valid = {"auto", "sandbox", "plan", "readonly"}
    for p in BUILTIN_PRESETS:
        assert p["mode"] in valid, f"{p[chr(110)+chr(97)+chr(109)+chr(101)]} 的 mode 不合法"

def test_改返回值不污染预设本体():
    """find_preset 必须给拷贝。调用方改了返回值，
    随代码分发的预设不能跟着变——否则这个进程里后续
    所有「以此为准」拿到的都是被改脏的预设。
    """
    from jancode_agent.presets import BUILTIN_PRESETS, find_preset

    preset = find_preset("标准模式")
    preset["system_prompt"] = "被我改了"
    preset["mode"] = "plan"

    original = next(p for p in BUILTIN_PRESETS if p["name"] == "标准模式")
    assert original["system_prompt"] == ""
    assert original["mode"] == "auto"
    assert find_preset("标准模式")["mode"] == "auto"
