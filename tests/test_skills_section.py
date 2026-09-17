# -*- coding: utf-8 -*-
"""skills_section 截断行为：超限不加载，且提示语不能指向不存在的工具。

以前截断提示让模型「用 list_skills 查看全部」——工具列表里没有
list_skills，模型会照做然后空试一轮。
"""

from jancode_agent.library import MAX_SKILL_CHARS, Skill, skills_section


def test_技能截断时不指向不存在的工具():
    big = Skill("大技能", "", "很长的做法" * 3000)
    assert len(big.content) * 2 > MAX_SKILL_CHARS  # 前提：真会超限
    text = skills_section([big, big])
    assert "未加载" in text
    assert "list_skills" not in text


def test_技能不超限时全部加载():
    small = Skill("小技能", "说明", "做法内容")
    text = skills_section([small])
    assert "小技能" in text and "做法内容" in text
    assert "未加载" not in text