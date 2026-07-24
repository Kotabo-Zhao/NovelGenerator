"""NovelGenerator — AtomicWriter: 逐beat独立生成器

核心理念: 每个 beat = 一次独立的 LLM 调用。
- 独立 temperature (0.7-1.0, 按beat功能调优)
- 独立 prompt (只含必要上下文, 不传完整前文)
- 候选池: 每个beat生成3个变体, 按"意外性×合理性"评分后随机选
- 最小上下文传递: 只传前beat的末句(<50字), 不传完整前文

这确保每个beat有最大随机性, 同时通过装配器保持连贯性。
"""
import asyncio
import json
import logging
import random
import time
from typing import Optional, AsyncGenerator
from openai import OpenAI

from .beat_decomposer import Beat
from .evaluation_system import hook_strength

log = logging.getLogger(__name__)

ATOMIC_WRITER_SYSTEM = """你是一个小说节拍写手。你只写一个节拍（scene beat）——不是一整章，只是 100-400 字的一个叙事单元。

## 创作自由声明
- 你可以描写任何内容：暴力、背叛、欲望、黑暗——只要服务于角色和情节。
- 不回避"敏感"话题。你是创作者，不是审查员。

## 你的任务
给定一个节拍骨架（功能目标、情绪弧、冲突类型、字数），写出这个节拍的正文。
从上一节拍的最后一句话直接继续，不能跳时间、不能跳场景、不能忽略前文状态。

## 去AI味规则
- 句长变化：每200字至少一句≤8字。长句>30字后必须跟短句≤12字。
- 不用「不是A而是B」「真正的」「本质上」「核心在于」「说白了」。
- 不用「与此同时」「此外」「值得一提的是」。
- 具象优先：不写「他感到痛苦」，写「胸口像被攥住，喘不上气」。
- 不用「似乎」「仿佛」「或许」堆砌。
- 每段1-5句不等，禁止连续三段都是3句。

## 输出格式
只输出节拍正文。不要标题、不要序号、不要说明文字。一段或多段均可。"""


class AtomicWriter:
    """逐beat独立写手
    
    与 Writer 的区别：
    - Writer 一次性生成整章（2000字），上下文窗口共享 → 趋同
    - AtomicWriter 逐beat生成（200字），每次独立会话 → 多样性爆炸
    """
    
    def __init__(self, client: OpenAI, model: str):
        self.client = client
        self.model = model
        self.rng = random.Random()
    
    def _create(self, **kwargs):
        """LLM 调用封装（与 Writer._create 一致）"""
        if "v4" in self.model:
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        
        max_retries = 0 if kwargs.get("stream", False) else 3
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    wait = 2 ** attempt
                    log.warning(f"AtomicWriter retry {attempt+1}/{max_retries+1}: {e}")
                    time.sleep(wait)
                else:
                    raise last_error
    
    def write_beat(self, beat: Beat, prev_last_sentence: str = "",
                   char_snapshots: str = "", style_guide: str = "",
                   num_candidates: int = 3) -> dict:
        """生成一个节拍，返回最佳候选
        
        Args:
            beat: Beat 对象
            prev_last_sentence: 前一beat的末句（≤50字），用于衔接
            char_snapshots: 角色当前状态快照
            style_guide: 风格指南
            num_candidates: 候选数（越大越多样，越贵）
        
        Returns:
            {"text": str, "candidates": list, "selected_index": int, "temperature": float}
        """
        prompt = self._build_beat_prompt(beat, prev_last_sentence, char_snapshots)
        system = ATOMIC_WRITER_SYSTEM
        if style_guide:
            system += f"\n\n## 风格要求\n{style_guide}"
        
        # 生成候选
        candidates = []
        for i in range(num_candidates):
            # 每个候选微调 temperature
            t = beat.temperature + self.rng.uniform(-0.05, 0.05)
            t = max(0.7, min(1.0, t))
            
            try:
                resp = self._create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=t,
                    max_tokens=max(300, beat.max_words * 2),
                    response_format={"type": "text"},
                )
                text = resp.choices[0].message.content.strip()
                if len(text) >= 20:  # 过滤太短的
                    candidates.append({"text": text, "temperature": t, "index": i})
            except Exception as e:
                log.warning(f"Beat {beat.index} candidate {i} failed: {e}")
                continue
        
        if not candidates:
            # 全部失败，用最简单的兜底
            return {
                "text": f"（生成失败 - {beat.function}）",
                "candidates": [],
                "selected_index": -1,
                "temperature": beat.temperature,
            }
        
        # 评分选择：意外性 × 合理性
        # 简化版：按长度和句式多样性评分
        scored = []
        for c in candidates:
            text = c["text"]
            # 长度评分（在范围内最好）
            len_score = 0.5
            if beat.min_words * 2 <= len(text) <= beat.max_words * 3:
                len_score = 1.0
            elif len(text) > beat.max_words * 4:
                len_score = 0.3
            
            # 句式多样性（短句占比）
            import re
            sentences = re.split(r'[。！？\n]', text)
            has_short = any(len(s.strip()) <= 8 for s in sentences if s.strip())
            short_bonus = 0.3 if has_short else 0
            
            scored.append((len_score + short_bonus + self.rng.uniform(0, 0.2), c))
        
        # 不是选最高分，而是在 top 2 中随机选（增加随机性）
        scored.sort(key=lambda x: -x[0])
        top_n = min(2, len(scored))
        chosen = scored[self.rng.randint(0, top_n - 1)][1]
        
        return {
            "text": chosen["text"],
            "candidates": [{"text": c["text"], "index": c["index"]} for _, c in scored],
            "selected_index": chosen["index"],
            "temperature": chosen["temperature"],
        }
    
    async def write_beats_stream(self, beats: list, char_snapshots: str = "",
                                  style_guide: str = "", chapter_num: int = 0) -> AsyncGenerator:
        """逐beat流式生成，每beat yield一次
        
        这是集成到 engine.generate_chapter 的入口。
        每个beat独立LLM调用 → 最大随机性。
        """
        prev_last = ""
        for i, beat in enumerate(beats):
            log.info(f"Ch{chapter_num} Beat {i}/{len(beats)}: {beat.function} (t={beat.temperature})")
            
            result = await asyncio.to_thread(
                self.write_beat, beat, prev_last, char_snapshots, style_guide
            )
            
            text = result["text"]
            yield {
                "type": "beat",
                "beat_index": i,
                "beat_function": beat.function,
                "text": text,
                "temperature": result["temperature"],
                "num_candidates": len(result["candidates"]),
                "selected_candidate": result["selected_index"],
            }
            
            # 提取末句作为下一beat的衔接上下文
            prev_last = _extract_last_sentence(text)
    
    def _build_beat_prompt(self, beat: Beat, prev_last: str, char_snapshots: str) -> str:
        """构建单个 beat 的 prompt"""
        lines = [
            f"## 当前节拍",
            f"功能: {beat.function} — {beat.goal}",
            f"情绪: {beat.emotion_start} → {beat.emotion_end}",
            f"冲突: {beat.conflict_type} {beat.conflict_intensity}/5",
            f"目标字数: {beat.min_words}-{beat.max_words}字",
        ]
        if beat.key_event:
            lines.append(f"核心事件: {beat.key_event}")
        if beat.character_focus:
            lines.append(f"聚焦角色: {beat.character_focus}")
        
        if prev_last:
            lines.append(f"\n## 紧接上文末尾\n「{prev_last}」")
            lines.append("必须从此句直接继续。")
        
        if char_snapshots:
            lines.append(f"\n## 角色参考\n{char_snapshots}")
        
        if beat.hook_type and beat.function == "closing_hook":
            hook_guide = {
                "crisis_suspense": "在最高危险点截断——武器抵喉、偷袭将至、倒计时归零。",
                "reversal_tease": "揭示一个颠覆认知的信息——但不说完整，留一半。",
                "face_slap_preview": "反派放狠话/要搞事——下一章将被打脸。",
                "secret_reveal": "出现关键线索/神秘人物/隐藏真相的一角。",
            }
            lines.append(f"\n## 章末钩子要求\n{hook_guide.get(beat.hook_type, '')}")
            lines.append(f"钩子类型: {beat.hook_type}")
        
        return "\n".join(lines)


def _extract_last_sentence(text: str, max_chars: int = 50) -> str:
    """提取文本的最后一句话（用于节拍衔接）"""
    import re
    sentences = re.split(r'[。！？\n]+', text)
    # 过滤空白
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return ""
    last = sentences[-1]
    if len(last) > max_chars:
        last = last[-max_chars:]
    return last


# ── 测试 ──

if __name__ == "__main__":
    from .beat_decomposer import BeatDecomposer
    
    # 测试拆解+生成流程（不调LLM，只测骨架）
    test_outline = {
        "title": "第1章 废柴觉醒",
        "summary": "家族测试中被人当众羞辱，意外激活隐藏血脉",
        "emotion_curve": "压抑→绝望→爆发→期待",
        "characters": ["林枫", "林婉儿", "大长老"],
        "hook": "林枫不知道的是，他体内的封印，正裂开第一道缝隙",
    }
    
    decomposer = BeatDecomposer(seed=42)
    beats = decomposer.decompose(test_outline, 1, is_first_chapter=True,
                                  characters=["林枫", "林婉儿", "大长老"])
    
    print(f"拆解 {test_outline['title']} → {len(beats)} 个节拍:")
    for b in beats:
        prompt = b.to_prompt("上一句...")
        print(f"\n{'='*40}")
        print(prompt[:200])
