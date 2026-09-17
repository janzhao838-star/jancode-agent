"""技能库与自定义智能体。

两者都是「一小段用户可改的文本 + 一点配置」，放在一个模块里省掉一套几乎
重复的读写代码。

存 JSON 而不是 TOML：标准库的 tomllib 只能读不能写，为了存两个列表去引
一个第三方库不划算。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

LIB_DIR = Path.home() / ".jancode-agent"
SKILLS_PATH = LIB_DIR / "skills.json"
AGENTS_PATH = LIB_DIR / "agents.json"
AUTOMATIONS_PATH = LIB_DIR / "automations.json"

# 技能全文会进系统提示词。不设上限的话，攒到几十条以后每一轮请求都要重新
# 带上几万字——钱和时间都花在重复读同一堆文本上。
MAX_SKILL_CHARS = 12_000


@dataclass
class Skill:
    """一条技能：名字 + 什么时候用 + 具体怎么做。"""

    name: str
    description: str = ""
    content: str = ""
    created: float = 0.0


@dataclass
class Persona:
    """一个自定义智能体。

    名字叫 Persona 而不是 Agent，是为了不和 agent.py 里的执行体混淆：
    这个只是「角色设定 + 默认模型 + 用哪些技能」，真正干活的是 Agent。
    """

    name: str
    system_prompt: str = ""
    model: str = ""
    skills: list[str] = field(default_factory=list)
    created: float = 0.0


@dataclass
class Automation:
    """一条定时/自动化任务。

    enabled 默认关：自动跑任务要花用户自己的额度，
    默认开着等于「装上就开始扣钱」，这种事不能替用户决定。
    """

    name: str
    prompt: str = ""
    schedule: str = ""
    enabled: bool = False
    agent: str = ""
    model: str = ""
    last_run: float = 0.0
    last_result: str = ""
    created: float = 0.0


def _load(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _save(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 技能 ----------


def list_skills() -> list[Skill]:
    out = []
    for raw in _load(SKILLS_PATH):
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        out.append(Skill(
            name=name,
            description=str(raw.get("description") or ""),
            content=str(raw.get("content") or ""),
            created=float(raw.get("created") or 0.0),
        ))
    return out


def find_skill(name: str) -> Skill | None:
    for skill in list_skills():
        if skill.name == name:
            return skill
    return None


def upsert_skill(name: str, description: str = "", content: str = "") -> Skill:
    """新建或覆盖一条技能（按名字）。同名就更新，不另存一份。"""
    items = _load(SKILLS_PATH)
    now = time.time()
    for raw in items:
        if str(raw.get("name") or "").strip() == name:
            raw.update({"name": name, "description": description, "content": content})
            _save(SKILLS_PATH, items)
            return Skill(name, description, content, float(raw.get("created") or now))
    items.append({"name": name, "description": description, "content": content, "created": now})
    _save(SKILLS_PATH, items)
    return Skill(name, description, content, now)


def delete_skill(name: str) -> bool:
    items = _load(SKILLS_PATH)
    kept = [x for x in items if str(x.get("name") or "").strip() != name]
    if len(kept) == len(items):
        return False
    _save(SKILLS_PATH, kept)
    return True


def skills_section(skills: list[Skill] | None = None) -> str:
    """把技能渲染成系统提示词里的一段。

    给全文而不是只给名字：只给名字的话模型不知道自己会不会，要么不用，
    要么凭名字瞎猜着做。技能是用户自己写的，本来就该照做。
    """
    items = skills if skills is not None else list_skills()
    if not items:
        return ""
    parts: list[str] = []
    used = 0
    for skill in items:
        block = f"### {skill.name}\n"
        if skill.description:
            block += f"{skill.description}\n"
        block += skill.content.strip() + "\n"
        if used + len(block) > MAX_SKILL_CHARS:
            parts.append("（技能太多，其余未加载。用 list_skills 查看全部。）")
            break
        used += len(block)
        parts.append(block)
    return "\n已知技能（用户自己写的做法，遇到同类任务照这个来）：\n\n" + "\n".join(parts)


# ---------- 自定义智能体 ----------


def list_agents() -> list[Persona]:
    out = []
    for raw in _load(AGENTS_PATH):
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        skills = raw.get("skills")
        out.append(Persona(
            name=name,
            system_prompt=str(raw.get("system_prompt") or ""),
            model=str(raw.get("model") or ""),
            skills=[str(s) for s in skills] if isinstance(skills, list) else [],
            created=float(raw.get("created") or 0.0),
        ))
    return out


def find_agent(name: str) -> Persona | None:
    for persona in list_agents():
        if persona.name == name:
            return persona
    return None


def upsert_agent(name: str, system_prompt: str = "", model: str = "",
                 skills: list[str] | None = None) -> Persona:
    items = _load(AGENTS_PATH)
    now = time.time()
    picked = list(skills or [])
    for raw in items:
        if str(raw.get("name") or "").strip() == name:
            raw.update({"name": name, "system_prompt": system_prompt,
                        "model": model, "skills": picked})
            _save(AGENTS_PATH, items)
            return Persona(name, system_prompt, model, picked, float(raw.get("created") or now))
    items.append({"name": name, "system_prompt": system_prompt, "model": model,
                  "skills": picked, "created": now})
    _save(AGENTS_PATH, items)
    return Persona(name, system_prompt, model, picked, now)


def delete_agent(name: str) -> bool:
    items = _load(AGENTS_PATH)
    kept = [x for x in items if str(x.get("name") or "").strip() != name]
    if len(kept) == len(items):
        return False
    _save(AGENTS_PATH, kept)
    return True


def persona_prompt(persona: Persona) -> str:
    """把自定义智能体渲染成系统提示词里的一段。"""
    parts = []
    if persona.system_prompt.strip():
        parts.append("你的角色设定：\n" + persona.system_prompt.strip())
    picked = [s for s in (find_skill(n) for n in persona.skills) if s is not None]
    section = skills_section(picked)
    if section:
        parts.append(section)
    return "\n\n".join(parts)


# ---------- 自动化 / 定时任务 ----------


def list_automations() -> list[Automation]:
    out = []
    for raw in _load(AUTOMATIONS_PATH):
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        out.append(Automation(
            name=name,
            prompt=str(raw.get("prompt") or ""),
            schedule=str(raw.get("schedule") or ""),
            enabled=bool(raw.get("enabled")),
            agent=str(raw.get("agent") or ""),
            model=str(raw.get("model") or ""),
            last_run=float(raw.get("last_run") or 0.0),
            last_result=str(raw.get("last_result") or ""),
            created=float(raw.get("created") or 0.0),
        ))
    return out


def upsert_automation(name: str, prompt: str = "", schedule: str = "",
                      enabled: bool = False, agent: str = "", model: str = "") -> Automation:
    items = _load(AUTOMATIONS_PATH)
    now = time.time()
    for raw in items:
        if str(raw.get("name") or "").strip() == name:
            raw.update({"name": name, "prompt": prompt, "schedule": schedule,
                        "enabled": enabled, "agent": agent, "model": model})
            _save(AUTOMATIONS_PATH, items)
            return Automation(name, prompt, schedule, enabled, agent, model,
                              float(raw.get("last_run") or 0.0),
                              str(raw.get("last_result") or ""),
                              float(raw.get("created") or now))
    items.append({"name": name, "prompt": prompt, "schedule": schedule, "enabled": enabled,
                  "agent": agent, "model": model, "last_run": 0.0, "last_result": "",
                  "created": now})
    _save(AUTOMATIONS_PATH, items)
    return Automation(name, prompt, schedule, enabled, agent, model, 0.0, "", now)


def record_run(name: str, result: str) -> None:
    """记下这次自动执行的结果，界面上要看得到跑到哪一步了。"""
    items = _load(AUTOMATIONS_PATH)
    for raw in items:
        if str(raw.get("name") or "").strip() == name:
            raw["last_run"] = time.time()
            raw["last_result"] = result[:2000]
            _save(AUTOMATIONS_PATH, items)
            return


def delete_automation(name: str) -> bool:
    items = _load(AUTOMATIONS_PATH)
    kept = [x for x in items if str(x.get("name") or "").strip() != name]
    if len(kept) == len(items):
        return False
    _save(AUTOMATIONS_PATH, kept)
    return True
