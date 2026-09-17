# -*- coding: utf-8 -*-
"""配置文件字段不能静默丢失。

load_config 以前漏读 provider.effort、agent.mode、agent.system_extra：
Web 端能设的东西写进 config.toml 却不生效，还无任何提示。
"""

from jancode_agent.config import load_config


def _write(tmp_path, body):
    f = tmp_path / "config.toml"
    f.write_text(body, encoding="utf-8")
    return f


def test_内置供应商读取effort(tmp_path):
    f = _write(tmp_path, chr(10).join([
        "[provider]",
        'name = "deepseek"',
        'effort = "high"',
    ]) + chr(10))
    cfg = load_config(path=f)
    assert cfg.provider.effort == "high"


def test_自定义供应商读取effort(tmp_path):
    f = _write(tmp_path, chr(10).join([
        "[provider]",
        'name = "自建"',
        'base_url = "http://x/v1"',
        'model = "m"',
        'effort = "low"',
    ]) + chr(10))
    cfg = load_config(path=f)
    assert cfg.provider.effort == "low"


def test_读取mode与system_extra(tmp_path):
    f = _write(tmp_path, chr(10).join([
        "[provider]",
        'name = "deepseek"',
        "[agent]",
        'mode = "readonly"',
        'system_extra = "你是专职审阅者"',
    ]) + chr(10))
    cfg = load_config(path=f)
    assert cfg.mode == "readonly"
    assert cfg.system_extra == "你是专职审阅者"


def test_未写字段保持默认(tmp_path):
    f = _write(tmp_path, "[provider]" + chr(10) + 'name = "deepseek"' + chr(10))
    cfg = load_config(path=f)
    assert cfg.provider.effort == ""
    assert cfg.mode == "auto"
    assert cfg.system_extra == ""