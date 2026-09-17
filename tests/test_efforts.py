# -*- coding: utf-8 -*-
"""推理档位：界面固定四档，发送前按模型能力收敛。

要锁住的行为：
1. 默认（空/default）永远不带字段——这是任何模型都安全的档。
2. 只支持开/关的模型（deepseek-v4、glm-5 系）选什么都收敛成 high。
3. OpenAI 系的 max 收敛到 high（它们的最高档就是 high）。
4. 完全未知的模型：低/高原样发，max 收敛到 high。
5. 界面固定四档 CHOICES 不随模型变化。
"""
from jancode_agent.efforts import CHOICES, effective_effort, label, options_for


def test_choices_fixed():
    assert CHOICES == ["default", "low", "high", "max"]


def test_default_never_sends_field():
    for model in ("deepseek-v4-pro", "gpt-5", "glm-5.3-flash", "", "unknown-model"):
        assert effective_effort(model, "") == ""
        assert effective_effort(model, "default") == ""


def test_onoff_models_clamp_to_high():
    # deepseek-v4 与 glm-5 系只支持开/关：任何非默认档都等于「开」
    for model in ("deepseek-v4-pro", "deepseek-r1", "glm-5.3-flash", "glm-z1"):
        for want in ("low", "high", "max"):
            assert effective_effort(model, want) == "high", (model, want)
        assert "high" in options_for(model)


def test_openai_max_clamps_to_high():
    assert effective_effort("gpt-5", "max") == "high"
    assert effective_effort("o3", "max") == "high"
    # 支持列表内的档位原样通过
    assert effective_effort("gpt-5", "low") == "low"
    assert effective_effort("gpt-5", "high") == "high"


def test_unknown_model_passthrough_with_max_clamp():
    assert effective_effort("some-new-model", "low") == "low"
    assert effective_effort("some-new-model", "high") == "high"
    assert effective_effort("some-new-model", "max") == "high"
    assert options_for("some-new-model") == []


def test_labels_cover_choices():
    names = [label(c) for c in CHOICES]
    assert names == ["默认", "低", "高", "最大"]


def test_provider_payload_uses_clamped_effort():
    """payload 构建要用收敛后的值：只支持开/关的模型选 low 也发 high。"""
    from dataclasses import replace
    from jancode_agent.config import ProviderConfig
    from jancode_agent.providers import Client as OpenAICompatProvider

    cfg = ProviderConfig(
        name="t", base_url="https://x/v1", api_key="k",
        model="deepseek-v4-pro", effort="low",
    )
    p = OpenAICompatProvider(cfg)
    payload = p._chat_payload([], None)
    assert payload["reasoning_effort"] == "high"
    # 默认档：字段整个不出现
    cfg2 = replace(cfg, effort="")
    p2 = OpenAICompatProvider(cfg2)
    payload2 = p2._chat_payload([], None)
    assert "reasoning_effort" not in payload2
