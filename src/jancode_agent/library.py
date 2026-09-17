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


# ---------- 内置技能：随程序提供，点「安装」才写进你的技能库 ----------
#
# 这些不是摆设文案，每一条都是真正会改变智能体做法的指令：
# 装上去之后，之后每次任务都会带上它，输出格式和检查项都会按它来。
# 写成「做法」而不是「知识」——知识模型本来就有，值得固化的只有做法。

BUILTIN_SKILLS: list[dict] = [
    {
        "name": "周报生成",
        "category": "效率工具",
        "description": "把零散的工作记录整理成领导能直接看的周报：成果、数据、风险、下周计划。",
        "content": """写周报时按这个结构：
1. 本周成果：最多 5 条，每条一句话说清楚「做了什么 + 带来什么变化」。
2. 关键数据：能量化就给数字；给不出数字就说明为什么量化不了。
3. 风险与阻塞：只写真正卡住的，写清楚卡在谁/什么上、需要什么支持。
4. 下周计划：3 条以内，每条可验证。
禁止：流水账（「参加了会议」）、形容词堆砌（「圆满成功」）、没有主语的句子。""",
    },
    {
        "name": "会议纪要",
        "category": "办公协同",
        "description": "从聊天记录或转写文本里提炼决议、待办、责任人，去掉寒暄和重复。",
        "content": """整理会议纪要时：
- 决议：会上真正拍板的事，每条带上是谁拍的。
- 待办：格式「谁 · 做什么 · 什么时候完成」。没有责任人的待办要显式标注「责任人待定」。
- 分歧：没有结论的争论也要记下来，注明各方立场，避免下次重复吵。
- 删掉：寒暄、重复表述、与议题无关的闲聊。
不确定的地方标注「（此处记录不清，需确认）」，不要自己补。""",
    },
    {
        "name": "代码审查",
        "category": "技术开发",
        "description": "按边界条件、错误处理、可维护性逐条审查，只报真问题并给出改法。",
        "content": """审查代码时按这个顺序看，只报真问题：
1. 正确性：边界（空、零、超大、并发）、off-by-one、类型混淆。
2. 错误处理：失败路径有没有被吞掉？异常信息能不能定位问题？
3. 资源：有没有忘记关闭/释放的东西。
4. 可维护性：命名是否说谎、函数是否太长、重复逻辑。
每条按「问题 — 为什么是问题 — 怎么改」写，给出具体改法而不是「建议优化」。
不要报格式和风格问题，除非它导致歧义。""",
    },
    {
        "name": "Git 提交规范",
        "category": "技术开发",
        "description": "把改动整理成 Conventional Commits，说清楚「为什么」而不是「改了什么」。",
        "content": """写提交信息时：
- 格式：<类型>: <一句话说明>，类型用 feat/fix/refactor/docs/test/chore。
- 正文说「为什么改」，不是「改了什么」——改了什么看 diff 就知道。
- 破坏性改动必须在正文写清楚影响范围和迁移方式。
- 一个提交只做一件事；如果改动包含多件事，先建议怎么拆。""",
    },
    {
        "name": "排障五步法",
        "category": "运维部署",
        "description": "现象 → 假设 → 验证 → 定位 → 修复，每步都要有证据，不许跳步猜。",
        "content": """排查故障时严格按五步走，每步都要落证据：
1. 现象：把观察到的现象写成可复现的一句话，写清环境。
2. 假设：列出所有可能原因，按可能性排序。
3. 验证：用最小成本的方式逐个排除，记下每条被排除的证据。没验证过的假设不算排除。
4. 定位：找到根因，说清楚为什么是它而不是别的。
5. 修复：给出改法，并说明怎么防止复发。
禁止在没有证据的情况下断言「应该就是 xxx 导致的」。""",
    },
    {
        "name": "日志分析",
        "category": "运维部署",
        "description": "从大段日志里定位根因：先找第一个错误，再看时间线，别被后面的连锁报错带偏。",
        "content": """分析日志时：
- 先找时间线上**第一个**异常，后面的报错往往是它的连锁反应。
- 按时间排序，关注突然出现的模式（某个字段变了、某条重试开始）。
- 统计错误分布（哪个错误最多、集中在哪个时间段），这比逐条读快得多。
- 结论必须落到具体行：引用原文片段作为证据，不要转述。""",
    },
    {
        "name": "SQL 优化",
        "category": "数据分析",
        "description": "看执行计划找瓶颈：索引、隐式转换、全表扫描，按影响排序给改法。",
        "content": """优化 SQL 时：
1. 先要执行计划，不要凭 SQL 长相猜。
2. 检查：字段上有没有函数或隐式类型转换（会让索引失效）、有没有 SELECT *、JOIN 字段类型是否一致。
3. 判断区分度：区分度低的字段建索引往往没用。
4. 按影响大小排序给建议，并说明每条预计能省多少扫描行数。
没有执行计划时，明确说明「需要执行计划才能判断」，不要瞎给建议。""",
    },
    {
        "name": "数据清洗",
        "category": "数据分析",
        "description": "处理缺失值、重复、异常值给出一份可复现的清洗清单，每步说明理由。",
        "content": """清洗数据时输出一份清单，每步都要说明理由和影响：
- 缺失：占比多少？删除还是填充？填充用什么策略、为什么。
- 重复：按什么键判定重复？保留哪一条、依据是什么。
- 异常值：用业务规则还是统计方法判定？删掉会不会损失真实的长尾样本。
- 每步都要给清洗前后的行数变化，让结果可复现、可回滚。""",
    },
    {
        "name": "中文润色",
        "category": "内容创作",
        "description": "去掉翻译腔和空话，把长句拆短，保留原意不添油加醋。",
        "content": """润色中文时：
- 删：翻译腔（「进行了一个…的操作」）、空话（「很大程度上」「一定程度上」）、过度副词。
- 拆：超过 40 字的句子拆短。
- 换：被动改主动；名词化动词改回动词（「做出决定」→「决定」）。
- 保留原意：不要为了好看而添加原文没有的信息。改动大的地方单独列出来说明原因。""",
    },
    {
        "name": "网页幻灯片",
        "category": "内容创作",
        "description": "生成单个 HTML 文件的演示稿，能直接双击打开，键盘翻页。",
        "content": """做网页幻灯片时：
- 输出**单个** HTML 文件，内联所有 CSS/JS，不引任何外部资源，双击就能打开。
- 16:9 版式，左右方向键翻页，底部显示页码。
- 每页只讲一件事，标题不超过一行，正文不超过 5 条。
- 配色克制：一个主色 + 中性灰，字号要够大（正文不小于 20px）。
完成后告诉使用者文件路径，并提醒按方向键翻页。""",
    },
    {
        "name": "文档摘要",
        "category": "办公协同",
        "description": "长文提炼成三层：一句话结论、要点清单、需要决策的问题。",
        "content": """给长文做摘要时输出三层：
1. 一句话结论：读完这篇最该知道的一件事。
2. 要点：5 条以内，每条不超过 25 字，按重要性排序。
3. 待决策：文中需要人来拍板的问题，以及各自的选项。
不要复述原文结构，不要写「本文介绍了…」这种开头。""",
    },
    {
        "name": "正则助手",
        "category": "技术开发",
        "description": "写正则并逐段解释，附上能匹配和不能匹配的测试用例。",
        "content": """写正则时：
- 给出正则本身，并逐段解释每个部分的含义。
- 必须给测试用例：3 个应该匹配的、3 个不应该匹配的（含边界，比如空串、超长、特殊字符）。
- 说明它做不到什么（比如正则不适合解析嵌套结构），避免被当成万能工具。
- 提醒使用者确认所用语言的正则方言差异（贪婪、回溯、Unicode 支持）。""",
    },
]


def builtin_skills() -> list[dict]:
    """内置技能 + 是否已安装。"""
    installed = {s.name for s in list_skills()}
    out = []
    for item in BUILTIN_SKILLS:
        row = dict(item)
        row["installed"] = row["name"] in installed
        out.append(row)
    return out


def install_builtin(name: str) -> bool:
    """把一条内置技能写进用户的技能库。

    装上去之后每次任务都会带上它——这才是「能安装、能使用」，
    不是列表里放一堆点不动的卡片。
    """
    for item in BUILTIN_SKILLS:
        if item["name"] == name:
            upsert_skill(item["name"], item["description"], item["content"])
            return True
    return False

# ---------- 第二批：对齐 AionClaw 的分类，用同样的用途 ----------
#
# AionClaw 的「招投标信息检索」「GEO/SEO 监控」「电商文案」这类技能背后是它自己的
# 服务端数据源，我们没法学，但**用途**可以等价实现：教智能体用公开检索、
# 结构化提问和固定输出格式把它做出来。能自己做的自己做，不依赖别人。

BUILTIN_SKILLS += [
    {
        "name": "公开信息检索",
        "category": "招投标",
        "description": "围绕一个主题做系统性检索：先立检索式，再多源交叉，最后给出带出处的结论。",
        "content": """做信息检索时：
1. 先写出检索式（关键词 + 同义词 + 排除词），说清楚为什么这么立。
2. 多源交叉：同一个结论至少两处独立来源，只有一处来源要显式标注「单一来源，待证」。
3. 每条结论都必须给出处（链接或文件名 + 位置），没出处的写成「未能证实」。
4. 输出表格：结论 / 出处 / 时间 / 可信度。
禁止把检索不到写成「不存在」。""",
    },
    {
        "name": "搜索可见性检查",
        "category": "招投标",
        "description": "检查一个品牌或关键词在搜索结果里的呈现：别人怎么描述你、负面信息在哪、怎么改。",
        "content": """做搜索可见性检查时：
1. 用品牌名、品牌名+评价、品牌名+问题 三组词分别检索。
2. 分类记录：官方来源、第三方媒体、用户讨论、负面信息。每类给出处。
3. 找差异：官方在说什么、用户在说什么，差距在哪。
4. 给建议：优先改哪三个地方（标题、首屏描述、结构化信息），每条说清楚为什么。
没有真实检索结果时，明确说明「需要实际检索数据」，不要编造。""",
    },
    {
        "name": "电商商品文案",
        "category": "电商",
        "description": "写主图卖点、详情页文案和售后问答，落到具体场景而不是形容词。",
        "content": """写商品文案时：
1. 卖点：先写「谁在什么场景下遇到什么问题」，再写这个商品怎么解决。最多 5 条。
2. 每条卖点配一个可验证的细节（材质、尺寸、实测数据），不要「高品质」「超耐用」。
3. 详情页：按「问题 → 方案 → 证据 → 放心买」的顺序组织。
4. 售后问答：把最常见的 5 个犹豫点写出来并正面回答，不回避短板。
不写绝对化用语（最、第一、100%），这是合规要求。""",
    },
    {
        "name": "小红书文案",
        "category": "自媒体营销",
        "description": "按小红书的语感写标题、正文和标签：口语化、有具体场景、不硬广。",
        "content": """写小红书文案时：
1. 标题：12-20 字，带具体场景或数字，不用「绝绝子」这类过时词。
2. 正文：第一句就要有信息量，别铺陈。分 3-5 段，每段一个点。
3. 用第一人称讲真实体验，包括一个不完美的地方（更可信）。
4. 标签 5-8 个：2 个大词 + 3 个精准词 + 1 个长尾词。
5. 结尾给一个具体问题引评论，不要「你学会了吗」。""",
    },
    {
        "name": "财报解读",
        "category": "财务股票",
        "description": "读财报先看三张表的勾稽关系和异常项，不预测股价。",
        "content": """解读财报时：
1. 先看三张表的互相印证：利润增长有没有现金流支撑？应收账款增速是否远超营收？
2. 找异常：毛利率突变、费用率异常、关联交易、非经常性损益占比。
3. 每个异常都要说明「可能的解释」和「需要进一步查什么」。
4. 只做事实与会计层面的分析。**不给买卖建议，不预测股价。**
数据来源不明时明确说明，绝不编造数字。""",
    },
    {
        "name": "论文精读",
        "category": "学术教育",
        "description": "把一篇论文拆成问题、方法、证据、局限四块，判断结论是否站得住。",
        "content": """精读一篇论文时按四块拆：
1. 问题：它到底想解决什么？之前的做法差在哪？
2. 方法：核心思路一句话说清；关键设计为什么这么做。
3. 证据：实验怎么设的、对照组是什么、指标是否支持结论、有没有挑数据。
4. 局限：作者自己承认的和没承认的。这个结论能推广到哪、不能推广到哪。
最后给一句判断：结论站得住吗？还差什么证据。""",
    },
    {
        "name": "文献综述",
        "category": "学术教育",
        "description": "把一个方向的多篇文献按方法流派归类，找出共识、分歧和空白。",
        "content": """写文献综述时：
1. 按方法流派归类，不要按时间流水账。
2. 每一类写：代表工作、核心思路、适用场景。
3. 明确列出：哪些结论是共识、哪些有分歧（双方证据各是什么）、哪里还是空白。
4. 引用要具体到「谁的哪篇工作提出了什么」，不写「有学者认为」。""",
    },
    {
        "name": "合同风险审阅",
        "category": "法律",
        "description": "逐条找出对你不利的条款：责任、付款、终止、争议解决，标出风险等级。",
        "content": """审阅合同时重点看这几类条款，按风险高/中/低标注：
1. 责任与赔偿：有没有无限责任、有没有赔偿上限、间接损失是否排除。
2. 付款：节点是否清晰、验收标准是否主观（「甲方满意为止」是高危）。
3. 知识产权：成果归谁、背景知识产权怎么界定。
4. 终止：什么条件下能退出、退出后已付款怎么算。
5. 争议解决：管辖地是否对你不利。
每条给出「原文位置 + 为什么有风险 + 建议怎么改」。
**说明：这是文本层面的风险提示，不构成法律意见，重大合同请找律师。**""",
    },
    {
        "name": "PPT 大纲",
        "category": "内容创作",
        "description": "先定结论和受众，再倒推页序；每页只讲一件事，标出配图和口径。",
        "content": """做 PPT 大纲时：
1. 先确定：给谁看、看完要他们做什么决定（这决定全部内容取舍）。
2. 结论先行：第 2 页就给出核心结论，后面每页都是支撑它的证据。
3. 每页写：标题（一句结论句，不是名词短语）、要点 3 条以内、配图建议、数据口径。
4. 删掉：议程页、谢谢页、与结论无关的背景。
输出成 Markdown 提纲，标明页数。""",
    },
    {
        "name": "信息抽取成表格",
        "category": "效率工具",
        "description": "从一堆杂乱文本里抽出结构化字段，输出可直接粘进表格的结果。",
        "content": """做信息抽取时：
1. 先定义字段（字段名、类型、是否必填、示例），让使用者确认再抽。
2. 抽不到就留空并标注「未提及」，**绝不猜测填充**。
3. 原文有歧义的地方把原文片段附在备注列。
4. 输出 Markdown 表格或 CSV，字段顺序固定，方便直接粘贴。
5. 最后报一句：共处理 N 条，完整 M 条，缺失字段主要是哪个。""",
    },
    {
        "name": "接口联调",
        "category": "技术开发",
        "description": "联调失败时按请求、响应、时延、鉴权四条线定位，给出可复现的 curl。",
        "content": """接口联调不通时按顺序排查：
1. 请求本身：URL、方法、Header（Content-Type 最容易错）、body 结构。
2. 鉴权：token 是否过期、放的位置对不对（Bearer 还是 query）、签名时间戳是否超时。
3. 响应：状态码 + 响应体一起看；4xx 是调用方问题，5xx 才怀疑服务端。
4. 网络与时延：超时设置、跨域、代理。
每步都给一条**可直接复制执行的 curl**，让使用者能自己验证。""",
    },
    {
        "name": "指标异动归因",
        "category": "数据分析",
        "description": "指标突然涨跌时，先拆维度再排口径，最后才谈原因。",
        "content": """分析指标异动时按顺序：
1. 先排口径：统计口径有没有变、埋点有没有发版、时区有没有错。**八成异动是口径问题。**
2. 拆维度：按渠道/地区/机型/新老用户拆，看是整体变化还是某一小撮带动。
3. 看时序：突变的拐点对应什么时间发生了什么（发版、活动、外部事件）。
4. 才谈原因，并给出可验证的下一步。
每个结论都要有数据支撑，写清楚「我是怎么算出来的」。""",
    },
    {
        "name": "变更前检查单",
        "category": "运维部署",
        "description": "上线或改配置前过一遍：影响面、回滚、验证、值班，缺一项都不上。",
        "content": """做变更前必须逐项确认并写明：
1. 影响面：改了什么、谁会受影响、影响是读还是写、有没有级联。
2. 回滚：怎么回、多久能回完、回滚会不会丢数据。**没有回滚方案不许动。**
3. 验证：改完用什么命令/指标确认成功，正常值是多少。
4. 时机：为什么现在做、避开什么时段。
5. 出事找谁：联系人和升级路径。
输出成一份可直接贴到变更单的清单。""",
    },
    {
        "name": "邮件撰写",
        "category": "办公协同",
        "description": "商务邮件写成三段：要什么、凭什么、下一步，让收件人三十秒能回。",
        "content": """写商务邮件时：
1. 主题行说清楚「什么事 + 需要对方做什么」，别用「关于…的说明」。
2. 第一段直接说要什么（结论先行），第二段给依据，第三段给明确的下一步和时限。
3. 需要对方做选择时，把选项写成 1/2/3，并标出你推荐哪个。
4. 全文控制在 200 字内。真正需要展开的细节放附件，正文只给摘要。
5. 语气：请求就写请求，不要用「麻烦您」「不胜感激」堆砌礼节。""",
    },
]

BUILTIN_SKILLS += [
    {
        "name": "短视频脚本",
        "category": "AI短剧",
        "description": "写 30-60 秒竖屏短视频的分镜脚本：钩子、转折、结尾，逐镜标注画面与台词。",
        "content": """写竖屏短视频脚本时：
1. 前 3 秒必须有钩子（冲突、反常识、直给结果），否则划走。
2. 结构：钩子 → 铺垫（一句）→ 转折 → 结论/行动，30-60 秒按 6-10 个镜头分。
3. 每个镜头写：时长、画面内容、台词/字幕、音效。
4. 台词口语化，一句不超过 15 字，避免书面语。
5. 结尾给一个具体的互动引导，不要「点赞关注」这种废话。""",
    },
    {
        "name": "通用办公助手",
        "category": "通用办公",
        "description": "处理日常杂事：整理表格、写通知、排日程、做对比清单，先问清楚再动手。",
        "content": """处理办公杂事时：
1. 先确认三件事：给谁用、什么格式、什么时候要。不清楚就先问，别猜。
2. 输出直接可用：表格给 Markdown 或 CSV，通知给能直接发的成稿，日程给时间块。
3. 需要取舍时给出推荐方案 + 一句理由，并说明代价。
4. 涉及人名、金额、日期这类关键信息，做完回读一遍确认没抄错。""",
    },
    {
        "name": "生活服务规划",
        "category": "生活服务",
        "description": "做行程、预算、清单类规划：按约束条件排，标出取舍和备选。",
        "content": """做生活类规划时：
1. 先把约束列清楚：时间、预算、人数、必须满足的条件。
2. 给一个主方案 + 一个备选方案，说明两者差别在哪。
3. 时间安排要留缓冲（转场、排队），别排得没有余量。
4. 预算给区间而不是单点，并标出哪几项最容易超。
5. 附一份可勾选的清单，按执行顺序排。""",
    },
    {
        "name": "商业运营分析",
        "category": "商业运营",
        "description": "拆解一个业务：收入怎么来的、成本卡在哪、增长杠杆是什么，先算账再给建议。",
        "content": """分析业务时按这个顺序：
1. 收入结构：谁付钱、为什么付、复购靠什么。
2. 单位经济：单个客户获取成本 vs 生命周期价值，算清楚再谈规模。
3. 成本：固定与变动分开，找出随规模增长会失控的那项。
4. 杠杆：列出 3 个能撬动结果的动作，按「见效速度 × 确定性」排序。
数字缺失时明确列出「需要哪些数据才能判断」，不要用行业均值硬套。""",
    },
    {
        "name": "信息资讯简报",
        "category": "信息资讯",
        "description": "把一天或一周的相关资讯压缩成简报：只留影响判断的那几条，标出处和时间。",
        "content": """做资讯简报时：
1. 先定范围：关注什么主题、排除什么噪音（广告、公关稿、旧闻重发）。
2. 每条写：发生了什么（一句话）、为什么重要、影响谁、出处与时间。
3. 只留真正会改变判断的，宁少勿多；同一件事多家报道只留一条最权威的。
4. 最后给一句整体判断：这段时间的主要变化趋势是什么。
没有可靠出处的一律不进简报。""",
    },
    {
        "name": "翻译润色",
        "category": "通用办公",
        "description": "中英互译保留原意与语气，专业术语统一，长句按目标语言习惯重组。",
        "content": """翻译时：
1. 先确认用途（合同/邮件/宣传/技术文档）和读者，这决定语气与术语选择。
2. 术语全文统一，第一次出现时用「中文（English）」标注。
3. 不逐字直译：按目标语言的语序重组长句，但不得增删信息。
4. 原文有歧义时，给出两种译法并说明差别，不要擅自选一个。
5. 数字、单位、日期格式按目标语言习惯转换，并单独列出来复核。""",
    },
]
