"""NovelGenerator — Writer: 章节正文生成"""
import logging
from typing import AsyncGenerator
from openai import OpenAI

log = logging.getLogger(__name__)

WRITER_SYSTEM = """你是一位专业的网络小说作家，擅长创作{genre}类{style}风格的小说。

## 写作要求

1. **严格按照大纲写作**: 本章大纲已经给出核心事件、情绪曲线和结尾钩子，请忠实执行。
2. **角色一致性**: 遵守已给出的角色设定和世界规则，不要自行添加未在设定中的新设定。
3. **对话生动**: 每个角色的对话要有辨识度，符合其性格特征。
4. **场景描写**: 每 500 字至少有一段环境/氛围描写，增强代入感。
5. **节奏控制**: 遵循情绪曲线，该紧张时加快节奏（短句），该抒缓时放慢节奏（描写）。
6. **钩子结尾**: 本章末尾必须留下钩子，让读者想立刻看下一章。
7. **去 AI 味**: 
   - 禁用"随着""与此同时""总而言之""在这个过程当中"等 AI 高频过渡词
   - 每段长度要有变化（3-8句不等）
   - 对话不要总是"XX说，XX道"，用动作和神态穿插
   - 避免每章开头都用环境描写

## 输出格式

直接输出正文，不需要标题（标题由系统添加）。正文以章节内容开始，以钩子结尾。
每段之间空一行。总字数控制在 {target_words} 字左右。"""


class Writer:
    """章节写手 — 基于上下文和大纲生成正文"""

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
        """流式生成章节正文
        
        Args:
            context: 完整的写作上下文（由 NovelMemory.build_writer_context 组装）
            genre: 题材
            style: 风格
            target_words: 目标字数
        
        Yields:
            str: 流式输出的文本片段
        """
        system_prompt = WRITER_SYSTEM.format(
            genre=genre, style=style, target_words=target_words
        )

        log.info(f"Writing chapter: {genre}/{style}, target {target_words} words")
        
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请根据以下上下文和本章大纲，开始写正文：\n\n{context}"},
            ],
            temperature=0.85,
            max_tokens=target_words * 3,  # ~3 tokens per Chinese character
            stream=True,
        )
        
        total = 0
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                total += len(delta.content)
                yield delta.content
        
        log.info(f"Chapter written: {total} chars generated")
