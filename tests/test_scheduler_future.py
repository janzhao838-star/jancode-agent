import time
from datetime import datetime, timedelta

from jancode_agent import scheduler


def test_daily_never_run_returns_future():
    """从没跑过、且今天的点已经过去时，必须给明天，不能给今天那个过去的时间。"""
    now = time.time()
    got = scheduler.next_run("每天 00:01", 0, now)
    assert got is not None and got > now


def test_daily_after_today_returns_tomorrow():
    now = time.time()
    got = scheduler.next_run("每天 00:01", now - 3600, now)
    assert got > now
    # 应当是明天那个点，误差不超过 2 分钟
    when = datetime.fromtimestamp(got)
    assert when.hour == 0 and when.minute == 1


def test_interval_long_ago_skips_to_future():
    """机器关了三天再开机，不能补跑 72 次。"""
    now = time.time()
    got = scheduler.next_run("每 60 分钟", now - 3 * 86400, now)
    assert got > now
    assert got - now <= 3600 + 1


def test_hourly_long_ago_skips_to_future():
    now = time.time()
    got = scheduler.next_run("每小时", now - 5 * 3600, now)
    assert got > now
    assert got - now <= 3600 + 1
