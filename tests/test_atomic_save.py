# -*- coding: utf-8 -*-
"""library 存盘的原子性：写入中断不能清空用户数据。

_save 直接 write_text 覆盖的话，中途崩溃留下截断 JSON，
_load 读不出就返回空列表——技能库静默清空。
原子写（临时文件 + os.replace）保证读者要么看到旧完整版，
要么看到新完整版。"""

import json
import os
import threading

from jancode_agent import library as lib


def test_存盘不留下临时文件(tmp_path, monkeypatch):
    path = tmp_path / "skills.json"
    monkeypatch.setattr(lib, "SKILLS_PATH", path)
    lib.upsert_skill("t", "d", "c")
    assert path.exists()
    assert not path.with_name(path.name + ".tmp").exists(), "换名后临时文件应消失"


def test_写入后任何时刻文件都是完整JSON(tmp_path, monkeypatch):
    """并发读写下的基本保证：load 到的永远是合法 JSON（旧或新）。"""
    path = tmp_path / "skills.json"
    monkeypatch.setattr(lib, "SKILLS_PATH", path)
    lib.upsert_skill("s1", "", "x" * 5000)
    bad = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except PermissionError:
                # Windows 上 replace 换名瞬间读者会撞上瞬态拒绝访问，
                # 重试即可——这不是「读到坏文件」。应用层 _load 同样处理。
                time.sleep(0.02)
            except (OSError, ValueError) as exc:
                bad.append(exc)

    th = threading.Thread(target=reader)
    th.start()
    for i in range(30):
        lib.upsert_skill(f"s{i}", "", "y" * 5000)
    stop.set()
    th.join()
    assert not bad, f"读到过不完整的文件: {bad[:1]}"


def test_截断的旧文件当空库处理且下次存盘修复(tmp_path, monkeypatch):
    """极端情况（老版本留下的截断文件）不崩、可恢复。"""
    path = tmp_path / "skills.json"
    path.write_text('[{"name": "a", "', encoding="utf-8")
    monkeypatch.setattr(lib, "SKILLS_PATH", path)
    assert lib.list_skills() == []
    lib.upsert_skill("b", "", "c")
    assert [s.name for s in lib.list_skills()] == ["b"]
