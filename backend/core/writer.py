"""NovelGenerator — Writer: 章节正文生成（两遍式风格迁移）"""
import logging
from typing import AsyncGenerator
from openai import OpenAI
from .styles import get_style, build_style_prompt, build_custom_style

log = logging.getLogger(__name__)

WRITER_SYSTEM = """你是一位专业的网络小说作家。

## 你的写作身份

{style_guide}

## 核心写作要求

1. **绝对忠于风格**: 上述文笔特征、语气基调、对话风格是你必须严格遵守的准则。
2. **少样本参考**: 如果提供了风格示例，请模仿其句式节奏、意象选择、情感张力。
3. **标志句式**: 适当使用上述标志性句式/词汇，但不要堆砌。
4. **严禁写法**: 上述禁止列表中的写法一律不得出现。
5. **去 AI 味**:
   - 禁用所有 AI 高频过渡词（随着/与此同时/总而言之/在这个过程中/此外/值得一提的是）
   - 每段长度有变化（2-6句不等），避免齐整的段落
   - 对话不用「XX说」「XX道」每句都标注，用动作和神态穿插
   - 每章开头不要总是环境描写
   - 不要在所有段落结尾加感叹号
6. **角色一致性**: 遵守角色设定和世界规则。
7. **钩子结尾**: 本章末尾必须留下钩子。

## 输出格式

直接输出正文，不需要标题。每段之间空一行。总字数控制在 {target_words} 字左右。"""


STYLE_POLISH_SYSTEM = """你是一位专业的文字编辑，专精于将文字打磨成特定作家的风格。

## 目标风格

{style_guide}

## 打磨要求

你需要将以下草稿进行风格打磨。注意:
1. **不必重写全文**——保留原稿的核心情节和对话内容
2. **修正文笔**——将不匹配的句式替换为目标风格的句式
3. **注入风格标志**——适当加入目标风格的标志性写法（但不能生硬）
4. **去掉违和感**——移除与目标风格冲突的用词和表述
5. **保持字数**——打磨后的字数应与原稿相近（±10%）

## 输出格式

直接输出打磨后的正文，不需要标题和说明。每段之间空一行。"""


class Writer:
    """章节写手 — 两遍生成: 初稿 + 风格打磨"""

    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model

    async def write_stream(
        self,
        context: str,
        genre: str = "玄幻",
        style: str = "热血爽文",
        target_words: int = 3000,
    ) -> AsyncGenerator[str, None]:
        """流式生成章节正文（两遍式: 初稿 + 风格打磨）
        
        Yields:
            str: 流式输出的文本片段。第一阶段 yield 初稿，第二阶段 yield 打磨后的最终版。
        """
        # 解析风格
        if style in ("自定义风格",) or style.startswith("自定义"):
            style_config = build_custom_style(style)
        else:
            style_config = get_style(style)
        
        style_prompt = build_style_prompt(style_config)

        # ── 第一遍: 生成初稿 ──
        system_prompt = WRITER_SYSTEM.format(
            style_guide=style_prompt,
            target_words=target_words,
        )

        log.info(f"Writing chapter: {genre}/{style}, pass 1/2 (draft)")
        
        draft = ""
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请根据以下上下文和本章大纲，开始写正文：\n\n{context}"},
            ],
            temperature=0.85,
            max_tokens=target_words * 3,
            stream=True,
        )
        
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                draft += delta.content
                yield delta.content  # 流式输出初稿
        
        log.info(f"Draft done: {len(draft)} chars")

        # ── 第二遍: 风格打磨 ──
        # 只对较长内容做打磨（<500字跳过，自定义风格跳过）
        if len(draft) < 500 or style_config.get("is_custom"):
            log.info("Skipping polish pass (too short or custom style)")
            return

        log.info(f"Pass 2/2: style polish")
        
        polish_prompt = STYLE_POLISH_SYSTEM.format(style_guide=style_prompt)
        
        polish_stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": polish_prompt},
                {"role": "user", "content": f"草稿如下：\n\n{draft}"},
            ],
            temperature=0.6,  # 低温保证一致性
            max_tokens=target_words * 3,
            stream=True,
        )
        
        yield "\n\n"  # 分隔符（前端可以忽略）
        
        polished = ""
        for chunk in polish_stream:
            delta = chunk.choices[0].delta
            if delta.content:
                polished += delta.content
        
        log.info(f"Polish done: {len(polished)} chars")
        
        # 产出打磨后的最终版
        if polished and len(polished) > len(draft) * 0.5:
            yield polished
            log.info(f"Final output: polished version ({len(polished)} chars)")
        else:
            log.warning(f"Polish result too short ({len(polished)} chars), using draft")
