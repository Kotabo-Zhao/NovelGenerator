"""NovelGenerator — Embellisher Agent: 专属润色师

职责: 接收 Writer 的初稿，执行风格打磨 + Humanizer 去AI味
独立 Agent 设计，可单独调参和替换
"""

import logging
from openai import OpenAI

log = logging.getLogger(__name__)

EMBELLISHER_SYSTEM = """你是一位资深文学编辑，专精于将初稿打磨成出版级文字。

## 打磨维度

1. **句式优化**: 长短句交替，去掉冗长从句，每段2-6句不等
2. **节奏调整**: 紧张处短句加快，抒情处长句放缓
3. **去AI味**: 移除机械痕迹——禁用{anti_patterns}
4. **风格注入**: 按照目标风格的特征润色文字
5. **感官增强**: 增加视觉/听觉/触觉/嗅觉的具体描写
6. **对话打磨**: 让对话更有角色辨识度，减少「XX说」标注

## 目标风格

{style_guide}

## 输出

直接输出打磨后的正文，不要标注改了哪里。"""


class Embellisher:
    """润色师 Agent — 独立于 Writer，负责文字打磨"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    def polish(self, draft: str, style_config: dict, target_words: int) -> str:
        """对初稿进行风格打磨
        
        Args:
            draft: 初稿文本
            style_config: 风格配置 (从 styles.py)
            target_words: 目标字数
        Returns:
            打磨后的文本
        """
        if len(draft) < 500:
            return draft

        anti = style_config.get("anti_patterns", "AI高频过渡词、机械句式、每段齐整长度")
        style_guide = f"作者: {style_config.get('author','')}\n文笔: {style_config.get('prose','')}\n语气: {style_config.get('tone','')}\n节奏: {style_config.get('pacing','')}"

        system_prompt = EMBELLISHER_SYSTEM.format(
            anti_patterns=anti,
            style_guide=style_guide,
        )

        log.info(f"Embellisher polishing: {len(draft)} chars")
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请打磨以下初稿（目标{target_words}字）：\n\n{draft}"},
            ],
            temperature=0.55,
            max_tokens=target_words * 3,
        )
        
        polished = response.choices[0].message.content or draft
        log.info(f"Embellisher done: {len(polished)} chars")
        return polished if len(polished) > len(draft) * 0.5 else draft
