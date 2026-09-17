"""中转站接入方式兼容性测试。

中转站文档给的接入片段基本都是 OPENAI_API_KEY / OPENAI_BASE_URL 这一套。
如果只认自己的 JANCODE_*，用户照着文档粘贴命令就是不生效——
而且不报错，只是连到了默认地址，最难查。
"""

from __future__ import annotations

import pytest

from jancode_agent.config import load_config


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in ("JANCODE_API_KEY", "JANCODE_BASE_URL", "JANCODE_MODEL",
                 "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_认中转站文档里的通用变量(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-doc")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-v4.1-flash")
    config = load_config()
    assert config.provider.api_key == "sk-doc"
    assert config.provider.base_url == "https://relay.example.com/v1"
    assert config.provider.model == "deepseek-v4.1-flash"


def test_自己的变量优先(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-doc")
    monkeypatch.setenv("JANCODE_API_KEY", "sk-own")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("JANCODE_BASE_URL", "https://own.example.com/v1")
    config = load_config()
    assert config.provider.api_key == "sk-own"
    assert config.provider.base_url == "https://own.example.com/v1"


def test_空值不算设置(monkeypatch):
    # 空字符串（比如 export FOO= 这种）不能把默认值顶掉
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    config = load_config()
    assert config.provider.api_key == ""
