# -*- coding: utf-8 -*-
"""会话持久化：对话记录落到本机文件，关窗口、重启应用都不丢。

之前对话历史只存在浏览器 localStorage 里：清浏览器数据、换台机器、
或者桌面壳换了个 WebView 配置，历史就全没了。现在界面每次保存都同步
一份到本地服务，服务端原子写进 ~/.jancode-agent/sessions.json（0600）。

格式上做清洗和上限：会话数、单会话轮数都封顶，防止一份数据悄悄长到
几百 MB 把启动拖死；坏数据宁可丢也不让读档崩掉。
"""

import json
import os
import tempfile
from pathlib import Path

# 上限取「正常重度用户也够用」的量级：500 个会话 × 每个最多 1000 轮。
MAX_SESSIONS = 500
MAX_TURNS = 1000


def _sanitize_store(raw: object) -> dict | None:
    """清洗界面发来的存储快照，不合规范的条目直接丢弃。

    会话按时间新到旧排，超上限的旧会话裁掉。返回 None 表示整体无效。
    """
    if not isinstance(raw, dict):
        return None
    lst = raw.get("list")
    if not isinstance(lst, list):
        return None
    sessions = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if not sid:
            continue
        turns = item.get("turns")
        turns = turns if isinstance(turns, list) else []
        ts = item.get("ts")
        sessions.append({
            "id": sid,
            "title": str(item.get("title") or "新对话"),
            "ts": ts if isinstance(ts, (int, float)) else 0,
            "turns": turns[-MAX_TURNS:],
        })
    sessions.sort(key=lambda s: s["ts"], reverse=True)
    sessions = sessions[:MAX_SESSIONS]
    current = raw.get("current")
    current = current if isinstance(current, str) else None
    return {"list": sessions, "current": current}


def save_store(path: Path, raw: object) -> dict:
    """原子写盘。先写临时文件再改名，写一半断电也不会留下半个坏档。"""
    store = _sanitize_store(raw)
    if store is None:
        raise ValueError("store 必须是带 list 的对象")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".sessions-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(store, ensure_ascii=False))
        os.chmod(tmp, 0o600)  # 对话内容是隐私，别让同机其他用户读到
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return store


def load_store(path: Path) -> dict:
    """读档。文件缺失或损坏都按空档处理，绝不抛异常打断启动。

    PermissionError 重试几次：Windows 上杀毒软件和搜索索引会短暂锁住
    刚写完的文件，锁一下就读不到了，直接当空档会把用户历史「弄丢」。
    """
    for _ in range(3):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"list": [], "current": None}
        except (PermissionError, OSError):
            continue
        except ValueError:
            return {"list": [], "current": None}
        clean = _sanitize_store(data)
        return clean if clean is not None else {"list": [], "current": None}
    return {"list": [], "current": None}

