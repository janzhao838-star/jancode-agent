"""内置 Agent 预设。

用户拿到软件第一眼不该面对一张白纸：预设给四种现成的工作模式，
一键「以此为准」直接用，「复制自定义」复制一份到自己的智能体列表里改。
预设本身随代码分发，用户改不到也不该改——要改就复制一份。
"""

from __future__ import annotations


BUILTIN_PRESETS = [
    {
        "name": "标准模式",
        "emoji": "◆",
        "description": "默认行为：读文件、跑命令、改代码、跑测试，全程可见。",
        "system_prompt": "",
        "mode": "auto",
    },
    {
        "name": "计划模式",
        "emoji": "◇",
        "description": "先给方案再动手：只调研只出计划，不写文件不跑命令。适合大改动前先看思路。",
        "system_prompt": "先给出完整计划再等确认：列出要改哪些文件、每处怎么改、风险在哪。计划阶段不执行写操作。",
        "mode": "plan",
    },
    {
        "name": "极简模式",
        "emoji": "·",
        "description": "只给答案不啰嗦：不解释、不总结，代码给全、说明给短。",
        "system_prompt": "回答尽量短：代码给完整可运行的，解释一两句就够。不重复需求，不复述代码，不输出礼貌用语。",
        "mode": "auto",
    },
    {
        "name": "创造模式",
        "emoji": "✦",
        "description": "鼓励尝试：主动给多种方案和取舍，适合探索性任务和原型。",
        "system_prompt": "主动提出多种可行方案并说明取舍（速度、可读性、扩展性），可以给出超出字面要求的改进建议，但每条要标注是建议还是已实现。",
        "mode": "auto",
    },
]


def find_preset(name):
    """按名字找内置预设。找不到返回 None。"""
    for preset in BUILTIN_PRESETS:
        if preset["name"] == name:
            return preset
    return None
