# -*- coding: utf-8 -*-
"""桌面版启动路径的配置合并：apply_saved_settings / _env_wins。

桌面版双击启动时唯一可靠的配置来源是界面里保存的设置，
而脚本/CI 临时指定的 JANCODE_* 环境变量必须反过来赢过存档。
这条优先级一旦搞反，症状是「明明配好了却连不上」——最难查的一类。
"""

import pytest

import jancode_agent.server as sm
from jancode_agent.config import AgentConfig, ProviderConfig


def _cfg():
    return AgentConfig(
        provider=ProviderConfig(name="t", base_url="http://x/v1",
                                model="m", api_key=""),
    )


def test_环境变量赢过一切(monkeypatch):
    """JANCODE_* 设置时必须盖掉 provider 现值。"""
    monkeypatch.setenv("JANCODE_API_KEY", "sk-env")
    monkeypatch.setenv("JANCODE_BASE_URL", "http://env/v1")
    monkeypatch.setenv("JANCODE_MODEL", "env-model")
    p = sm._env_wins(_cfg().provider)
    assert p.api_key == "sk-env"
    assert p.base_url == "http://env/v1"
    assert p.model == "env-model"


def test_没有环境变量时原样返回(monkeypatch):
    for name in ("JANCODE_API_KEY", "JANCODE_BASE_URL", "JANCODE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    p = sm._env_wins(_cfg().provider)
    assert p.base_url == "http://x/v1"
    assert p.model == "m"


def test_没有活跃供应商时配置原样返回(monkeypatch):
    monkeypatch.setattr(sm, "load_providers", lambda: ([], "ghost"))
    cfg = _cfg()
    assert sm.apply_saved_settings(cfg) is cfg


def test_存档配置被套上且环境变量仍能赢(monkeypatch, tmp_path):
    monkeypatch.setattr(sm, "load_providers", lambda: ([
        {"name": "active", "base_url": "http://saved/v1",
         "api_key": "sk-saved", "model": "saved-model"},
    ], "active"))
    monkeypatch.delenv("JANCODE_API_KEY", raising=False)
    got = sm.apply_saved_settings(_cfg())
    assert got.provider.api_key == "sk-saved"
    assert got.provider.model == "saved-model"
    # 再给环境变量：它赢
    monkeypatch.setenv("JANCODE_MODEL", "env-model")
    got = sm.apply_saved_settings(_cfg())
    assert got.provider.model == "env-model"
    assert got.provider.api_key == "sk-saved"  # 没设 KEY，存档的保留
