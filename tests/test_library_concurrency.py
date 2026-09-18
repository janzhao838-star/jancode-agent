# -*- coding: utf-8 -*-
"""库文件并发写：定时任务线程与界面线程同时改 automations.json。

以前两线程共用同一个 .tmp 临时文件，一方 os.replace 改名后，
另一方对着不存在的文件抛 FileNotFoundError，调度线程直接崩。
"""

import threading

import jancode_agent.library as lib


def test_并发写不崩溃不丢条目(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "AUTOMATIONS_PATH", tmp_path / "automations.json")
    lib.upsert_automation("甲", "任务一", "每天 09:00")
    lib.upsert_automation("乙", "任务二", "每小时")

    errors = []
    barrier = threading.Barrier(2)

    def writer():
        barrier.wait()
        try:
            for i in range(50):
                lib.record_run("甲", "结果" + str(i))
        except Exception as exc:  # 崩溃即记录
            errors.append(exc)

    def editor():
        barrier.wait()
        try:
            for i in range(50):
                lib.upsert_automation("乙", "任务二改", "每天 10:00", enabled=True)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=writer)
    t2 = threading.Thread(target=editor)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert not errors, errors
    rows = {r.name: r for r in lib.list_automations()}
    assert set(rows) == {"甲", "乙"}
    assert rows["甲"].last_run > 0
    assert rows["乙"].enabled and rows["乙"].schedule == "每天 10:00"