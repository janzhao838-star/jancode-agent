"""推理强度档位：按模型自己支持的能力给选项。

为什么不能统一给一套：reasoning_effort 不是所有模型都认。
给不支持的模型硬塞这个字段，provider 会直接返回 400，整个请求失败。
所以默认是「不给」（空列表 = 不显示这个选择器、请求里也不带该字段），
只有确认支持的模型家族才列档位。

中转站的 /v1/models 通常只返回模型名，没有能力元数据（实测确实如此），
所以这里用公开的模型家族规则判断；将来若某个 provider 提供了能力字段，
应该优先用它，这里就是兜底层。
"""

from __future__ import annotations

import re

# 各家推理模型支持的三档
_THREE = ["low", "medium", "high"]
# 只支持「开/关思考」的，映射成高/默认
_ONE = ["high"]


def options_for(model: str) -> list[str]:
    """这个模型支持的推理强度档位。空列表表示不支持调节。"""
    name = (model or "").strip().lower()
    if not name:
        return []

    # OpenAI 推理系列：gpt-5 及以后的/o1/o3/o4/codex 都支持 low/medium/high
    if re.match(r"^(o1|o3|o4|gpt-[5-9]|codex)", name):
        return list(_THREE)

    # Anthropic 的思考档位
    if name.startswith("claude") and ("thinking" in name or "4-" in name or "5-" in name):
        return list(_THREE)

    # DeepSeek：只有 reasoner 类带思考，普通对话模型不收这个字段
    if "deepseek-reasoner" in name or "deepseek-r" in name or "deepseek-v4" in name:
        return list(_ONE)

    # 通义：带 thinking 的（qwq、qwen3-*-thinking）
    if "qwq" in name or ("qwen" in name and "thinking" in name):
        return list(_ONE)

    # 智谱：z1 系列是推理模型
    if name.startswith("glm-z") or "glm-4.5" == name:
        return list(_ONE)

    # Kimi：k2 thinking（kimi-k2-0905-preview 属于推理型）
    if "kimi-k2" in name or ("kimi" in name and "thinking" in name) or "k1.5" in name:
        return list(_ONE)

    # 阶跃：step-2 支持思考开关
    if name.startswith("step-2"):
        return list(_ONE)

    # 其余（qwen-plus、glm-4-plus、moonshot-v1、doubao、ernie、hunyuan、
    # minimax、baichuan、yi、本地 ollama 等）：不支持，就不给这个选项
    return []


def label(level: str) -> str:
    return {"low": "快速", "medium": "均衡", "high": "深度思考"}.get(level, level)
