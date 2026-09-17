"""定时任务调度的测试。

时间计算是最容易写错、又最难在界面上发现的那类代码：
写错了照样显示「已配置」，只是永远不触发，或者每三十秒触发一次。
"""

from __future__ import annotations

import time

import pytest

from jancode_agent import library, scheduler


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "AUTOMATIONS_PATH", tmp_path / "automations.json")


def test_识别每N分钟():
    assert scheduler.parse_interval("每 30 分钟") == 1800
    assert scheduler.parse_interval("每15分钟") == 900
    assert scheduler.parse_interval("每 0 分钟") is None
    assert scheduler.parse_interval("每 一会儿 分钟") is None


def test_识别每天几点():
    assert scheduler.parse_daily("每天 09:30") == (9, 30)
    assert scheduler.parse_daily("每天23:59") == (23, 59)
    assert scheduler.parse_daily("每天 25:00") is None
    assert scheduler.parse_daily("每天") is None


def test_没跑过的每N分钟任务立刻就该跑():
    now = time.time()
    assert scheduler.next_run("每 10 分钟", 0.0, now) == now


def test_跑过之后按间隔推进而不是每次都触发():
    now = 1_000_000.0
    last = now - 60
    assert scheduler.next_run("每 10 分钟", last, now) == last + 600


def test_看不懂的写法返回None():
    # 界面靠这个 None 来提示用户「写法不认识」，而不是默默永不触发。
    assert scheduler.next_run("每周一三五", 0.0, time.time()) is None
    assert scheduler.next_run("", 0.0, time.time()) is None


def test_到点的任务才会被挑出来():
    now = time.time()
    due = library.upsert_automation("甲", "做点事", "每 1 分钟", enabled=True)
    library.upsert_automation("乙", "做点事", "每 1 分钟", enabled=False)
    picked = scheduler.due_now(library.list_automations(), now)
    assert [x.name for x in picked] == [due.name]


def test_没写任务内容的不会被触发():
    library.upsert_automation("空任务", "   ", "每 1 分钟", enabled=True)
    assert scheduler.due_now(library.list_automations(), time.time()) == []


def test_执行结果会记回去():
    library.upsert_automation("甲", "做点事", "每 1 分钟", enabled=True)
    item = library.list_automations()[0]
    text = scheduler.run_once(item, _config(), lambda cfg, prompt: "跑完了")
    assert text == "跑完了"
    library.record_run(item.name, text)
    saved = library.list_automations()[0]
    assert saved.last_result == "跑完了"
    assert saved.last_run > 0


def test_执行失败不会让调度线程挂掉():
    library.upsert_automation("甲", "做点事", "每 1 分钟", enabled=True)
    item = library.list_automations()[0]

    def boom(cfg, prompt):
        raise RuntimeError("网络断了")

    assert "网络断了" in scheduler.run_once(item, _config(), boom)


def _config():
    from jancode_agent.config import load_config

    return load_config(workspace=__import__("pathlib").Path("."))
