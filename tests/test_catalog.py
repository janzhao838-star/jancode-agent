# -*- coding: utf-8 -*-
"""模型目录：数据完整性——界面直接渲染这份列表，坏了就是白屏或假地址。"""

from jancode_agent.catalog import CATALOG, all_model_names, vendors


def test_每条目录字段齐全():
    for item in CATALOG:
        assert item["vendor"].strip(), item
        assert item["base_url"].startswith("http"), item
        assert item["models"], item
        assert all(m.strip() for m in item["models"]), item


def test_base_url不重复():
    urls = [item["base_url"] for item in CATALOG]
    assert len(urls) == len(set(urls)), "同一地址出现两次会渲染重复选项"


def test_列表函数():
    assert vendors() is not None and len(vendors()) == len(CATALOG)
    names = all_model_names()
    assert len(names) == sum(len(i["models"]) for i in CATALOG)
