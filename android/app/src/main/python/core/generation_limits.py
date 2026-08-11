"""生成请求的资源上限工具。

这个模块保持纯函数和零外部依赖，方便在后端和最小测试环境中复用。
"""
from __future__ import annotations


DEFAULT_MIN_GENERATION_TOKENS = 256


def max_generation_tokens(
    target_words: int,
    multiplier: int = 3,
    hard_cap: int = 4000,
    minimum: int = DEFAULT_MIN_GENERATION_TOKENS,
) -> int:
    """根据目标字数计算 max_tokens，并强制执行硬上限。

    `multiplier` 只用于估算，不允许通过续写/重试路径绕过 `hard_cap`。
    所有参数非法时直接失败，避免静默生成超大请求。
    """
    if not isinstance(target_words, int) or target_words <= 0:
        raise ValueError("target_words 必须是正整数")
    if not isinstance(multiplier, int) or multiplier <= 0:
        raise ValueError("multiplier 必须是正整数")
    if not isinstance(hard_cap, int) or hard_cap <= 0:
        raise ValueError("hard_cap 必须是正整数")
    if not isinstance(minimum, int) or minimum <= 0:
        raise ValueError("minimum 必须是正整数")
    if minimum > hard_cap:
        raise ValueError("minimum 不能大于 hard_cap")

    return min(max(minimum, target_words * multiplier), hard_cap)
