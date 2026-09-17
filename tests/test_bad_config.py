# -*- coding: utf-8 -*-
"""坏 TOML 配置不能让程序崩：回退默认 + stderr 指路。"""

import io
import contextlib

from jancode_agent.config import load_config


def test_坏配置不崩且回退默认(tmp_path, capsys):
    bad = tmp_path / "config.toml"
    bad.write_text("provider = [broken", encoding="utf-8")
    cfg = load_config(path=bad)
    assert cfg.provider.base_url, "应回退到内置供应商默认"
    out = capsys.readouterr()
    assert "解析失败" in out.err, "必须告诉用户配置没生效"


def test_好配置照常生效(tmp_path):
    good = tmp_path / "config.toml"
    good.write_text(
        "[provider]" + chr(10) + 'name = "deepseek"' + chr(10)
        + 'model = "deepseek-chat"' + chr(10),
        encoding="utf-8",
    )
    cfg = load_config(path=good)
    assert cfg.provider.name == "deepseek"
    assert cfg.provider.model == "deepseek-chat"
