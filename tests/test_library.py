"""技能库与自定义智能体的测试。

这两块的存储是「用户自己写的东西」，写坏了比读坏了严重得多——
所以重点测：同名覆盖不重复、删除、以及技能进系统提示词时的截断。
"""

from __future__ import annotations

import json

import pytest

from jancode_agent import library


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """把库指到临时目录。绝不碰用户真实的 ~/.jancode-agent。"""
    monkeypatch.setattr(library, "SKILLS_PATH", tmp_path / "skills.json")
    monkeypatch.setattr(library, "AGENTS_PATH", tmp_path / "agents.json")


def test_新建技能后能读回来():
    library.upsert_skill("发版流程", "打 tag 前要做什么", "1. 跑测试\n2. 改版本号")
    skills = library.list_skills()
    assert [s.name for s in skills] == ["发版流程"]
    assert skills[0].description == "打 tag 前要做什么"
    assert "跑测试" in skills[0].content


def test_同名的技能是覆盖不是新增():
    # 智能体自己会调 save_skill。如果同名就追加，跑几次任务之后
    # 技能库会被同一件事的十几种说法塞满，每轮请求都要带着它们。
    library.upsert_skill("发版流程", "第一版", "旧做法")
    library.upsert_skill("发版流程", "第二版", "新做法")
    skills = library.list_skills()
    assert len(skills) == 1
    assert skills[0].content == "新做法"


def test_删除技能():
    library.upsert_skill("临时", "", "内容")
    assert library.delete_skill("临时") is True
    assert library.list_skills() == []
    assert library.delete_skill("不存在") is False


def test_损坏的技能文件不会让程序崩掉():
    # 用户手改过这个文件、或者写到一半断电，都可能留下坏 JSON。
    # 这时候宁可当作「还没有技能」，也不能让整个界面起不来。
    library.SKILLS_PATH.write_text("{ 这不是 json", encoding="utf-8")
    assert library.list_skills() == []


def test_技能会进系统提示词():
    library.upsert_skill("发版流程", "打 tag 前要做什么", "先跑一遍测试")
    section = library.skills_section()
    assert "发版流程" in section
    assert "先跑一遍测试" in section


def test_没有技能时提示词里不多一段空话():
    assert library.skills_section() == ""


def test_技能太多时截断并说明(monkeypatch):
    monkeypatch.setattr(library, "MAX_SKILL_CHARS", 200)
    for i in range(20):
        library.upsert_skill(f"技能{i}", "", "内容" * 30)
    section = library.skills_section()
    assert "未加载" in section
    assert len(section) < 900


def test_自定义智能体能存也能删():
    library.upsert_agent("前端重构手", "只改前端，不碰后端", "gpt-5.4", ["发版流程"])
    agents = library.list_agents()
    assert [a.name for a in agents] == ["前端重构手"]
    assert agents[0].model == "gpt-5.4"
    assert agents[0].skills == ["发版流程"]
    assert library.delete_agent("前端重构手") is True
    assert library.list_agents() == []


def test_智能体的角色设定会进提示词():
    library.upsert_skill("发版流程", "", "先跑测试")
    library.upsert_agent("前端重构手", "只改前端", "gpt-5.4", ["发版流程"])
    persona = library.find_agent("前端重构手")
    assert persona is not None
    text = library.persona_prompt(persona)
    assert "只改前端" in text
    assert "先跑测试" in text


def test_智能体只带自己勾选的技能():
    library.upsert_skill("甲", "", "甲的做法")
    library.upsert_skill("乙", "", "乙的做法")
    library.upsert_agent("只带甲", "", "", ["甲"])
    persona = library.find_agent("只带甲")
    text = library.persona_prompt(persona)
    assert "甲的做法" in text
    assert "乙的做法" not in text


def test_存盘格式是能读的json():
    library.upsert_skill("甲", "说明", "内容")
    data = json.loads(library.SKILLS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data[0]["name"] == "甲"
