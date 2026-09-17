"""配置测试。注意：环境变量会覆盖配置文件，测试里必须隔离。"""

from pathlib import Path

import pytest

from jancode_agent.config import BUILTIN_PROVIDERS, load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("JANCODE_API_KEY", "JANCODE_BASE_URL", "JANCODE_MODEL"):
        monkeypatch.delenv(k, raising=False)


def test_无配置文件时用内置默认(tmp_path):
    cfg = load_config(path=tmp_path / "缺失.toml", provider_name="janzhao")
    assert cfg.provider.base_url in BUILTIN_PROVIDERS["janzhao"]["base_url"]
    assert cfg.provider.model


def test_未知供应商给出可用列表(tmp_path):
    with pytest.raises(ValueError) as e:
        load_config(path=tmp_path / "x.toml", provider_name="不存在")
    assert "deepseek" in str(e.value)


def test_配置文件覆盖预设(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('[provider]\nname = "janzhao"\nmodel = "自定义模型"\n', encoding="utf-8")
    cfg = load_config(path=p)
    assert cfg.provider.model == "自定义模型"


def test_环境变量优先级最高(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text('[provider]\nname = "janzhao"\napi_key = "文件里的"\n', encoding="utf-8")
    monkeypatch.setenv("JANCODE_API_KEY", "环境变量的")
    cfg = load_config(path=p)
    assert cfg.provider.api_key == "环境变量的"


def test_自定义供应商缺字段时报错(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[provider]\nname = "我的站"\n', encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_config(path=p)
    assert "base_url" in str(e.value)


def test_内置供应商都是国内或自建():
    """守住「不做国外模型」这条线。

    按性质判断而不是列 id：早前的教训是，只查已知的几个 id
    会漏掉那些不叫 openai 但实际跑国外模型的供应商。
    """
    foreign = ("gpt-", "claude", "gemini", "grok", "mistral", "llama")
    for name, spec in BUILTIN_PROVIDERS.items():
        model = spec["model"].lower()
        assert not any(model.startswith(f) or f"/{f}" in model for f in foreign), \
            f"供应商 {name} 的默认模型 {spec['model']} 疑似国外模型"


def test_预设里没有推广码():
    for name, spec in BUILTIN_PROVIDERS.items():
        blob = " ".join(spec.values())
        for bad in ("aff=", "ref=", "referral", "invite", "/i/"):
            assert bad not in blob, f"供应商 {name} 含推广参数 {bad}"


import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """把和模型接入相关的环境变量清干净。

    这些测试断言的是「没有配置时用什么默认值」，可开发机的 shell 里
    往往导出着 OPENAI_BASE_URL 之类的东西（本机就有，指向另一个中转站）——
    不清掉的话，测试结果就取决于跑测试的人机器上装了什么。
    """
    for name in ("JANCODE_API_KEY", "JANCODE_BASE_URL", "JANCODE_MODEL",
                 "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
