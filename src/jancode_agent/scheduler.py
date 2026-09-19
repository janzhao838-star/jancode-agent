"""定时任务的调度。

只认三种时间写法：每 N 分钟、每小时、每天 HH:MM。

为什么不支持 cron 表达式：写全 cron 就是重造一个 cron，而用户真正会用的
就这几种。看不懂的写法直接判为「不合法」，让界面明确报错，
比默默按错误的规则跑要安全——自动任务跑错的代价是用户的钱。
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta

from .library import Automation, list_automations, record_run


def parse_interval(schedule: str) -> int | None:
    """「每 30 分钟」→ 1800 秒。"""
    text = schedule.strip().replace(" ", "")
    if not text.startswith("每") or not text.endswith("分钟"):
        return None
    number = text[1:-2]
    if not number.isdigit():
        return None
    minutes = int(number)
    return minutes * 60 if minutes > 0 else None


def is_hourly(schedule: str) -> bool:
    return schedule.strip().replace(" ", "") == "每小时"


def parse_daily(schedule: str) -> tuple[int, int] | None:
    """「每天 09:30」→ (9, 30)。"""
    text = schedule.strip().replace(" ", "")
    if not text.startswith("每天") or ":" not in text:
        return None
    hour_text, _, minute_text = text[2:].partition(":")
    if not (hour_text.isdigit() and minute_text.isdigit()):
        return None
    hour, minute = int(hour_text), int(minute_text)
    if 0 <= hour < 24 and 0 <= minute < 60:
        return hour, minute
    return None


def _advance(moment: float, step: float, now: float) -> float:
    """把错过的时间点直接跳到下一个将来，不补跑。

    补跑会在关了很久的机器上一次性触发很多次——既花额度又吓人。
    宁可跳过，也不要突然跑一堆。
    """
    if moment > now:
        return moment
    missed = int((now - moment) // step) + 1
    return moment + missed * step


def next_run(schedule: str, last_run: float, now: float | None = None) -> float | None:
    """算出下一次该跑的时间戳。写法不认识就返回 None。

    一律基于「上次跑的时间」而不是「当前时间」来推进：
    用当前时间的话，服务重启一次就会把当天该跑的漏掉，
    或者反过来每分钟触发一次。
    """
    now = time.time() if now is None else now

    interval = parse_interval(schedule)
    if interval is not None:
        if not last_run:
            return now
        return _advance(last_run + interval, interval, now)

    if is_hourly(schedule):
        if not last_run:
            return now
        return _advance(last_run + 3600, 3600, now)

    daily = parse_daily(schedule)
    if daily is not None:
        hour, minute = daily
        # 锚点不能落在 epoch 之前：server 校验写法时会传 (0, 0)，
        # fromtimestamp(0) 得到 1970 年，后面 .timestamp() 是负数，
        # Windows 的 localtime 不认负时间戳，直接 OSError 22。
        # 把 2001 年之前一律当「从没跑过」钳到不早于 epoch+1 天。
        anchor = last_run or now
        if anchor < 1e9:
            anchor = max(now, 86400.0)
        base = datetime.fromtimestamp(anchor)
        when = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when.timestamp() <= (last_run or now - 86400):
            when += timedelta(days=1)
        if when.timestamp() <= now and last_run:
            when += timedelta(days=1)
        # 兜底：无论哪种情况，返回的都必须是将来。
        # 之前「从没跑过 + 今天的点已经过去」会返回今天那个已经过去的时间，
        # 调用方拿去算「还有多久跑」会得到负数，调度判断也跟着错。
        while when.timestamp() <= now:
            when += timedelta(days=1)
        return when.timestamp()

    return None


def due_now(items: list[Automation], now: float | None = None) -> list[Automation]:
    now = time.time() if now is None else now
    out = []
    for item in items:
        if not item.enabled or not item.prompt.strip():
            continue
        moment = next_run(item.schedule, item.last_run, now)
        if moment is not None and moment <= now:
            out.append(item)
    return out


def run_once(item: Automation, config, runner) -> str:
    """跑一条自动化任务，返回结果文本。

    异常一律吞掉并转成结果文本：一条任务失败不该把整个调度线程带走——
    线程一死，之后所有定时任务都静默不跑了，而这在界面上看不出来。
    """
    from dataclasses import replace

    from .library import find_agent, persona_prompt, skills_section

    run_config = config
    if item.agent:
        persona = find_agent(item.agent)
        if persona is not None:
            run_config = replace(run_config, system_extra=persona_prompt(persona))
            if not item.model and persona.model:
                item = replace(item, model=persona.model)
    else:
        run_config = replace(run_config, system_extra=skills_section())
    if item.model:
        run_config = replace(
            run_config, provider=replace(run_config.provider, model=item.model))

    try:
        return runner(run_config, item.prompt)
    except Exception as exc:
        return f"执行失败：{exc}"


_thread: threading.Thread | None = None


def start(config, interval: float = 30.0) -> threading.Thread:
    """起一个后台线程盯着定时任务。

    daemon=True：主程序退出时它必须跟着走，否则关掉窗口后
    后台还在偷偷跑任务、还在花用户的额度。
    """

    async def _default_runner(run_config, prompt: str) -> str:
        from .agent import Agent

        parts: list[str] = []
        async with Agent(run_config) as agent:
            async for step in agent.run(prompt):
                if step.kind == "answer" and not step.subagent:
                    if step.delta:
                        # 流式回答是一串 delta 分片，收尾还会发一个空的
                        # answer 标记。覆盖式赋值的话最后只剩空串——
                        # 任务实际跑成了，记录里却是「（没有结论）」。
                        parts.append(step.text)
                    elif step.text:
                        parts = [step.text]
                elif step.kind == "error" and not step.subagent:
                    parts = [f"失败：{step.text}"]
        last = "".join(parts)
        return last or "（没有结论）"

    def _runner(run_config, prompt: str) -> str:
        return asyncio.run(_default_runner(run_config, prompt))

    def _loop() -> None:
        while True:
            try:
                for item in due_now(list_automations()):
                    result = run_once(item, config, _runner)
                    record_run(item.name, result)
            except Exception:
                # 调度线程绝不能死：它一死，所有定时任务都静默停了。
                pass
            time.sleep(interval)

    # 幂等：桌面版入口和 serve() 都会调 start，重复起线程会让同一个
    # 定时任务被两个循环同时触发——额度双倍消耗，结果文件互相覆盖。
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    thread = threading.Thread(target=_loop, name="jancode-scheduler", daemon=True)
    thread.start()
    _thread = thread
    return thread
