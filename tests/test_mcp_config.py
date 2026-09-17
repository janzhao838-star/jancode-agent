# -*- coding: utf-8 -*-
"""MCP 配置持久化：坏文件不炸、默认值补齐、权限收紧。

load_servers 读的是用户家目录里的 mcp.json——手改坏一行
不该让整个智能体起不来；save 落盘要 600，里面有服务端密钥。
"""

import json

import jancode_agent.mcp as mcp


def test_load缺失文件返回空(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp, "CONFIG_PATH", tmp_path / "nope.json")
    assert mcp.load_servers() == []


def test_load坏JSON不炸(monkeypatch, tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text("{ 不是合法 json", encoding="utf-8")
    monkeypatch.setattr(mcp, "CONFIG_PATH", p)
    assert mcp.load_servers() == []


def test_load字段缺失补默认值(monkeypatch, tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"servers": [
        {"name": "fs", "command": "npx"},          # 无 args/env/enabled
        {"name": "", "command": "x"},              # 名字为空：丢弃
        {"name": "half"},                          # 无 command：丢弃
        "不是字典",                                 # 垃圾行：丢弃
    ]}), encoding="utf-8")
    monkeypatch.setattr(mcp, "CONFIG_PATH", p)
    rows = mcp.load_servers()
    assert len(rows) == 1
    assert rows[0] == {"name": "fs", "command": "npx", "args": [], "env": {}, "enabled": True}


def test_save收紧权限且能回读(monkeypatch, tmp_path):
    p = tmp_path / "sub" / "mcp.json"
    monkeypatch.setattr(mcp, "CONFIG_PATH", p)
    mcp.save_servers([{"name": "fs", "command": "npx", "args": ["-y", "x"],
                       "env": {"K": "V"}, "enabled": False}])
    import stat
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"mcp.json 里有服务端密钥，权限必须是 600，实际 {oct(mode)}"
    assert mcp.load_servers()[0]["enabled"] is False


def test_save列表格式也能读回(monkeypatch, tmp_path):
    """老版本直接落一个数组，load 要兼容。"""
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps([{"name": "old", "command": "run"}]), encoding="utf-8")
    monkeypatch.setattr(mcp, "CONFIG_PATH", p)
    rows = mcp.load_servers()
    assert len(rows) == 1 and rows[0]["name"] == "old"
